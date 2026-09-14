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

from sadana import artifact_store, plugins


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


def validate(plugin_dir: Path, *, check_bodies: bool = True) -> plugins.ManifestOutcome:
    """Read ``plugin_dir``'s own ``plugin.toml`` and run the `§8` checks
    against it, in order, stopping at the first failure — the original
    seven, plus an eighth added at D3's own Deploy-stage review:
    acyclicity. §8 never named it because nothing that actually walked a
    graph existed yet to make the gap real; D3's own `run_graph` does, and
    a graph that loops back on itself has no other way to be caught before
    it makes a walk run forever. Runs no step of the plugin's declared
    sequence — a ``body`` reference is only imported far enough to confirm
    the named function exists.

    ``check_bodies=False`` skips that one step. It is the only step that
    imports and executes ``plugin_dir``'s own code
    (``_load_body_module()``): safe for this function's original caller,
    ``discover_plugins()``, which only ever scans plugins already sitting
    under ``_plugins_root()`` — already fetched, already first-party by
    this project's current posture (`plugin_blueprint.md` §10 Risk 1).
    Unsafe for anything nobody has reviewed yet. CLAUDE.md: "A check that
    can execute code as a side effect of validating it... must offer a
    mode that never executes anything, and any caller handling input from
    a source it doesn't already trust uses that mode" —
    `docs/tasks/PLUGIN-MARKET-01-submit-vet-and-browse-safely/spec.md`'s
    own reason for this parameter existing at all."""
    manifest_path = plugin_dir / "plugin.toml"
    if not manifest_path.exists():
        return plugins.ManifestParseError(detail=f"no plugin.toml at {manifest_path}")
    try:
        manifest = plugins._parse_manifest(manifest_path.read_text(encoding="utf-8"))
    except (tomllib.TOMLDecodeError, KeyError, TypeError, UnicodeDecodeError) as e:
        return plugins.ManifestParseError(detail=str(e))

    # Names first, and the plugin's own name before anything else: it is the
    # one field here that becomes a filesystem path and an environment
    # variable identifier rather than staying a label, so nothing downstream
    # should get to use it before it has been checked
    # (`docs/tasks/PLUGIN-CONFIG-01-settings-and-secrets-a-plugin-owns/spec.md`).
    # ``isinstance`` before the regex, not belt-and-braces: these checks run
    # after the ``try`` above, so ``PLUGIN_NAME_RE.fullmatch(123)`` would leave
    # here as an uncaught ``TypeError`` rather than an outcome — and
    # ``discover_plugins`` has no handler, so one malformed plugin would take
    # out every other one instead of being silently excluded.
    if not isinstance(manifest.name, str) or not plugins.PLUGIN_NAME_RE.fullmatch(manifest.name):
        return plugins.InvalidPluginName(name=str(manifest.name))

    seen_settings: set[str] = set()
    for setting in manifest.settings:
        if not isinstance(setting.name, str) or not plugins.SETTING_NAME_RE.fullmatch(setting.name):
            return plugins.InvalidSettingName(setting=str(setting.name))
        if setting.name in seen_settings:
            return plugins.DuplicateSettingName(name=setting.name)
        seen_settings.add(setting.name)

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

    if check_bodies:
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


# ── H18: parked approvals ────────────────────────────────────────────────
# Its full contract is `docs/tasks/H18-parked-approvals/spec.md`.


def _persistable_paused_value(value: object) -> object:
    """`paused_value` becomes `conversation_store`'s `plugin_pauses.
    paused_value_json` — a JSON column, potentially read back by a whole
    different process (H18's own point). A `_sadana_`-prefixed key
    (`plugin_dispatch.build_dispatch`'s own dispatch-time injection — a
    live `DispatchContext`, the conversation key) is metadata about *how*
    this dispatch call was made, never part of what was parked for
    approval, and it is what makes `value` unserializable in the first
    place. Stripped here rather than crashing the park; `_sadana_
    session_key` is re-injected fresh by `plugin_dispatch.resume_paused_run`
    when the approved body actually runs, since a conversation's own key
    never changes. `_sadana_memory_ctx` is not re-injected — a resumed
    `call` body reading memory context is new work this item did not
    build. A non-dict `value` has no reserved keys to strip by
    construction (only `dispatch()`'s own `arguments: dict` ever carries
    them), so it passes through unchanged."""
    if isinstance(value, dict):
        return {k: v for k, v in value.items() if not k.startswith("_sadana_")}
    return value


