"""Ties PLUGINS' pure/I-O halves to CONVERSATION's run_child.

`docs/tasks/D3-graph-dispatch/spec.md`. The one module allowed to import
both `conversation.py` and `plugins.py`/`plugin_manifest.py` — neither of
those may import `conversation.py` (CLAUDE.md: "plugins.py and
plugin_manifest.py never import conversation.py"), and an `ask` node needs
`run_child`, so something has to sit on the other side of that boundary.
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable, Iterable, Mapping
from dataclasses import dataclass, replace

from sadana import observability, plugin_manifest, plugins
from sadana.conversation import (
    ChildSpec,
    Conversation,
    ExitReason,
    Message,
    PluginCatalogEntry,
    ResolvedNames,
    ToolSpec,
    TurnResult,
    _noop_persist,
    run_child,
)
from sadana.conversation import take_turn as _take_turn

DispatchFn = Callable[[str, dict], Awaitable[plugins.DagResult]]


def capturing_dispatch(dispatch: DispatchFn) -> tuple[DispatchFn, list[plugins.DagResult]]:
    """Wraps one turn's own dispatch, capturing what it returns before
    `run_turn` discards everything but `.text`
    (`docs/tasks/G3-real-plugin-under-eval/spec.md`). The one shared
    implementation of a technique this project proved three times
    independently before landing here — `scripts/prove_plugin_dispatch_e2e.py`'s
    own `_make_capturing_dispatch` first, `eval_harness.run_task` and
    `scripts/prove_conversation_e2e.py` next. A fresh `captured` list per
    call, never shared across turns."""
    captured: list[plugins.DagResult] = []

    async def wrapped(name: str, arguments: dict) -> plugins.DagResult:
        result = await dispatch(name, arguments)
        captured.append(result)
        return result

    return wrapped, captured


@dataclass(frozen=True)
class PluginSet:
    """Everything a template's recipe and a real dispatch call both need,
    derived once from a scan of installed, validated plugins. Rebuilt from
    scratch, never patched — the same posture create_conversation() already
    takes toward a ConversationTemplate's recipe."""

    catalog: tuple[PluginCatalogEntry, ...]
    tool_specs: tuple[ToolSpec, ...]
    by_tool: Mapping[str, tuple[plugins.InstalledPlugin, plugins.Entry]]


def _describe(purpose: str) -> Callable[[ResolvedNames], str]:
    # A nested def, not a bare lambda in the loop below: `purpose` is this
    # call's own parameter, so two entries in one build_plugin_set() call
    # never end up sharing one closure's late-bound loop variable.
    def describe(_resolved: ResolvedNames) -> str:
        return purpose

    return describe


def build_plugin_set(installed: Iterable[plugins.InstalledPlugin]) -> PluginSet:
    """One `PluginCatalogEntry` and one `ToolSpec` per `Entry` across every
    given plugin (`plugin_blueprint.md §8`). `entry.tool` is used as both a
    `ToolSpec`'s `key` and `name` (matching
    `scripts/prove_conversation_e2e.py`'s own convention); `entry.purpose`
    is rendered verbatim as `describe`'s output — a plugin's own sentence
    has no other tool's name inside it to cross-reference. `entry.parameters`
    names a schema file relative to the plugin's own root; it is read here
    trusting it is already valid JSON Schema, because every ``installed``
    plugin already passed that exact check in ``validate()`` to become an
    ``InstalledPlugin`` at all.

    Two entries sharing a tool name are not checked here: `by_tool`'s dict
    construction lets a later one silently win, but `tool_specs` is still
    built unconditionally from every entry, so the same collision always
    also produces two `ToolSpec`s sharing one `name` — and
    `conversation.build_surface` already raises `DuplicateToolError` on
    exactly that, the moment a template's recipe becomes a real
    conversation, before any dispatch call could ever observe `by_tool`'s
    version of events."""
    catalog = []
    tool_specs = []
    by_tool: dict[str, tuple[plugins.InstalledPlugin, plugins.Entry]] = {}
    for plugin in installed:
        for entry in plugin.manifest.entries:
            catalog.append(PluginCatalogEntry(name=plugin.manifest.name, purpose=entry.purpose, entry_tool=entry.tool))
            schema = json.loads((plugin.directory / entry.parameters).read_text(encoding="utf-8"))
            tool_specs.append(
                ToolSpec(key=entry.tool, name=entry.tool, parameters=schema, describe=_describe(entry.purpose))
            )
            by_tool[entry.tool] = (plugin, entry)
    return PluginSet(catalog=tuple(catalog), tool_specs=tuple(tool_specs), by_tool=by_tool)


@dataclass
class ChildSeqTracker:
    """The one piece of mutable state this item introduces.
    ``run_turn``'s ``dispatch`` contract gives a handler no channel to
    report ``run_child``'s own bookkeeping (``next_child_seq`` advancing)
    back to whoever built the closure —
    ``docs/reference/dispatch_closure_state_bug.md``. Scoped to exactly one
    dispatch closure's lifetime; the caller must reconcile ``next_seq``
    onto its own ``Conversation`` after every ``take_turn()`` call (see
    ``build_dispatch``'s docstring)."""

    next_seq: int


