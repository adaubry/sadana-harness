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

from dataclasses import dataclass
from typing import Literal


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
    kind: Literal["compute", "ask", "route", "stop", "call", "each", "wait"]
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
