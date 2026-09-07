"""PLUGINS' I/O half: everything that touches a real file.

`docs/tasks/D2-manifest-validation/spec.md`. ``plugins.py`` stays pure
data and pure parsing; this module is where a path actually gets read,
per CLAUDE.md's rule that disk-touching code is its own file, separate
from a block's pure module.
"""

from __future__ import annotations

import importlib.util
import json
import tomllib
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
    """Read ``plugin_dir``'s own ``plugin.toml`` and run the seven `§8`
    checks against it, in order, stopping at the first failure. Runs no
    step of the plugin's declared sequence — a ``body`` reference is only
    imported far enough to confirm the named function exists."""
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

    return plugins.Valid(manifest=manifest)