async def parking_approve(plugin: str, node: str, value: object) -> bool:
    """Never actually awaited for a real decision. `_run_graph` recognises
    this exact function object, by identity, before it would call it, and
    parks the walk instead — the same way `KINDS_NOT_RUNNABLE` is checked
    before a node kind's body ever runs. Exists as a real `ApproveFn` rather
    than `None` so `run_graph`/`build_dispatch`/`resume_paused_run` keep one
    required-with-a-default parameter shape throughout; if this ever *is*
    awaited (a caller that bypasses `_run_graph`'s own check), the safe
    answer is still `False` — parking is not the same value as approval.

    Every non-interactive caller — `client_surface.take_turn`'s own default
    when no `approve` is given, and every door action that resumes a run —
    passes this instead of a real judgement callable, so a `call` node
    never blocks a process with nobody at the keyboard."""
    return False


async def run_graph(
    plugin_dir: Path,
    manifest: plugins.Manifest,
    entry: plugins.Entry,
    arguments: dict,
    *,
    ask: plugins.AskFn,
    approve: plugins.ApproveFn = _default_approve,
    resume: plugins.ResumeState | None = None,
    output_dir: Path | None = None,
) -> plugins.DagResult:
    """``_run_graph``'s walk, wrapped in the one thing that must surround the
    whole of it.

    ``artifact_store.activate(output_dir)`` is the only channel by which a
    node body learns where it may write. A body is never handed the path as an
    argument — a run's directory is a function of the *run*, which nothing in
    ``plugin.toml``, a body's signature or the threaded value names, so unlike
    a plugin's settings it cannot be looked up from what the body already
    knows (`docs/tasks/ARTIFACT-STORE-01-somewhere-to-put-what-a-plugin-makes
    /spec.md` § Why a contextvar). ``asyncio.to_thread`` propagates context, so
    a ``call`` body reaches it too.

    Everything this function does beyond that activation is in
    ``_run_graph``, whose docstring is the contract."""
    with artifact_store.activate(output_dir):
        return await _run_graph(
            plugin_dir,
            manifest,
            entry,
            arguments,
            ask=ask,
            approve=approve,
            resume=resume,
        )


