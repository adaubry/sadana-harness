"""Ties GATEWAY-DAEMON's own `MessageEvent` to CONVERSATION's turn machinery.

`docs/tasks/GATEWAY-DAEMON-01-daemon-and-webhook-channel/spec.md`. The one
module allowed to import both `gateway.py` and
`conversation.py`/`conversation_store.py`/`plugin_dispatch.py` — the same
role `plugin_dispatch.py` already plays between PLUGINS and CONVERSATION.

`handle_inbound()` is the entire bridge: load-or-create the conversation
`session_key_for(event)` names, run one turn with `event.text`, persist it,
return the reply. It is a direct copy of `subcommands/chat.py`'s own
`_chat_loop` per-turn body plus `cmd_chat`'s get-or-create branch — driven by
catching `ConversationNotFound` instead of an explicit `--resume` flag, since
a webhook payload carries no such flag.

`conn_lock` serializes every call: `conversation_store.open_store()`'s own
docstring states its connection is safe only "by one thread at a time,
different call to call," but a channel adapter built on `ThreadingHTTPServer`
(`channel_webhook.py`) hands each inbound request its own thread. This
module is spec.md's own designated single bridge every channel event routes
through, so the lock lives here — once, at the one chokepoint that actually
owns `conn` for the turn — rather than being re-derived independently by
every caller that happens to serve requests on multiple threads. Public, not
`_conn_lock`, since GATEWAY-DAEMON-02 (`docs/tasks/GATEWAY-DAEMON-02-scheduled
-and-resumable-triggers/review.md`) made `scheduling.py`'s own background
tick thread a second real cross-module caller that must serialize against
this same `conn` — a deploy-stage cold review caught the tick loop touching
`conn` with no lock at all, the exact "background/scheduled task writing
alongside a live turn" scenario CLAUDE.md's own connection-safety rule
names.
"""

from __future__ import annotations

import asyncio
import sqlite3
import threading
import time
from dataclasses import replace

from sadana import conversation_store, memory, memory_store, observability, plugin_dispatch, plugins
from sadana.conversation import (
    ConversationTemplate,
    ExitReason,
    Message,
    TemplateRecipe,
    TurnKey,
    append,
    create_conversation,
    iteration_budget_from_config,
    wall_clock_budget_from_config,
)
from sadana.gateway import MessageEvent, session_key_for

conn_lock = threading.Lock()


