"""PLUGINS' static contract — the state a plugin run reports back with.

`docs/tasks/D1-dagresult-dispatch/spec.md`: replaces dispatch's old bare
``str`` return (CONV-10's own note: no channel to report anything back
beyond a result string) with a typed result. An error and a success are no
longer the same type; a plugin's output has a real channel
(``artifacts``); a step-by-step record (``trace``) exists for whatever
later reads it.

Pure data only — no disk, network, or clock access anywhere in this module,
so it never needs to be split from itself under CLAUDE.md's I/O-module
rule. No dependency on ``conversation.py``: this module is a leaf, so no
caller of it ever risks an import cycle."""

from __future__ import annotations

import json
import os
import re
import tomllib
from collections import deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Literal, get_args

from sadana import config

# All seven node kinds `plugin_blueprint.md §6` names, shared by `Node.kind`
# (the declared graph) and `NodeTrace.kind` (a run of it) — one closed set,
# declared once.
NodeKind = Literal["compute", "ask", "route", "stop", "call", "each", "wait"]

NODE_KINDS: tuple[str, ...] = get_args(NodeKind)

# Which of ``Node``'s optional fields each kind actually uses. One table, read
# by everything that has to present the vocabulary to a person rather than
# guess at it (`docs/tasks/PLUGIN-EDITOR-01-draw-wire-and-save/spec.md`) —
# derived from ``NodeKind`` above rather than typed out a second time, which
# is what keeps "adding a kind is a work item, not a field" true: a new kind
# is a compile-time-visible hole here, not a silently-empty row.
KIND_USES: dict[str, tuple[str, ...]] = {
    "compute": ("body", "next"),
    "ask": ("skill", "next"),
    "route": ("body", "ports"),
    "stop": (),
    "call": ("body", "next"),
    "each": ("next",),
    "wait": ("next",),
}

# Kinds nothing can execute yet. ``plugin_manifest.run_graph`` refuses these
# by reading this set rather than naming ``each`` itself, so there is one fact
# here rather than a matched pair to keep in step. A kind listed here is still
# drawn in the editor, marked unusable rather than hidden, so a creator can lay
# out what they mean before the runtime can carry it out.
KINDS_NOT_RUNNABLE: frozenset[str] = frozenset({"each"})

# What a plugin's own name, and a setting's name, are allowed to be. Both are
# allowlists rather than denylists, and both exist because these names stop
# being labels: a plugin's name becomes a directory
# (``_plugins_root() / name``, ``_skill_path``, ``plugin_install.install``),
# and with ``[[setting]]`` it becomes an environment variable identifier too
# (CLAUDE.md: "A caller-supplied name that becomes a filesystem path is
# checked against an allowlist pattern before it touches a path").
#
# Lowercase only, so no two names differ by case alone on a filesystem that
# does not care — the same class of collision pre-commit's
# ``check-case-conflict`` guards in this repo. No leading, trailing or
# doubled dash, which leaves no way to spell ``..``, a separator, or an
# absolute path. The 64-character ceiling is not cosmetic: without it a
# 400-character name passes every check and then raises ``OSError: File name
# too long`` from the first ``is_file()``, which used to escape the editor's
# ``handle()`` as a 500 carrying the absolute path of the plugins directory.
#
# One pattern for the whole project. The editor grew its own copy of this
# rule first (``editor_server._SAFE_NAME``); a second source of truth means a
# name can pass one door and fail another depending which validated it.
PLUGIN_NAME_RE = re.compile(r"^[a-z0-9](-?[a-z0-9]){0,63}$")
SETTING_NAME_RE = re.compile(r"^[a-z0-9]+(_[a-z0-9]+)*$")


@dataclass(frozen=True)
class SkillRef:
    """A name, not a path — see D2's spec.md Design section. A plugin's
    real on-disk location can move (re-install, version bump)
    independently of anything holding a reference to it, so this holds
    the two names that resolve to one, fresh, every call."""

    plugin: str
    skill: str


class SkillLoadError(Exception):
    """A skill's SKILL.md is missing, malformed, or its declared name
    doesn't match what was asked for."""


_SKILL_DESCRIPTION_MAX_CHARS = 1024  # agentskills.io's own ceiling.


def _plugins_root() -> Path:
    """Where installed plugins live. ``SADANA_PLUGINS_DIR`` if set, else
    under the existing state directory. Resolved fresh on every call,
    never cached — same posture as ``config.get_paths()``."""
    return config.env_path("SADANA_PLUGINS_DIR", default=config.get_paths().state_dir / "plugins")


