"""PLUGINS' I/O half: everything that touches a real file.

`docs/tasks/D2-manifest-validation/spec.md`. ``plugins.py`` stays pure
data and pure parsing; this module is where a path actually gets read,
per CLAUDE.md's rule that disk-touching code is its own file, separate
from a block's pure module.
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import tomllib
from collections.abc import Callable
from pathlib import Path
from types import ModuleType

import jsonschema

from sadana import plugins


def load_skill(ref: plugins.SkillRef) -> str:
    """Read ``ref``'s SKILL.md, validate its frontmatter, and return the
    body — the text a child's system prompt is built from. Raises
    ``plugins.SkillLoadError`` when the file is absent, isn't valid UTF-8,
    its frontmatter is missing/malformed, its declared ``name`` doesn't
    match ``ref.skill``, or its ``description`` is absent or over 1024
    characters."""
    path = plugins._skill_path(ref) / "SKILL.md"
    if not path.exists():
        raise plugins.SkillLoadError(f"no SKILL.md at {path}")
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError as e:
        raise plugins.SkillLoadError(f"SKILL.md at {path} is not valid UTF-8: {e}") from e
    return plugins._validate_skill_text(text, path, ref.skill)


# ── D2: manifest validation ──────────────────────────────────────────────
# Its full contract is `docs/tasks/D2-manifest-validation/spec.md`.


def _check_schema(plugin_dir: Path, entry: plugins.Entry) -> plugins.InvalidSchema | None:
    path = plugin_dir / entry.parameters
    if not path.exists():
        return plugins.InvalidSchema(entry=entry.tool, path=str(path), detail="schema file not found")
    try:
        schema = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        return plugins.InvalidSchema(entry=entry.tool, path=str(path), detail=f"could not read as JSON: {e}")
    if not isinstance(schema, dict):
        return plugins.InvalidSchema(entry=entry.tool, path=str(path), detail="schema must be a JSON object")
    try:
        jsonschema.validators.validator_for(schema).check_schema(schema)
    except jsonschema.exceptions.SchemaError as e:
        return plugins.InvalidSchema(entry=entry.tool, path=str(path), detail=str(e))
    return None


def _check_skill(plugin_dir: Path, node: plugins.Node) -> plugins.UnresolvedSkill | None:
    """Resolved against ``plugin_dir`` itself (`plugin_blueprint.md §5.1`'s
    ``skills/`` sits beside ``plugin.toml``), never through
    ``plugins._plugins_root()`` — that resolves the plugin *currently
    installed* under ``SADANA_PLUGINS_DIR``, which ``plugin_dir`` need not
    be (and, in a test, usually isn't). ``validate()`` takes one ``Path``
    and must answer only for that directory's own contents."""
    if node.skill is None:
        return plugins.UnresolvedSkill(node=node.name, detail="ask node has no skill declared")
    path = plugin_dir / "skills" / node.skill / "SKILL.md"
    if not path.exists():
        return plugins.UnresolvedSkill(node=node.name, detail=f"no SKILL.md at {path}")
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError as e:
        return plugins.UnresolvedSkill(node=node.name, detail=f"SKILL.md at {path} is not valid UTF-8: {e}")
    try:
        plugins._validate_skill_text(text, path, node.skill)
    except plugins.SkillLoadError as e:
        return plugins.UnresolvedSkill(node=node.name, detail=str(e))
    return None


def _load_body_module(plugin_dir: Path, module_name: str, cache: dict[str, ModuleType | None]) -> ModuleType | None:
    """One ``module_name.py`` under ``plugin_dir`` is loaded at most once
    per ``validate()`` call — several nodes commonly name bodies in the
    same file (e.g. every ``call``/``route`` node in one ``init.py``), and
    re-importing it per node would re-run its top-level code once per
    reference instead of once per file. ``None`` in ``cache`` means
    already tried and failed; never re-attempted within the same call.
    Never registered in ``sys.modules`` — ``validate()`` caches nothing
    across calls (spec.md's Rejected alternatives, guideline 4), so this
    cache is scoped to the caller's own dict, not the process."""
    if module_name in cache:
        return cache[module_name]
    module_file = plugin_dir / f"{module_name}.py"
    if not module_file.exists():
        cache[module_name] = None
        return None
    try:
        spec = importlib.util.spec_from_file_location(module_name, module_file)
        if spec is None or spec.loader is None:
            cache[module_name] = None
            return None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    except Exception:
        # A plugin's init.py runs its own top-level code on import — an
        # accepted, narrower version of the same first-party trust boundary
        # `plugin_blueprint.md §10` Risk 1 already names for `call` nodes at
        # run time (spec.md's Concerns). Any failure here means the body
        # doesn't resolve, the expected outcome this check reports.
        cache[module_name] = None
        return None
    cache[module_name] = module
    return module


def _check_body(
    plugin_dir: Path, node: plugins.Node, body: str, modules: dict[str, ModuleType | None]
) -> plugins.UnresolvedBody | None:
    module_name, sep, func_name = body.partition(":")
    if not sep or not module_name or not func_name:
        return plugins.UnresolvedBody(node=node.name, body=body)
    module = _load_body_module(plugin_dir, module_name, modules)
    if module is None:
        return plugins.UnresolvedBody(node=node.name, body=body)
    func = getattr(module, func_name, None)
    if not callable(func):
        return plugins.UnresolvedBody(node=node.name, body=body)
    return None


def validate(plugin_dir: Path) -> plugins.ManifestOutcome:
    """Read ``plugin_dir``'s own ``plugin.toml`` and run the `§8` checks
    against it, in order, stopping at the first failure — the original
    seven, plus an eighth added at D3's own Deploy-stage review:
    acyclicity. §8 never named it because nothing that actually walked a
    graph existed yet to make the gap real; D3's own `run_graph` does, and
    a graph that loops back on itself has no other way to be caught before
    it makes a walk run forever. Runs no step of the plugin's declared
    sequence — a ``body`` reference is only imported far enough to confirm
    the named function exists."""
    manifest_path = plugin_dir / "plugin.toml"
    if not manifest_path.exists():
        return plugins.ManifestParseError(detail=f"no plugin.toml at {manifest_path}")
    try:
        manifest = plugins._parse_manifest(manifest_path.read_text(encoding="utf-8"))
    except (tomllib.TOMLDecodeError, KeyError, TypeError, UnicodeDecodeError) as e:
        return plugins.ManifestParseError(detail=str(e))

    for entry in manifest.entries:
        outcome = _check_schema(plugin_dir, entry)
        if outcome is not None:
            return outcome

    for node in manifest.nodes:
        if node.kind == "ask":
            skill_outcome = _check_skill(plugin_dir, node)
            if skill_outcome is not None:
                return skill_outcome

    seen: set[str] = set()
    for node in manifest.nodes:
        if node.name in seen:
            return plugins.DuplicateNodeName(name=node.name)
        seen.add(node.name)

    for entry in manifest.entries:
        if entry.start not in seen:
            return plugins.DanglingTarget(node=entry.tool, target=entry.start)

    for node in manifest.nodes:
        if node.next is not None and node.next not in seen:
            return plugins.DanglingTarget(node=node.name, target=node.next)
        for port in node.ports:
            if port not in seen:
                return plugins.DanglingTarget(node=node.name, target=port)

    modules: dict[str, ModuleType | None] = {}
    for node in manifest.nodes:
        if node.body is not None:
            body_outcome = _check_body(plugin_dir, node, node.body, modules)
            if body_outcome is not None:
                return body_outcome

    unreachable = plugins._first_unreachable(manifest)
    if unreachable is not None:
        return unreachable

    cycle = plugins._first_cycle(manifest)
    if cycle is not None:
        return cycle

    return plugins.Valid(manifest=manifest)


# ── D3: catalog and tool surface, and the graph's execution seams ────────
# Its full contract is `docs/tasks/D3-graph-dispatch/spec.md`.


def discover_plugins(plugins_root: Path | None = None) -> tuple[plugins.InstalledPlugin, ...]:
    """Every immediate subdirectory of ``plugins_root`` (default:
    ``plugins._plugins_root()``) whose ``plugin.toml`` comes back ``Valid``
    from ``validate()``. One that doesn't is silently excluded — the same
    "not trustworthy, don't build on it" outcome the `§8` checks already
    produce, applied by simply not including it. A root that doesn't exist
    yet (nothing installed) returns an empty tuple, not an error."""
    root = plugins_root if plugins_root is not None else plugins._plugins_root()
    if not root.is_dir():
        return ()
    installed = []
    for child in sorted(root.iterdir()):
        if not child.is_dir():
            continue
        outcome = validate(child)
        if isinstance(outcome, plugins.Valid):
            installed.append(
                plugins.InstalledPlugin(name=outcome.manifest.name, directory=child, manifest=outcome.manifest)
            )
    return tuple(installed)


def _coerce_text(value: object) -> str:
    """One coercion rule, used both for an ``ask`` node's input and for a
    run's final ``text`` — a plugin author's data is usually already a
    ``str`` by the time it reaches either; a ``dict``/``list`` (the raw
    tool-call ``arguments``, most often) becomes JSON, not a Python
    ``repr()``, so a model reading it sees ordinary JSON, not
    single-quoted Python syntax."""
    if isinstance(value, str):
        return value
    if isinstance(value, dict | list):
        return json.dumps(value)
    return str(value)


def _resolve_body(plugin_dir: Path, node: plugins.Node, modules: dict[str, ModuleType | None]) -> Callable:
    """Reuses ``_load_body_module``'s existing import-and-cache logic
    rather than a second one. ``node.body`` and the function it names are
    trusted to resolve — ``validate()``'s own ``UnresolvedBody`` check
    already proved it for whatever ``Manifest`` a caller reached this
    function with; the asserts are a self-check against this item's own
    bug, not a validation this function repeats."""
    assert node.body is not None, f"{node.name!r} has no body to resolve"
    module_name, _, func_name = node.body.partition(":")
    module = _load_body_module(plugin_dir, module_name, modules)
    assert module is not None, f"{node.name!r}'s body module {module_name!r} did not resolve"
    return getattr(module, func_name)


# ── F1: the approval gate ────────────────────────────────────────────────
# Its full contract is `docs/tasks/F1-call-node-approval/spec.md`.


async def _default_approve(plugin: str, node: str, value: object) -> bool:
    """``run_graph``'s default ``ApproveFn`` — the real behavior for this
    project's one interactive caller today: a person at a WSL terminal,
    asked synchronously and blocked on until they answer. ``input()`` runs
    in a worker thread via ``asyncio.to_thread`` so the blocking call never
    freezes the event loop the rest of a ``dispatch`` call is running on.
    Fails closed: anything other than ``y``/``yes`` (case-insensitively) is
    a decline."""
    prompt = f"{plugin}'s {node!r} step wants to run with input {value!r}. Allow it? [y/N] "
    answer = await asyncio.to_thread(input, prompt)
    return answer.strip().lower() in ("y", "yes")


async def run_graph(
    plugin_dir: Path,
    manifest: plugins.Manifest,
    entry: plugins.Entry,
    arguments: dict,
    *,
    ask: plugins.AskFn,
    approve: plugins.ApproveFn = _default_approve,
) -> plugins.DagResult:
    """Walks ``manifest``'s declared steps from ``entry.start``, exactly as
    ``validate()`` already proved they connect and never loop back on
    themselves (its reachability and acyclicity checks — trusted here the
    same way ``run_child`` already trusts an already-validated system
    prompt). Acyclicity is what keeps this walk bounded: nothing here
    caps the number of steps, because an already-validated ``manifest``
    cannot revisit a node, so a walk over one ends in at most
    ``len(manifest.nodes)`` steps on its own. A hand-built ``Manifest``
    that skips ``validate()`` (as this module's own tests do) has no such
    guarantee and can still loop forever — the same trust an untested
    caller already extends to reachability, body resolution, and every
    other `§8` check this function never re-runs. Every node kind's own
    failure — a
    raised exception, a ``route`` body naming an undeclared port, a
    ``call`` step declined by ``approve``, an ``each``/``wait`` node this
    vocabulary doesn't execute yet, an
    ``ask`` whose sub-task didn't finish cleanly — ends the walk at that
    node: ``failed_node`` names it, ``text`` is one fixed, generic sentence
    naming the plugin and the step (never the raw exception or its
    traceback), and nothing raises out of this function for any of them.

    A ``call`` node is asked about, via ``approve``, before anything else
    happens for it — no other kind is (`docs/tasks/F1-call-node-approval/
    spec.md`). Approval only clears the way to ask; no ``call`` node has a
    body-execution mechanism wired to it yet (PLUGINS' execution half, a
    separate later item), so an approved ``call`` node still ends the walk,
    the same way an ``each``/``wait`` node already does — the trace's
    ``detail`` is what distinguishes "declined" from "not runnable yet".

    Exactly one ``value`` is threaded through the loop — the entry's raw
    ``arguments`` for the first node, each node's own output after that.
    No node is ever given more than what the step immediately before it
    produced (CLAUDE.md: "A plugin graph's node receives only its
    immediate predecessor's output, never the run's accumulated
    history")."""
    by_name = plugins._node_index(manifest)
    modules: dict[str, ModuleType | None] = {}
    trace: list[plugins.NodeTrace] = []
    value: object = arguments
    current = entry.start

    def result(text: str, failed_node: str | None) -> plugins.DagResult:
        return plugins.DagResult(
            plugin=manifest.name, entry=entry.tool, text=text, artifacts=(), trace=tuple(trace), failed_node=failed_node
        )

    def failed(node: plugins.Node, detail: str) -> plugins.DagResult:
        trace.append(plugins.NodeTrace(node=node.name, kind=node.kind, visit=0, ok=False, port=None, detail=detail))
        return result(f"{manifest.name}'s {node.name!r} step did not complete.", node.name)

    while True:
        node = by_name[current]

        if node.kind in ("each", "wait"):
            return failed(node, f"{node.kind} steps are not runnable yet")

        port: str | None = None
        try:
            if node.kind == "compute":
                value = _resolve_body(plugin_dir, node, modules)(value)
            elif node.kind == "route":
                port = _resolve_body(plugin_dir, node, modules)(value)
                if port not in node.ports:
                    return failed(node, f"returned {port!r}, not a declared port")
            elif node.kind == "ask":
                assert node.skill is not None, f"{node.name!r} has no skill to ask"
                skill_ref = plugins.SkillRef(plugin=manifest.name, skill=node.skill)
                output = await ask(skill_ref, _coerce_text(value))
                if output is None:
                    return failed(node, "child did not complete")
                value = output
            elif node.kind == "call":
                approved = await approve(manifest.name, node.name, value)
                return failed(node, f"{node.kind} steps are not runnable yet" if approved else "declined")
            elif node.kind == "stop":
                pass
            else:
                return failed(node, f"unrecognized node kind {node.kind!r}")
        except Exception as e:
            return failed(node, f"node raised {type(e).__name__}")

        trace.append(plugins.NodeTrace(node=node.name, kind=node.kind, visit=0, ok=True, port=port, detail=None))

        next_name = port if node.kind == "route" else node.next
        if next_name is None:
            return result(_coerce_text(value), None)
        current = next_name