async def handle_inbound(
    conn: sqlite3.Connection,
    event: MessageEvent,
    *,
    plugin_set: plugin_dispatch.PluginSet,
    persona: str,
    provider: str,
    model: str,
    record_turn: observability.RecordTurnFn = observability.noop_record,
    record_plugin_run: observability.RecordPluginRunFn = observability.noop_record,
) -> tuple[bool, str]:
    """Load-or-create the conversation `session_key_for(event)` names, run
    one turn with `event.text` as the user input, persist it, and return
    `(ok, text)` — `ok` is `result.exit_reason == ExitReason.COMPLETED`;
    `text` is `final_text` on a clean turn, or the same `[exit_reason]
    detail` shape `_chat_loop` prints on any other one. Never raises for an
    expected turn outcome.

    Deviates from spec.md's Interface pseudocode (`-> str`): `ok` cannot be
    recovered from `text` alone after the fact, since `final_text` can be
    non-`None` even when `exit_reason != COMPLETED` (chat.py's own
    `BUDGET_EXHAUSTED` comment) — so the caller needs both values, not one
    string to re-parse.

    `record_turn`/`record_plugin_run` (OBSERVABILITY-01) default to a
    no-op; `cmd_gateway_run` builds a real `Recorder` once, outside this
    per-message call, and passes its two callables through — `conn` is
    already this call's own parameter and every inbound message shares the
    same connection, so there is nothing to build here that would differ
    call to call.

    Serialized by `conn_lock` — see the module docstring. `conn`'s
    `memory_store` schema is assumed already present, ensured once by
    `cmd_gateway_run` before the daemon starts serving — not re-checked
    here on every inbound message, the same "ensure once, not per-turn"
    posture `cmd_chat` already takes for the same tables. A caller that
    calls `handle_inbound` directly (this module's own tests) is
    responsible for that setup itself, matching how those same tests
    already call `observability.make_recorder(conn)` themselves rather
    than relying on `handle_inbound` to do it.

    A conversation with an outstanding pause takes a different path
    entirely (`docs/tasks/GATEWAY-DAEMON-02-scheduled-and-resumable-triggers
    /spec.md`): `event.text` resumes the paused plugin run directly — the
    model is never called, no turn happens, and the resumed result's own
    text is appended to history as one new `assistant` message rather than
    rendered as a tool result (the triggering turn's own tool-result
    message, naming the pause, was already appended and that turn already
    completed when the pause first happened — nothing here is still
    waiting on a tool call). Every inbound event for a conversation with no
    pause row is byte-for-byte today's existing behavior, unchanged below
    this check — except that its own `build_dispatch()` call now also
    passes `persist_pause`, a closure over this call's own `conn`/`key`
    (`conversation_store.save_pause_from_result`) — the one place a run's
    *first* pause gets persisted, called by `dispatch()` itself the moment
    a `run_graph` result comes back with `paused_node` set, the same point
    `record_plugin_run` already reads that same result."""
    with conn_lock:
        key = session_key_for(event)
        pause = conversation_store.load_pause(conn, conversation_key=key)
        if pause is not None:
            now = time.monotonic()
            pause_result = await plugin_dispatch.resume_paused_run(conn, key, event.text)
            conversation = conversation_store.load(conn, key, now=now)
            messages, _msg_key = append(
                key, conversation.messages, Message(role="assistant", content=pause_result.text)
            )
            conversation = replace(conversation, messages=messages)
            await asyncio.to_thread(conversation_store.save, conn, conversation, now=now)
            return pause_result.failed_node is None, pause_result.text

        account_key = memory.account_key_for(event.platform, event.chat_id)
        now = time.monotonic()
        try:
            conversation = conversation_store.load(conn, key, now=now)
        except conversation_store.ConversationNotFound:
            template = ConversationTemplate(
                name="webhook",
                recipe=TemplateRecipe(
                    stable_prompt=persona, catalog=plugin_set.catalog, tool_specs=plugin_set.tool_specs
                ),
            )
            entries = memory_store.list_entries(conn, account_key)
            override = memory_store.get_rubric_override(conn, account_key)
            system_message = memory.system_message_for(entries, memory.default_rubric(), override)
            conversation, _template = create_conversation(
                template,
                key,
                system_message=system_message,
                iteration_budget=iteration_budget_from_config(),
                wall_clock_budget=wall_clock_budget_from_config(now),
            )
            conversation_store.create(conn, conversation, now=now)

        async def persist_pause(turn_key: TurnKey, seq_in_turn: int, result: plugins.DagResult) -> None:
            conversation_store.save_pause_from_result(
                conn,
                conversation_key=key,
                result=result,
                turn_seq=turn_key.turn_seq,
                seq_in_turn=seq_in_turn,
            )

        dispatch, tracker = plugin_dispatch.build_dispatch(
            conversation,
            plugin_set,
            stable_prompt=persona,
            provider=provider,
            model=model,
            now=now,
            record_turn=record_turn,
            record_plugin_run=record_plugin_run,
            memory_context=memory_store.DispatchContext(account_key=account_key, conn=conn),
            persist_pause=persist_pause,
        )
        persist = conversation_store.bind_persist(conn, conversation, now=now)
        result, conversation = await plugin_dispatch.take_turn_and_reconcile(
            conversation,
            dispatch,
            tracker,
            user_input=event.text,
            provider=provider,
            model=model,
            now=now,
            persist=persist,
            record_turn=record_turn,
        )
        await asyncio.to_thread(conversation_store.save, conn, conversation, now=now)

        text = result.final_text or f"[{result.exit_reason.value}] {result.detail or ''}"
        return result.exit_reason == ExitReason.COMPLETED, text