def plugin_dir(plugins_root: Path, name: str) -> Path | None:
    """The directory ``name`` refers to under ``plugins_root``, or ``None``
    if ``name`` is not one this project will touch.

    Both halves of CLAUDE.md's rule, never either: the pattern is the
    intent, and the containment check after resolution is the proof. The
    second half catches what the first cannot — a symlink inside
    ``plugins_root`` pointing out of it resolves elsewhere while its name
    stays perfectly well-formed.

    Every place an outside name becomes a plugin directory goes through
    here: the editor's HTTP surface, and ``plugin_install`` before it moves
    a fetched tree into place."""
    if not PLUGIN_NAME_RE.fullmatch(name):
        return None
    candidate = plugins_root / name
    if candidate.resolve().parent != plugins_root.resolve():
        return None
    return candidate


def _skill_path(ref: SkillRef) -> Path:
    """``<plugins_root>/<plugin>/skills/<skill>`` — the layout the
    requester's own plugin-repo shape already commits to, used one level
    early. Only what populates ``_plugins_root()`` needs to change when a
    real installer exists; this function and every caller of it don't."""
    return _plugins_root() / ref.plugin / "skills" / ref.skill


def _parse_skill_md(text: str) -> tuple[dict[str, str], str]:
    """Split a SKILL.md's ``---``-delimited frontmatter from its body.
    Hand-rolled rather than a YAML dependency: the frontmatter this
    project reads is two flat string fields (``name``, ``description``),
    and a YAML parser was never needed just for this. Raises
    ``SkillLoadError`` if the delimiters are missing or unclosed."""
    if not text.startswith("---\n"):
        raise SkillLoadError("SKILL.md is missing its frontmatter delimiter")
    end = text.find("\n---", 4)
    if end == -1:
        raise SkillLoadError("SKILL.md's frontmatter is never closed")
    header = text[4:end]
    body = text[end + 4 :].lstrip("\n")
    frontmatter: dict[str, str] = {}
    for line in header.splitlines():
        if not line.strip():
            continue
        key, _, value = line.partition(":")
        frontmatter[key.strip()] = value.strip()
    return frontmatter, body


def _validate_skill_text(text: str, path: Path, expected_skill: str) -> str:
    """Validate an already-read SKILL.md's frontmatter and return its
    body. ``path`` is only used to name the file in an error — this
    function does no I/O of its own, so both of this project's two
    resolvers of *where* a SKILL.md lives — ``_skill_path()``'s configured
    plugins root, and ``plugin_manifest.validate()``'s own candidate
    ``plugin_dir`` — share one answer to *is it valid* once they've each
    read it. Raises ``SkillLoadError`` when the declared ``name`` doesn't
    match ``expected_skill``, or ``description`` is absent or over 1024
    characters."""
    frontmatter, body = _parse_skill_md(text)
    name = frontmatter.get("name")
    if name != expected_skill:
        raise SkillLoadError(f"SKILL.md at {path} declares name {name!r}, expected {expected_skill!r}")
    description = frontmatter.get("description")
    if not description:
        raise SkillLoadError(f"SKILL.md at {path} is missing a description")
    if len(description) > _SKILL_DESCRIPTION_MAX_CHARS:
        raise SkillLoadError(
            f"SKILL.md at {path} has a {len(description)}-char description "
            f"(over the {_SKILL_DESCRIPTION_MAX_CHARS}-char limit)"
        )
    return body


@dataclass(frozen=True)
class Artifact:
    """Something a plugin run produced, beyond the text a model reads."""

    kind: Literal["link", "file"]
    name: str
    ref: str  # a URL, or a path under the run's own output directory


@dataclass(frozen=True)
class NodeTrace:
    """One step of a DAG run, as it actually happened. ``kind`` lists all
    seven node kinds the design material names (`plugin_blueprint.md §6`),
    not only the four with no outside effect — a trace entry has to be
    able to name any node kind once a walker exists for the rest of
    them."""

    node: str
    kind: NodeKind
    visit: int  # 0-based; > 0 only once `each` exists
    ok: bool
    port: str | None  # which port was taken, for a `route`
    detail: str | None  # one line, for the transcript


@dataclass(frozen=True)
class DagResult:
    """What a plugin's dispatch call hands back. ``text`` is always
    present and always safe to render as-is, whether or not
    ``failed_node`` is set — it is the one field ``run_turn`` renders
    unconditionally into the tool-result message.

    ``paused_node`` (`docs/tasks/GATEWAY-DAEMON-02-scheduled-and-resumable-triggers
    /spec.md`) is the third, additive state a run can end in: a ``wait``
    node was reached and the run stopped there, not because anything
    failed. Deliberately one more optional field rather than a
    ``DagResult | DagPaused`` union — a paused result's ``text``/
    ``artifacts``/``trace`` mean exactly what a terminal result's do, only
    the resume position is genuinely new (CLAUDE.md: "a result whose
    branches differ only by one field... doesn't need this shape")."""

    plugin: str
    entry: str
    text: str
    artifacts: tuple[Artifact, ...] = ()
    trace: tuple[NodeTrace, ...] = ()
    failed_node: str | None = None  # None == the run reached a terminal node
    paused_node: str | None = None  # set only when the run stopped at a `wait` node