async def _run_graph(
    plugin_dir: Path,
    manifest: plugins.Manifest,
    entry: plugins.Entry,
    arguments: dict,
    *,
    ask: plugins.AskFn,
    approve: plugins.ApproveFn,
    resume: plugins.ResumeState | None,
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
    ``call`` step declined by ``approve``, an ``each`` node this vocabulary
    doesn't execute yet, an
    ``ask`` whose sub-task didn't finish cleanly — ends the walk at that
    node: ``failed_node`` names it, ``text`` is one fixed, generic sentence
    naming the plugin and the step (never the raw exception or its
    traceback), and nothing raises out of this function for any of them. A
    ``wait`` node is not a failure: reaching one for the first time ends the
    walk with ``paused_node`` set instead (`docs/tasks/GATEWAY-DAEMON-02
    -scheduled-and-resumable-triggers/spec.md`), and ``resume`` is how a
    caller continues past it later.

    ``resume``, when given, replaces the walk's usual starting position
    (``entry.start`` with ``arguments`` as ``value``) with
    ``resume.node``'s own successor and ``resume.value`` — the resuming
    event's payload standing in for that ``wait`` node's own output, exactly
    as any other node's output would. ``resume.trace``/``resume.artifacts``
    seed the walk's own record of everything that already happened before
    the pause, plus one new entry recording the ``wait`` node itself as
    resumed. Nothing else about the walk changes: it does not know or care
    whether it started fresh or resumed.

    A ``call`` node is asked about, via ``approve``, before anything else
    happens for it — no other kind is (`docs/tasks/F1-call-node-approval/
    spec.md`). Only once approved does its body run, resolved the same way
    ``compute``'s already is, but off the event loop (``asyncio.to_thread``)
    since ``call`` is the one kind expected to block
    (`docs/tasks/G1-call-node-run/spec.md`) — a declined ``call`` node never
    reaches body resolution at all.

    Exactly one ``value`` is threaded through the loop — the entry's raw
    ``arguments`` for the first node, each node's own output after that.
    No node is ever given more than what the step immediately before it
    produced (CLAUDE.md: "A plugin graph's node receives only its
    immediate predecessor's output, never the run's accumulated
    history")."""
    by_name = plugins._node_index(manifest)
    modules: dict[str, ModuleType | None] = {}
    trace: list[plugins.NodeTrace] = []
    artifacts: list[plugins.Artifact] = []

    def result(text: str, failed_node: str | None) -> plugins.DagResult:
        return plugins.DagResult(
            plugin=manifest.name,
            entry=entry.tool,
            text=text,
            artifacts=tuple(artifacts),
            trace=tuple(trace),
            failed_node=failed_node,
        )

    def failed(node: plugins.Node, detail: str) -> plugins.DagResult:
        trace.append(plugins.NodeTrace(node=node.name, kind=node.kind, visit=0, ok=False, port=None, detail=detail))
        return result(f"{manifest.name}'s {node.name!r} step did not complete.", node.name)

    def finish_call_body(node: plugins.Node, body_value: object) -> tuple[object, str | None] | plugins.DagResult:
        """The `Artifact`-checking step a `call` node's returned value
        always gets, whether the body just ran live or is finishing a
        parked approval (H18) — one function so the two paths can't drift
        apart. Returns `(value, detail)` to fold into the trace on success,
        or a terminal `DagResult` on a bad artifact reference."""
        if isinstance(body_value, plugins.Artifact):
            # Checked on the value crossing back, not at write time: a body
            # is free to build a `ref` it never wrote to, so what it
            # *claims* is the thing worth checking — G2's own reason for
            # inspecting the returned value at all. Nothing is moved or
            # deleted on the strength of a `ref`, so a bad one costs this
            # node and nothing else.
            if body_value.kind == "file" and not artifact_store.contains_active(body_value.ref):
                return failed(node, "file artifact points outside the run's own output directory")
            artifacts.append(body_value)
            return body_value.ref, f"emitted {body_value.kind} artifact {body_value.name!r}"
        return body_value, None

    # Before anything, including a resume: a plugin whose declared settings
    # have no value cannot work, and saying so here is the difference between
    # a message naming the value and a failure inside somebody's HTTP call
    # (`docs/tasks/PLUGIN-CONFIG-01-settings-and-secrets-a-plugin-owns/spec.md`).
    # Placed in ``run_graph`` rather than in ``dispatch`` because this is the
    # one door every execution path already goes through — dispatch, the eval
    # harness, and the gateway's resume. A manifest declaring no settings
    # reaches the walk exactly as it did before. ``failed_node`` is `"entry"`
    # because no node ran; `trace` and `artifacts` are still empty here, so
    # `result()` reports exactly that.
    absent = plugins.missing_settings(manifest)
    if absent:
        named = ", ".join(repr(name) for name in absent)
        text = (
            f"{manifest.name} needs a value for {named}. "
            f"Set each one with: sadana plugin set {manifest.name} <setting>"
        )
        if resume is not None:
            # A paused run must survive this. `paused_node=None` would make
            # `plugin_dispatch.resume_paused_run` call `delete_pause`, so a
            # setting blanked (or a plugin update adding a required one)
            # while a run waits would destroy the run the moment its answer
            # arrived — the person could then set the value and still never
            # resume. Returned exactly as the pause already was, so
            # `save_pause_from_result` rewrites it unchanged and this is
            # idempotent however many answers arrive before the value is set.
            # `paused_kind`/`paused_value` are carried over from `resume`
            # itself (H18) for the same reason: a `call`-kind pause that
            # hits this guard must stay a `call`-kind approvals row, not
            # silently downgrade to an unmarked one.
            return plugins.DagResult(
                plugin=manifest.name,
                entry=entry.tool,
                text=text,
                artifacts=resume.artifacts,
                trace=resume.trace,
                failed_node=None,
                paused_node=resume.node,
                paused_kind=resume.kind,
                paused_value=resume.value if resume.kind == "call" else None,
            )
        return result(text, "entry")

    value: object
    current: str
    if resume is None:
        value = arguments
        current = entry.start
    else:
        parked_node = by_name[resume.node]
        trace = list(resume.trace)
        artifacts = list(resume.artifacts)
        if resume.kind == "call":
            # H18. A `call` never ran its body when it parked — `resume.value`
            # is the `paused_value` a live park recorded, the exact input the
            # body would have received had it run synchronously.
            assert resume.decision in (
                "approve",
                "decline",
            ), f"a call-kind resume needs decision 'approve' or 'decline', got {resume.decision!r}"
            if resume.decision == "decline":
                return failed(parked_node, "declined")

            def run_body(n: plugins.Node = parked_node, v: object = resume.value) -> object:
                return _resolve_body(plugin_dir, n, modules)(v)

            try:
                body_value = await asyncio.to_thread(run_body)
            except Exception as e:
                return failed(parked_node, f"node raised {type(e).__name__}")
            outcome = finish_call_body(parked_node, body_value)
            if isinstance(outcome, plugins.DagResult):
                return outcome
            value, call_detail = outcome
            trace.append(
                plugins.NodeTrace(node=parked_node.name, kind="call", visit=0, ok=True, port=None, detail=call_detail)
            )
        else:
            value = resume.value
            trace.append(
                plugins.NodeTrace(
                    node=parked_node.name, kind=parked_node.kind, visit=0, ok=True, port=None, detail="resumed"
                )
            )
        if parked_node.next is None:
            return result(_coerce_text(value), None)
        current = parked_node.next

    while True:
        node = by_name[current]

        if node.kind in plugins.KINDS_NOT_RUNNABLE:
            return failed(node, f"{node.kind} steps are not runnable yet")
        if node.kind == "wait":
            return plugins.DagResult(
                plugin=manifest.name,
                entry=entry.tool,
                text=f"{manifest.name}'s {node.name!r} step is waiting for an external answer.",
                artifacts=tuple(artifacts),
                trace=tuple(trace),
                failed_node=None,
                paused_node=node.name,
            )

        port: str | None = None
        detail: str | None = None
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
                if approve is parking_approve:
                    # H18. Parked instead of asked — `approve` is never
                    # called at all. `docs/tasks/H18-parked-approvals/spec.md`
                    # Design §2 documents this mechanism in full.
                    return plugins.DagResult(
                        plugin=manifest.name,
                        entry=entry.tool,
                        text=f"{manifest.name}'s {node.name!r} step is waiting for approval.",
                        artifacts=tuple(artifacts),
                        trace=tuple(trace),
                        failed_node=None,
                        paused_node=node.name,
                        paused_kind="call",
                        paused_value=_persistable_paused_value(value),
                    )
                approved = await approve(manifest.name, node.name, value)
                if not approved:
                    return failed(node, "declined")

                def run_body(n: plugins.Node = node, v: object = value) -> object:
                    return _resolve_body(plugin_dir, n, modules)(v)

                body_value = await asyncio.to_thread(run_body)
                outcome = finish_call_body(node, body_value)
                if isinstance(outcome, plugins.DagResult):
                    return outcome
                value, detail = outcome
            elif node.kind == "stop":
                pass
            else:
                return failed(node, f"unrecognized node kind {node.kind!r}")
        except Exception as e:
            return failed(node, f"node raised {type(e).__name__}")

        trace.append(plugins.NodeTrace(node=node.name, kind=node.kind, visit=0, ok=True, port=port, detail=detail))

        next_name = port if node.kind == "route" else node.next
        if next_name is None:
            return result(_coerce_text(value), None)
        current = next_name
