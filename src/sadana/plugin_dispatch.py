"""Ties PLUGINS' pure/I-O halves to CONVERSATION's run_child.

`docs/tasks/D3-graph-dispatch/spec.md`. The one module allowed to import
both `conversation.py` and `plugins.py`/`plugin_manifest.py` — neither of
those may import `conversation.py` (CLAUDE.md: "plugins.py and
plugin_manifest.py never import conversation.py"), and an `ask` node needs
`run_child`, so something has to sit on the other side of that boundary.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Awaitable, Callable, Iterable, Mapping
from dataclasses import dataclass, replace

from sadana import conversation_store, memory_store, observability, plugin_manifest, plugins
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


async def _noop_persist_pause(_result: plugins.DagResult) -> None:
    """The default ``persist_pause``: a caller that never opted in behaves
    exactly as before this parameter existed — same posture as
    ``conversation.py``'s own ``_noop_persist``."""
    return None


def build_dispatch(
    conversation: Conversation,
    plugin_set: PluginSet,
    *,
    provider: str,
    model: str,
    now: float,
    persist: Callable[[tuple[Message, ...]], Awaitable[None]] = _noop_persist,
    approve: plugins.ApproveFn = plugin_manifest._default_approve,
    record_turn: observability.RecordTurnFn = observability.noop_record,
    record_plugin_run: observability.RecordPluginRunFn = observability.noop_record,
    memory_context: memory_store.DispatchContext | None = None,
    persist_pause: Callable[[plugins.DagResult], Awaitable[None]] = _noop_persist_pause,
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
    to a no-op, so a caller that never opted in behaves exactly as before.

    **Memory** (`docs/tasks/MEMORY-01-write-recall-and-forget/spec.md`):
    when `memory_context` is given, every call's `arguments` gets one
    reserved key, `_sadana_memory_ctx`, merged in *last* so it always wins
    over anything the model itself supplied under that name — the only way
    a plugin's node body can learn the account it is running for without
    that identity ever being something the model controls. Defaults to
    `None`, in which case `arguments` reaches `run_graph` with only the
    session key below added.

    **Session identity** (`docs/tasks/GATEWAY-DAEMON-02-scheduled-and-resumable-triggers
    /spec.md`): every call's `arguments` also always gets
    `_sadana_session_key` (`conversation.key`), the second occurrence of
    the same reserved-key convention `_sadana_memory_ctx` established —
    now a CLAUDE.md rule. A `call` node that starts a slow external job
    reads this to tell that job where to call back once it's done; nothing
    else in this project threads a session's identity through a node
    body's own call signature.

    **Pausing** (same spec): `persist_pause` is awaited with the real
    `DagResult` right where it's produced — the same point `record_plugin_run`
    already reads it, one line below — whenever `run_graph` returns one
    with `paused_node` set. This is the *only* place a run's first pause is
    persisted; `gateway_dispatch.handle_inbound()` passes a closure over its
    own `conn`/`conversation.key` (`conversation_store.save_pause_from_result`)
    rather than reconstructing what happened after the turn is over. Defaults
    to a no-op, matching every other optional callback here."""
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
                # The conversation's own voice, not the process's: read here,
                # at the one place a child is built, so no caller can hand
                # this a prompt belonging to some other conversation
                # (CLAUDE.md, PERSONA-01's own rule).
                stable_prompt=parent.stable_prompt,
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
        call_arguments = {**arguments, "_sadana_session_key": conversation.key}
        if memory_context is not None:
            call_arguments["_sadana_memory_ctx"] = memory_context
        result, duration_s = await observability.timed(
            plugin_manifest.run_graph(
                installed.directory, installed.manifest, entry, call_arguments, ask=ask, approve=approve
            )
        )
        await record_plugin_run(turn_key, seq_in_turn, result, duration_s)
        seq_in_turn += 1
        if result.paused_node is not None:
            await persist_pause(result)
        return result

    return dispatch, tracker


async def _unsupported_ask(_skill: plugins.SkillRef, _text: str) -> str | None:
    """A resumed walk cannot yet reach a real `ask` node
    (`docs/tasks/GATEWAY-DAEMON-02-scheduled-and-resumable-triggers/spec.md`'s
    own Non-goal): supporting it would mean threading a full `Conversation`
    and `run_child` through the resume path, duplicating `build_dispatch`'s
    own `ask` closure, for a scenario nothing concrete needs yet. Always
    returns `None` — the same "child did not complete" outcome `run_graph`
    already gives any other `ask` failure, so a resumed walk that reaches
    one still ends as a clean, named `DagResult`, never a crash."""
    return None


async def resume_paused_run(
    conn: sqlite3.Connection,
    conversation_key: str,
    payload_text: str,
    *,
    approve: plugins.ApproveFn = plugin_manifest._default_approve,
) -> plugins.DagResult:
    """Continues a conversation's own outstanding `plugin_pauses` row with
    `payload_text` as the resuming answer
    (`docs/tasks/GATEWAY-DAEMON-02-scheduled-and-resumable-triggers/spec.md`).

    Caller's obligation: check `conversation_store.load_pause()` first —
    this function trusts a row exists, matching the case
    `gateway_dispatch.handle_inbound()` already established before calling
    it.

    The plugin is re-resolved fresh, by name, via `plugin_manifest.validate()`
    on exactly `pause.plugin`'s own directory — never cached across the
    pause, the same "never cached... re-validated per call would be pure
    waste, not extra safety" posture `InstalledPlugin`'s own docstring
    already takes, and this project's own names-not-pointers rule
    (`plugins.ResumeState`'s own docstring). Deliberately *not*
    `discover_plugins()`: that scans and validates every installed plugin,
    not just the one this resume needs — wasted work scaling with total
    plugin count, repeated on every resume, for a lookup that only ever
    wants one name. A plugin/entry/node that no longer resolves is a clean,
    `failed_node`-set `DagResult`, never an exception — the pause row is
    cleared either way, since any terminal result means this run is over.
    The node check matters on its own, not just as a variant of the
    plugin/entry one: a deploy-stage cold review found that a plugin
    redeployed with its paused node renamed (plugin and entry still
    resolving) reached `run_graph`'s own unguarded `by_name[resume.node]`
    and raised an uncaught `KeyError` — before this function's own
    delete/save branches ever ran, so the pause row was never cleared and
    every later message on that session key repeated the same crash. A run
    that pauses again (a second `wait` node) has its pause row overwritten
    in place, matching `save_pause()`'s own upsert shape."""
    pause = conversation_store.load_pause(conn, conversation_key=conversation_key)
    assert pause is not None, f"resume_paused_run called with no pause row for {conversation_key!r}"

    outcome = plugin_manifest.validate(plugins._plugins_root() / pause.plugin)
    entry = (
        next((e for e in outcome.manifest.entries if e.tool == pause.entry), None)
        if isinstance(outcome, plugins.Valid)
        else None
    )
    node_missing = isinstance(outcome, plugins.Valid) and pause.node not in plugins._node_index(outcome.manifest)
    if not isinstance(outcome, plugins.Valid) or entry is None or node_missing:
        conversation_store.delete_pause(conn, conversation_key=conversation_key)
        detail = "the plugin's graph no longer has this step" if node_missing else "the plugin no longer resolves"
        return plugins.DagResult(
            plugin=pause.plugin,
            entry=pause.entry,
            text=f"{pause.plugin}'s {pause.node!r} step could not resume: {detail}.",
            artifacts=pause.artifacts,
            trace=pause.trace,
            failed_node=pause.node,
        )

    resume_state = plugins.ResumeState(
        node=pause.node, value=payload_text, trace=pause.trace, artifacts=pause.artifacts
    )
    result = await plugin_manifest.run_graph(
        plugins._plugins_root() / pause.plugin,
        outcome.manifest,
        entry,
        {},
        ask=_unsupported_ask,
        approve=approve,
        resume=resume_state,
    )
    if result.paused_node is None:
        conversation_store.delete_pause(conn, conversation_key=conversation_key)
    else:
        conversation_store.save_pause_from_result(conn, conversation_key=conversation_key, result=result)
    return result


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