@dataclass(frozen=True)
class ResumeState:
    """Where a paused run left off, handed back to ``run_graph`` (alongside
    that same call's own ``manifest``/``entry`` parameters — never
    duplicated here) to continue it
    (`docs/tasks/GATEWAY-DAEMON-02-scheduled-and-resumable-triggers/spec.md`).
    The plugin's directory and ``Manifest`` object are re-resolved fresh by
    ``plugin_dispatch.resume_paused_run`` from a stored plugin *name* before
    ``run_graph`` is ever called — the same "never cached... re-validated
    per call" posture ``InstalledPlugin``'s own docstring already takes, and
    this project's own names-not-pointers rule."""

    node: str  # the `wait` node's own name
    value: object  # the resuming event's payload, standing in for a predecessor's output
    trace: tuple[NodeTrace, ...]
    artifacts: tuple[Artifact, ...]


# ── D2: manifest validation ──────────────────────────────────────────────
# Its full contract is `docs/tasks/D2-manifest-validation/spec.md`.


@dataclass(frozen=True)
class Entry:
    """One tool a plugin exposes, and where its graph starts."""

    tool: str
    purpose: str
    parameters: str  # path to a schema file, relative to the plugin's own root
    start: str  # the node name execution would begin at


@dataclass(frozen=True)
class Node:
    """One node of a plugin's declared graph — data, not code
    (`plugin_blueprint.md §5.2`). ``kind`` is the same 7-value set
    ``NodeTrace.kind`` already uses."""

    name: str
    kind: NodeKind
    body: str | None = None  # "module:function", for compute/call/route
    skill: str | None = None  # a skill name, for ask
    next: str | None = None  # a single successor node name
    ports: tuple[str, ...] = ()  # named successor node names, for route


@dataclass(frozen=True)
class Setting:
    """One value a plugin needs from the person running it, declared in
    ``plugin.toml`` as a ``[[setting]]`` table
    (`docs/tasks/PLUGIN-CONFIG-01-settings-and-secrets-a-plugin-owns/spec.md`).

    ``secret`` is the only behavioural field: it decides whether a prompt
    echoes and whether the value may ever be shown back. It does *not*
    decide where the value is stored — both kinds resolve from the same
    namespaced environment variable, and spec.md's Concerns records why,
    and that every ``secret=False`` setting is the set to move once a real
    config file exists.

    Deliberately carries no default value. A default would have to be
    *injected* at run time, which means either mutating the process
    environment or putting the value back into the run's data channel —
    the one thing this design exists to avoid."""

    name: str
    purpose: str
    secret: bool


@dataclass(frozen=True)
class Manifest:
    """A plugin's own description of itself, parsed from ``plugin.toml``."""

    name: str
    version: str
    description: str
    entries: tuple[Entry, ...]
    nodes: tuple[Node, ...]
    # Trailing and defaulted on purpose: every existing construction of a
    # ``Manifest`` — in ``src/``, in the editor's ``replace()`` calls, and in
    # the suite — is positional-or-keyword over the five fields above, and a
    # field inserted anywhere but the end would silently re-bind them.
    settings: tuple[Setting, ...] = ()


def setting_env_var(plugin: str, name: str) -> str:
    """The one place the naming rule for a plugin setting is written down.

    The separator between the plugin's name and the setting's is *doubled*,
    and neither name may contain a doubled underscore: a plugin name's
    hyphens become single underscores and a setting name may carry single
    underscores, so a single separator would let plugin "a-b" setting "c"
    and plugin "a" setting "b_c" resolve to the same variable — one plugin
    reading another's value.

    Both names must already have passed their pattern — ``validate()``
    refuses a manifest whose names do not, so a caller reaching here with a
    bad one has skipped validation, which is a bug in the caller and not a
    lookup that should quietly return nothing."""
    assert PLUGIN_NAME_RE.fullmatch(plugin), f"{plugin!r} is not a valid plugin name"
    assert SETTING_NAME_RE.fullmatch(name), f"{name!r} is not a valid setting name"
    return f"SADANA_PLUGIN__{plugin.replace('-', '_').upper()}__{name.upper()}"


