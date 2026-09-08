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

`_conn_lock` serializes every call: `conversation_store.open_store()`'s own
docstring states its connection is safe only "by one thread at a time,
different call to call," but a channel adapter built on `ThreadingHTTPServer`
(`channel_webhook.py`) hands each inbound request its own thread. This
module is spec.md's own designated single bridge every channel event routes
through, so the lock lives here — once, at the one chokepoint that actually
owns `conn` for the turn — rather than being re-derived independently by
every caller that happens to serve requests on multiple threads.
"""

from __future__ import annotations

import asyncio
import sqlite3
import threading
import time

from sadana import conversation_store, plugin_dispatch
from sadana.conversation import (
    ConversationTemplate,
    ExitReason,
    TemplateRecipe,
    create_conversation,
    iteration_budget_from_config,
    wall_clock_budget_from_config,
)
from sadana.gateway import MessageEvent, session_key_for

_conn_lock = threading.Lock()


async def handle_inbound(
    conn: sqlite3.Connection,
    event: MessageEvent,
    *,
    plugin_set: plugin_dispatch.PluginSet,
    persona: str,
    provider: str,
    model: str,
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

    Serialized by `_conn_lock` — see the module docstring."""
    with _conn_lock:
        key = session_key_for(event)
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
            conversation, _template = create_conversation(
                template,
                key,
                system_message="",
                iteration_budget=iteration_budget_from_config(),
                wall_clock_budget=wall_clock_budget_from_config(now),
            )
            conversation_store.create(conn, conversation, now=now)

        dispatch, tracker = plugin_dispatch.build_dispatch(
            conversation, plugin_set, stable_prompt=persona, provider=provider, model=model, now=now
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
        )
        await asyncio.to_thread(conversation_store.save, conn, conversation, now=now)

        text = result.final_text or f"[{result.exit_reason.value}] {result.detail or ''}"
        return result.exit_reason == ExitReason.COMPLETED, text
