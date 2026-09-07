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

import tomllib
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from sadana import config

# All seven node kinds `plugin_blueprint.md §6` names, shared by `Node.kind`
# (the declared graph) and `NodeTrace.kind` (a run of it) — one closed set,
# declared once.
NodeKind = Literal["compute", "ask", "route", "stop", "call", "each", "wait"]


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
    unconditionally into the tool-result message."""

    plugin: str
    entry: str
    text: str
    artifacts: tuple[Artifact, ...] = ()
    trace: tuple[NodeTrace, ...] = ()
    failed_node: str | None = None  # None == the run reached a terminal node


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
class Manifest:
    """A plugin's own description of itself, parsed from ``plugin.toml``."""

    name: str
    version: str
    description: str
    entries: tuple[Entry, ...]
    nodes: tuple[Node, ...]


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


ManifestOutcome = (
    Valid
    | ManifestParseError
    | InvalidSchema
    | UnresolvedSkill
    | DuplicateNodeName
    | DanglingTarget
    | UnresolvedBody
    | UnreachableNode
)


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
    return Manifest(
        name=plugin_meta["name"],
        version=plugin_meta["version"],
        description=plugin_meta["description"],
        entries=entries,
        nodes=nodes,
    )


def _first_unreachable(manifest: Manifest) -> UnreachableNode | None:
    """A breadth-first walk of ``manifest``'s own declared edges, from
    every entry's ``start`` — reads the graph's shape, runs nothing. Every
    ``entry.start`` is already known to name a real node by the time this
    runs — ``plugin_manifest.validate()``'s own dangling-target check
    holds first, same as every other check in its sequence assuming the
    one before it held."""
    by_name = {node.name: node for node in manifest.nodes}
    visited: set[str] = set()
    queue: deque[str] = deque(entry.start for entry in manifest.entries)
    while queue:
        name = queue.popleft()
        if name in visited:
            continue
        visited.add(name)
        node = by_name[name]
        if node.next is not None:
            queue.append(node.next)
        queue.extend(node.ports)
    for node in manifest.nodes:
        if node.name not in visited:
            return UnreachableNode(node=node.name)
    return None