def read_setting(plugin: str, name: str) -> str | None:
    """What a node body calls to read one of its own plugin's settings.

    ``None`` when unset *and* when set to the empty string: a blank line in
    ``.env`` is a value nobody supplied, and treating the two alike is what
    stops ``missing_settings`` below being defeated by one.

    This is the only channel by which a value reaches a running plugin. It
    is deliberately not ``arguments`` and not the walk's threaded value —
    nothing the model writes can reach it, and nothing it returns can end up
    in a ``DagResult``, a ``NodeTrace`` or a recorded run by accident."""
    return os.environ.get(setting_env_var(plugin, name)) or None


def missing_settings(manifest: Manifest) -> tuple[str, ...]:
    """The declared settings with no value, in declaration order.

    Derived on every call, never stored: reading it fresh is what makes
    "set it, then run again" work without restarting anything."""
    return tuple(s.name for s in manifest.settings if read_setting(manifest.name, s.name) is None)


def manifest_to_dict(manifest: Manifest) -> dict[str, object]:
    """``manifest`` as JSON-safe primitives — the plugin's own declared
    name, version, description, entries and node graph, exactly as
    `plugin_blueprint.md §3.5` describes ("boxes and arrows... displayable
    without executing the plugin"). A direct field walk, not a new
    vocabulary: a `Manifest` already is that data, so this is the whole
    function."""
    return {
        "name": manifest.name,
        "version": manifest.version,
        "description": manifest.description,
        "entries": [
            {"tool": e.tool, "purpose": e.purpose, "parameters": e.parameters, "start": e.start}
            for e in manifest.entries
        ],
        "nodes": [
            {
                "name": n.name,
                "kind": n.kind,
                "body": n.body,
                "skill": n.skill,
                "next": n.next,
                "ports": list(n.ports),
            }
            for n in manifest.nodes
        ],
        "settings": [{"name": s.name, "purpose": s.purpose, "secret": s.secret} for s in manifest.settings],
    }


def _toml_string(value: str) -> str:
    """One TOML basic string, or ``ValueError`` if TOML cannot carry the
    text at all.

    Most of the escaping is ``json.dumps``'s job: TOML basic strings and
    JSON strings agree on ``"``, ``\\`` and every control character below
    U+0020, which both spell ``\\uXXXX``. Two places they do not agree, both
    found by a self-check that emitted them and watched ``tomllib`` refuse
    what came back:

    * **U+007F.** JSON escapes nothing at or above U+0020, so ``json.dumps``
      passes DEL through raw; TOML forbids it raw in a basic string. Escaped
      here by hand, because a plugin whose description held one produced a
      ``plugin.toml`` nothing could reopen.
    * **A lone surrogate.** ``JSON.stringify`` in a browser emits one
      happily — half a pasted emoji, or a ``slice()`` through an astral
      character — and it is not a Unicode scalar, so neither TOML nor UTF-8
      can carry it. Rejected rather than mangled.

    ``ensure_ascii=False`` is load-bearing rather than cosmetic. With the
    default, ``json.dumps`` spells a non-BMP character — an emoji in a
    plugin's description — as a surrogate pair, ``\\ud83d\\ude00``, and TOML
    accepts neither half as a valid scalar. Emitting the character
    literally, which TOML allows in a basic string, avoids the question."""
    try:
        value.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise ValueError(f"{value!r} contains a character TOML cannot carry (a lone surrogate)") from exc
    return json.dumps(value, ensure_ascii=False).replace("\x7f", "\\u007F")


def manifest_to_toml(manifest: Manifest) -> str:
    """``manifest`` as the ``plugin.toml`` a programmer would have written —
    the inverse of ``_parse_manifest`` above, and the first code in this
    project that writes a plugin file rather than reading one
    (`docs/tasks/PLUGIN-EDITOR-01-draw-wire-and-save/spec.md`).

    Hand-rolled rather than a TOML-writing dependency: the shape is three
    string fields and two arrays of tables, with no dates, no numbers and no
    nesting, and its correctness is pinned by a round-trip test over every
    real fixture plugin rather than by trust. A field that is ``None`` (or an
    empty ``ports``) is omitted entirely, which is what makes the round trip
    exact — ``_parse_manifest`` reads those same fields with ``.get()`` and
    produces ``None``/``()`` for an absent one."""
    lines = [
        "[plugin]",
        f"name = {_toml_string(manifest.name)}",
        f"version = {_toml_string(manifest.version)}",
        f"description = {_toml_string(manifest.description)}",
    ]
    for entry in manifest.entries:
        lines += [
            "",
            "[[entry]]",
            f"tool = {_toml_string(entry.tool)}",
            f"purpose = {_toml_string(entry.purpose)}",
            f"parameters = {_toml_string(entry.parameters)}",
            f"start = {_toml_string(entry.start)}",
        ]
    for node in manifest.nodes:
        lines += ["", "[[node]]", f"name = {_toml_string(node.name)}", f"kind = {_toml_string(node.kind)}"]
        for field, value in (("body", node.body), ("skill", node.skill), ("next", node.next)):
            if value is not None:
                lines.append(f"{field} = {_toml_string(value)}")
        if node.ports:
            lines.append("ports = [" + ", ".join(_toml_string(p) for p in node.ports) + "]")
    for setting in manifest.settings:
        lines += [
            "",
            "[[setting]]",
            f"name = {_toml_string(setting.name)}",
            f"purpose = {_toml_string(setting.purpose)}",
            f"secret = {'true' if setting.secret else 'false'}",
        ]
    return "\n".join(lines) + "\n"