def build_dispatch(
    conversation: Conversation,
    plugin_set: PluginSet,
    *,
    stable_prompt: str,
    provider: str,
    model: str,
    now: float,
    persist: Callable[[tuple[Message, ...]], Awaitable[None]] = _noop_persist,
    approve: plugins.ApproveFn = plugin_manifest._default_approve,
    record_turn: observability.RecordTurnFn = observability.noop_record,
    record_plugin_run: observability.RecordPluginRunFn = observability.noop_record,
) -> tuple[DispatchFn, ChildSeqTracker]:
    """Builds one dispatch closure matching `conversation.py`'s own
    `dispatch` contract exactly, and the `ChildSeqTracker` it shares with
    its own `ask` callback.

    The closure: looks `name` up in `plugin_set.by_tool`; if it isn't
    there, returns a `DagResult` naming that (Requirement 7) rather than
    raising — the same shape `eval_harness._no_tools_dispatch` already
    uses for its own "should be unreachable" case. On a hit, it calls
    `plugin_manifest.run_graph` with an `ask` callback that spawns a
    bounded, tool-less child via `run_child` (a judgement step is pure
    judgement, `plugin_blueprint.md §3.3` — nothing in `Node` gives a
    plugin author a way to ask for more) and reports back its final text
    on a clean `ExitReason.COMPLETED`, `None` otherwise. `approve` passes
    straight through to `run_graph` for its own `call`-node gate
    (`docs/tasks/G3-real-plugin-under-eval/spec.md`) — defaulted, like
    `persist`, since no caller needed to override it until a real `call`
    node existed.

    **The caller's obligation**, since nothing enforces it structurally:
    after a `take_turn()` call made with the returned dispatch, reconcile
    before doing anything else —
    `conversation = replace(updated_conversation, next_child_seq=tracker.next_seq)`
    — and build a fresh dispatch (and tracker) from that reconciled value
    before the next turn.

    **Recording** (`docs/tasks/OBSERVABILITY-01-turn-and-plugin-run-records/spec.md`):
    `record_turn` is awaited after every `TurnResult` this closure produces
    — both `ask`'s own child turn and, via `take_turn_and_reconcile`, the
    parent turn itself — and `record_plugin_run` after every real
    `run_graph` call `dispatch` makes, keyed by this turn's own `TurnKey`
    plus a closure-local `seq_in_turn` (fresh per `build_dispatch` call,
    never shared across turns — the same posture `ChildSeqTracker` and
    `capturing_dispatch`'s own `captured` list already take). Both default
    to a no-op, so a caller that never opted in behaves exactly as before."""
    tracker = ChildSeqTracker(next_seq=conversation.next_child_seq)
    turn_key = conversation.pending_turn_key
    seq_in_turn = 0

    async def ask(skill: plugins.SkillRef, text: str) -> str | None:
        parent = replace(conversation, next_child_seq=tracker.next_seq)
        spec = ChildSpec(node_name=skill.skill, skill=skill, input=text, tools=frozenset())
        (result, _child, updated_parent), duration_s = await observability.timed(
            run_child(
                parent,
                spec,
                stable_prompt=stable_prompt,
                provider=provider,
                model=model,
                dispatch=dispatch,
                persist=persist,
                now=now,
            )
        )
        tracker.next_seq = updated_parent.next_child_seq
        await record_turn(result, duration_s)
        if result.exit_reason == ExitReason.COMPLETED and result.final_text is not None:
            return result.final_text
        return None

    async def dispatch(name: str, arguments: dict) -> plugins.DagResult:
        nonlocal seq_in_turn
        hit = plugin_set.by_tool.get(name)
        if hit is None:
            return plugins.DagResult(
                plugin="none",
                entry=name,
                text=f"tool_error: no installed plugin currently backs {name!r}",
                artifacts=(),
                trace=(),
                failed_node="entry",
            )
        installed, entry = hit
        result, duration_s = await observability.timed(
            plugin_manifest.run_graph(
                installed.directory, installed.manifest, entry, arguments, ask=ask, approve=approve
            )
        )
        await record_plugin_run(turn_key, seq_in_turn, result, duration_s)
        seq_in_turn += 1
        return result

    return dispatch, tracker


async def take_turn_and_reconcile(
    conversation: Conversation,
    dispatch: DispatchFn,
    tracker: ChildSeqTracker,
    *,
    user_input: str,
    provider: str,
    model: str,
    now: float,
    persist: Callable[[tuple[Message, ...]], Awaitable[None]] = _noop_persist,
    record_turn: observability.RecordTurnFn = observability.noop_record,
) -> tuple[TurnResult, Conversation]:
    """Self-check addition: `build_dispatch`'s own docstring named the
    reconciliation step as the caller's obligation, enforced by nothing but
    a comment. This closes it structurally instead — a caller that goes
    through this function instead of `conversation.take_turn` directly
    cannot forget the reconciliation, because there is no second step left
    to forget. Direct use of `dispatch`/`tracker` (this project's own unit
    tests, or a caller that needs `take_turn`'s other parameters) is still
    supported; this is the recommended path, not the only one.

    `record_turn` (default a no-op) is awaited with the turn's own
    `TurnResult` and its wall-clock duration once it's done —
    OBSERVABILITY-01's other emitter, alongside `build_dispatch`'s own."""
    (result, updated), duration_s = await observability.timed(
        _take_turn(
            conversation,
            user_input=user_input,
            provider=provider,
            model=model,
            dispatch=dispatch,
            persist=persist,
            now=now,
        )
    )
    await record_turn(result, duration_s)
    return result, replace(updated, next_child_seq=tracker.next_seq)