def _require_str(data: dict, field: str, where: str) -> str:
    value = data.get(field)
    if not isinstance(value, str):
        raise ValueError(f"{where}: {field!r} must be a string, got {type(value).__name__}")
    return value


def _optional_str(data: dict, field: str, where: str) -> str | None:
    value = data.get(field)
    if value is None or isinstance(value, str):
        return value
    raise ValueError(f"{where}: {field!r} must be a string or absent, got {type(value).__name__}")


def manifest_from_dict(data: dict) -> Manifest:
    """The inverse of ``manifest_to_dict`` — what a browser posts back turned
    into a real ``Manifest`` (`docs/tasks/PLUGIN-EDITOR-01-draw-wire-and-save
    /spec.md`).

    Raises ``ValueError`` naming the offending field, never a bare
    ``KeyError``/``TypeError`` from an indexing accident: the one caller is an
    HTTP endpoint that has to turn this into an answer a person can act on.
    ``kind`` is checked against ``NODE_KINDS`` here because this is the only
    door through which a node kind arrives from outside a hand-written file —
    ``_parse_manifest`` trusts what a programmer typed, and nothing
    downstream re-checks it."""
    if not isinstance(data, dict):
        raise ValueError(f"manifest must be an object, got {type(data).__name__}")
    entries_raw = data.get("entries", [])
    nodes_raw = data.get("nodes", [])
    if not isinstance(entries_raw, list) or not isinstance(nodes_raw, list):
        raise ValueError("'entries' and 'nodes' must both be lists")

    entries = []
    for index, raw in enumerate(entries_raw):
        where = f"entry {index}"
        if not isinstance(raw, dict):
            raise ValueError(f"{where}: must be an object, got {type(raw).__name__}")
        entries.append(
            Entry(
                tool=_require_str(raw, "tool", where),
                purpose=_require_str(raw, "purpose", where),
                parameters=_require_str(raw, "parameters", where),
                start=_require_str(raw, "start", where),
            )
        )

    nodes = []
    for index, raw in enumerate(nodes_raw):
        where = f"node {index}"
        if not isinstance(raw, dict):
            raise ValueError(f"{where}: must be an object, got {type(raw).__name__}")
        name = _require_str(raw, "name", where)
        kind = _require_str(raw, "kind", f"node {name!r}")
        if kind not in NODE_KINDS:
            raise ValueError(f"node {name!r}: {kind!r} is not one of {', '.join(NODE_KINDS)}")
        ports = raw.get("ports", [])
        if not isinstance(ports, list) or not all(isinstance(p, str) for p in ports):
            raise ValueError(f"node {name!r}: 'ports' must be a list of strings")
        nodes.append(
            Node(
                name=name,
                kind=kind,  # type: ignore[arg-type]  # checked against NODE_KINDS above
                body=_optional_str(raw, "body", f"node {name!r}"),
                skill=_optional_str(raw, "skill", f"node {name!r}"),
                next=_optional_str(raw, "next", f"node {name!r}"),
                ports=tuple(ports),
            )
        )

    settings_raw = data.get("settings", [])
    if not isinstance(settings_raw, list):
        raise ValueError("'settings' must be a list")
    settings = []
    for index, raw in enumerate(settings_raw):
        where = f"setting {index}"
        if not isinstance(raw, dict):
            raise ValueError(f"{where}: must be an object, got {type(raw).__name__}")
        name = _require_str(raw, "name", where)
        secret = raw.get("secret")
        if not isinstance(secret, bool):
            raise ValueError(f"setting {name!r}: 'secret' must be true or false")
        settings.append(Setting(name=name, purpose=_require_str(raw, "purpose", f"setting {name!r}"), secret=secret))

    return Manifest(
        name=_require_str(data, "name", "plugin"),
        version=_require_str(data, "version", "plugin"),
        description=_require_str(data, "description", "plugin"),
        entries=tuple(entries),
        nodes=tuple(nodes),
        settings=tuple(settings),
    )


@dataclass(frozen=True)
class Valid:
    """``plugin.toml`` parsed and every `§8` check held."""

    manifest: Manifest


@dataclass(frozen=True)
class ManifestParseError:
    """``plugin.toml`` itself doesn't parse."""

    detail: str


@dataclass(frozen=True)
class InvalidSchema:
    """An entry's ``parameters`` file is missing or isn't valid JSON Schema."""

    entry: str
    path: str
    detail: str


@dataclass(frozen=True)
class UnresolvedSkill:
    """An ``ask`` node's ``skill`` doesn't resolve to a real SKILL.md."""

    node: str
    detail: str


@dataclass(frozen=True)
class DuplicateNodeName:
    """Two nodes declare the same ``name``."""

    name: str


@dataclass(frozen=True)
class DanglingTarget:
    """A ``next``, ``ports``, or an entry's ``start`` value names a node
    that isn't declared. ``node`` is the declaring node's name, or an
    entry's ``tool`` name when ``target`` came from ``Entry.start``."""

    node: str
    target: str


@dataclass(frozen=True)
class UnresolvedBody:
    """A ``body`` reference doesn't resolve to a callable in ``init.py``."""

    node: str
    body: str


@dataclass(frozen=True)
class UnreachableNode:
    """A declared node no entry point or edge ever reaches."""

    node: str


@dataclass(frozen=True)
class CyclicGraph:
    """The declared graph loops back on itself. ``node`` names one node on
    the cycle, not the whole cycle — enough to point a plugin author at it.

    D2's own reachability check (``_first_unreachable``) proves every node
    can be reached from an entry; it does not prove the graph is
    acyclic, despite every design document naming this structure a DAG.
    Added at D3's own Deploy-stage review, once ``run_graph`` (D3's own
    graph walker) made the gap a real one: a route back-edge makes a walk
    that has nowhere else to end run forever. Checked here, at load time,
    rather than bounded at walk time — an acyclic graph is bounded by
    construction (at most one visit per node before a walk ends), so
    catching the cause here means the walker needs no separate ceiling of
    its own to guard the same scenario."""

    node: str


@dataclass(frozen=True)
class InvalidPluginName:
    """The plugin's own name is not one ``PLUGIN_NAME_RE`` allows.

    Wider than the work item that added it: a plugin's name already became
    a filesystem path long before it became an environment variable
    identifier, and nothing in this tree checked it. Caught here because
    install, the marketplace's vetting, the editor's save and
    ``discover_plugins`` all already route through ``validate()``."""

    name: str


@dataclass(frozen=True)
class InvalidSettingName:
    """A ``[[setting]]``'s name is not one ``SETTING_NAME_RE`` allows."""

    setting: str


@dataclass(frozen=True)
class DuplicateSettingName:
    """Two ``[[setting]]`` tables declare the same name — one of them would
    silently win, and which one is an accident of file order."""

    name: str


ManifestOutcome = (
    Valid
    | ManifestParseError
    | InvalidSchema
    | UnresolvedSkill
    | DuplicateNodeName
    | DanglingTarget
    | UnresolvedBody
    | UnreachableNode
    | CyclicGraph
    | InvalidPluginName
    | InvalidSettingName
    | DuplicateSettingName
)


def describe_manifest_outcome(outcome: ManifestOutcome) -> str:
    """A one-line, human-readable account of any non-`Valid`
    `ManifestOutcome` — lives beside the type family it describes rather
    than at whichever caller first needed prose for it (PLUGIN-MARKET-01's
    own reviewer/creator-facing rejection reason), so a second future
    caller needing the same words doesn't re-derive this match."""
    match outcome:
        case ManifestParseError(detail=detail):
            return f"plugin.toml does not parse: {detail}"
        case InvalidSchema(entry=entry, path=path, detail=detail):
            return f"entry {entry!r}'s schema at {path} is invalid: {detail}"
        case UnresolvedSkill(node=node, detail=detail):
            return f"node {node!r}: {detail}"
        case DuplicateNodeName(name=name):
            return f"duplicate node name {name!r}"
        case DanglingTarget(node=node, target=target):
            return f"node {node!r} targets undeclared node {target!r}"
        case UnresolvedBody(node=node, body=body):
            return f"node {node!r}'s body {body!r} does not resolve"
        case UnreachableNode(node=node):
            return f"node {node!r} is never reached from any entry"
        case CyclicGraph(node=node):
            return f"the graph cycles back through node {node!r}"
        case InvalidPluginName(name=name):
            return f"plugin name {name!r} is not lowercase letters, digits and single hyphens"
        case InvalidSettingName(setting=setting):
            return f"setting name {setting!r} is not lowercase letters, digits and single underscores"
        case DuplicateSettingName(name=name):
            return f"duplicate setting name {name!r}"
        case _:  # pragma: no cover - Valid has no rejection reason; ManifestOutcome is exhausted above
            return "plugin.toml does not validate"


def _require_bool(data: dict, field: str) -> bool:
    """``TypeError`` for anything but a real boolean, which
    ``plugin_manifest.validate``'s existing ``except`` turns into a
    ``ManifestParseError``. Without it a string in that field validates, and the
    plugin then cannot be saved in the editor — ``manifest_from_dict``
    refuses the same value on the way back, an error about a field the
    editor has no control over."""
    value = data[field]
    if not isinstance(value, bool):
        raise TypeError(f"{field!r} must be true or false, got {type(value).__name__}")
    return value


def _parse_manifest(text: str) -> Manifest:
    """Raises on any shape the caller should treat as a parse failure —
    malformed TOML, or a required field missing — never on a well-formed
    but semantically wrong manifest, which ``plugin_manifest.validate()``'s
    later checks catch."""
    data = tomllib.loads(text)
    plugin_meta = data["plugin"]
    entries = tuple(
        Entry(tool=e["tool"], purpose=e["purpose"], parameters=e["parameters"], start=e["start"])
        for e in data.get("entry", [])
    )
    nodes = tuple(
        Node(
            name=n["name"],
            kind=n["kind"],
            body=n.get("body"),
            skill=n.get("skill"),
            next=n.get("next"),
            ports=tuple(n.get("ports", [])),
        )
        for n in data.get("node", [])
    )
    settings = tuple(
        Setting(name=s["name"], purpose=s["purpose"], secret=_require_bool(s, "secret"))
        for s in data.get("setting", [])
    )
    return Manifest(
        name=plugin_meta["name"],
        version=plugin_meta["version"],
        description=plugin_meta["description"],
        entries=entries,
        nodes=nodes,
        settings=settings,
    )


def _node_index(manifest: Manifest) -> dict[str, Node]:
    """``manifest``'s own nodes, keyed by name — built fresh from whatever
    ``manifest`` is handed, never cached, since a second caller with a
    different ``Manifest`` value must never see a stale index. Shared by
    ``_first_unreachable`` (below) and ``plugin_manifest.run_graph`` so
    there is one way to build this lookup, not two."""
    return {node.name: node for node in manifest.nodes}


def _successors(node: Node) -> list[str]:
    """Every node name an edge leads from ``node`` to — its ``next``, then
    its named ports. One definition of "what does this node point at",
    shared by the reachability walk, the cycle walk and
    ``editor_layout.positions`` rather than spelled out again at each."""
    return ([node.next] if node.next is not None else []) + list(node.ports)


def rename_node(manifest: Manifest, old: str, new: str) -> Manifest:
    """``manifest`` with one node renamed and every reference to it
    followed — its successors' arrows, any route port naming it, and any
    entry starting at it.

    Pure, and in Python rather than in the editor's JavaScript, because a
    rename is a graph rewrite: miss one of those three places and a person
    drawing a plugin silently gets a dangling arrow. CLAUDE.md's rule that a
    browser surface holds no logic that can be held in Python is exactly
    about this — nothing in this repository can test JavaScript."""
    if not new:
        raise ValueError("a step needs a name")
    index = _node_index(manifest)
    if old not in index:
        raise ValueError(f"no step named {old!r}")
    if new != old and new in index:
        raise ValueError(f"a step named {new!r} already exists")
    swap = lambda name: new if name == old else name  # noqa: E731 - one expression, three call sites below
    # ``replace`` on the manifest, never a field-by-field rebuild: this
    # function and ``remove_node`` below each used to name all five fields,
    # which silently dropped ``settings`` the moment a sixth was added and
    # nothing failed until the person's next run. ``add_node`` already had
    # the right shape; these two now match it, so the next field added to
    # ``Manifest`` is carried through here without anyone remembering to.
    return replace(
        manifest,
        entries=tuple(replace(entry, start=swap(entry.start)) for entry in manifest.entries),
        nodes=tuple(
            replace(
                node,
                name=swap(node.name),
                next=None if node.next is None else swap(node.next),
                ports=tuple(swap(port) for port in node.ports),
            )
            for node in manifest.nodes
        ),
    )


def remove_node(manifest: Manifest, name: str) -> Manifest:
    """``manifest`` without ``name``, and without any arrow that pointed at
    it — an arrow to a deleted step would be a dangling reference the person
    never drew. An entry that started there is left pointing at the first
    remaining step, or at nothing if none remain."""
    index = _node_index(manifest)
    if name not in index:
        raise ValueError(f"no step named {name!r}")
    kept = tuple(
        replace(
            node,
            next=None if node.next == name else node.next,
            ports=tuple(port for port in node.ports if port != name),
        )
        for node in manifest.nodes
        if node.name != name
    )
    fallback = kept[0].name if kept else ""
    return replace(
        manifest,
        entries=tuple(replace(e, start=fallback if e.start == name else e.start) for e in manifest.entries),
        nodes=kept,
    )


def add_node(manifest: Manifest, kind: str) -> tuple[Manifest, str]:
    """``manifest`` with one more step of ``kind``, and the name it was
    given — the lowest ``step_N`` nothing else is using, so a person adding
    boxes never has to think about naming one before they know what it
    does."""
    if kind not in NODE_KINDS:
        raise ValueError(f"{kind!r} is not one of {', '.join(NODE_KINDS)}")
    taken = _node_index(manifest)
    number = 1
    while f"step_{number}" in taken:
        number += 1
    name = f"step_{number}"
    return replace(manifest, nodes=(*manifest.nodes, Node(name=name, kind=kind))), name  # type: ignore[arg-type]


def _first_unreachable(manifest: Manifest) -> UnreachableNode | None:
    """A breadth-first walk of ``manifest``'s own declared edges, from
    every entry's ``start`` — reads the graph's shape, runs nothing. Every
    ``entry.start`` is already known to name a real node by the time this
    runs — ``plugin_manifest.validate()``'s own dangling-target check
    holds first, same as every other check in its sequence assuming the
    one before it held."""
    by_name = _node_index(manifest)
    visited: set[str] = set()
    queue: deque[str] = deque(entry.start for entry in manifest.entries)
    while queue:
        name = queue.popleft()
        if name in visited:
            continue
        visited.add(name)
        queue.extend(_successors(by_name[name]))
    for node in manifest.nodes:
        if node.name not in visited:
            return UnreachableNode(node=node.name)
    return None


def _first_cycle(manifest: Manifest) -> CyclicGraph | None:
    """A depth-first walk from every entry's ``start``, tracking the
    current path — reaching a node already on that path is a back edge,
    the definition of a cycle. Reads the graph's shape only, runs nothing;
    independent of ``_first_unreachable`` (a cycle can exist among
    reachable nodes, as it does in `plugin_blueprint.md`'s own worry about
    plugin composition, §12 OQ2 — this checks one plugin's own graph, not
    across plugins)."""
    by_name = _node_index(manifest)
    visited: set[str] = set()
    on_path: set[str] = set()

    def visit(name: str) -> CyclicGraph | None:
        if name in on_path:
            return CyclicGraph(node=name)
        if name in visited:
            return None
        visited.add(name)
        on_path.add(name)
        for successor in _successors(by_name[name]):
            outcome = visit(successor)
            if outcome is not None:
                return outcome
        on_path.discard(name)
        return None

    for entry in manifest.entries:
        outcome = visit(entry.start)
        if outcome is not None:
            return outcome
    return None


# ── D3: catalog and tool surface, and the graph's execution seams ────────
# Its full contract is `docs/tasks/D3-graph-dispatch/spec.md`.


@dataclass(frozen=True)
class InstalledPlugin:
    """One plugins-root subdirectory that validated cleanly, kept around so
    a dispatch call doesn't re-read plugin.toml on every tool call — a
    plugin is immutable and external (`plugin_blueprint.md §3.1`); nothing
    in this project can change one mid-conversation, so re-validating per
    call would be pure waste, not extra safety."""

    name: str
    directory: Path
    manifest: Manifest


# The one seam this module and plugin_manifest.py expose for "consult a
# model" — neither may import conversation.py (spec.md's import-direction
# invariant), so neither can call run_child directly. None means the
# sub-task didn't finish cleanly; a str is what it reported back.
AskFn = Callable[[SkillRef, str], Awaitable[str | None]]

# Gates a `call` node before its body runs (`docs/tasks/F1-call-node-approval
# /spec.md`). Args: plugin name, node name, the value about to reach it.
# True lets the walk proceed; False ends the run at that node. `ask`/
# `compute`/`route`/`stop` never call this — one kind, not every body.
ApproveFn = Callable[[str, str, object], Awaitable[bool]]
