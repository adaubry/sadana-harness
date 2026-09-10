"""A moment in time can start a conversation with nobody asking.

`docs/tasks/GATEWAY-DAEMON-02-scheduled-and-resumable-triggers/spec.md`. I/O:
the clock, `conn`. Its two one-line pure helpers stay in this same file
rather than a second one — CLAUDE.md's I/O-module rule exists to keep a
large, block-spanning pure module free of disk/network/clock code, not to
forbid two trivial pure helpers living beside the ten lines of I/O they
exist to serve (`conversation_store.py`'s own `_conversation_row()` already
sets this precedent).

Firing a trigger is nothing but delivering a synthetic inbound message
through the exact bridge `channel_webhook.py` already uses —
`gateway_dispatch.handle_inbound()`, completely unchanged. No second
bridge, no new turn-loop entry point.
"""

from __future__ import annotations

import asyncio
import logging
import sqlite3
import time

from sadana import conversation_store, gateway_dispatch, observability, plugin_dispatch
from sadana.gateway import MessageEvent

logger = logging.getLogger(__name__)


def _is_due(next_run_at: float, now: float) -> bool:
    return next_run_at <= now


def _advance(now: float, interval_seconds: float) -> float:
    """Always `now + interval_seconds`, never `next_run_at + interval_seconds`
    — the actual mechanism behind "a missed schedule does not fire
    retroactively": a gap longer than one interval (the daemon was down)
    advances straight past every already-past firing instead of leaving a
    backlog that would all come due on the very next tick."""
    return now + interval_seconds


async def tick(
    conn: sqlite3.Connection,
    *,
    plugin_set: plugin_dispatch.PluginSet,
    persona: str,
    provider: str,
    model: str,
    record_turn: observability.RecordTurnFn = observability.noop_record,
    record_plugin_run: observability.RecordPluginRunFn = observability.noop_record,
) -> int:
    """Fires every trigger whose `next_run_at` has arrived, returning how
    many fired. A trigger's own failure is caught, logged, and skipped —
    never raised out of this function, so one bad trigger can't stop the
    rest of a tick (CLAUDE.md: "an observability/recording write's failure
    is caught and logged, never raised to the caller of the run it is
    observing," applied here to a tick's own per-trigger work). A failed
    firing is deliberately *not* advanced or deleted: it stays due and is
    retried on the next tick, the same self-healing shape a transient
    failure deserves — "no catch-up" (requirement 5) is about the daemon
    having been down, not about one attempt failing while it was up.

    Every direct touch of `conn` this function makes (the due-triggers
    query, the post-fire advance/delete) is wrapped in
    `gateway_dispatch.conn_lock` — the same lock `handle_inbound()` already
    holds for its own whole body. `run_tick_loop` runs on its own
    background thread, and without this a deploy-stage cold review found it
    racing `conn` against every webhook request's own thread, violating
    `conversation_store.open_store()`'s documented single-writer-at-a-time
    contract. `handle_inbound()` itself is called *outside* any lock this
    function holds — it acquires the same, non-reentrant lock internally,
    so nesting would deadlock."""
    now = time.time()
    with gateway_dispatch.conn_lock:
        due = conversation_store.due_triggers(conn, now=now)
    fired = 0
    for trigger in due:
        event = MessageEvent(platform="schedule", chat_id=trigger.name, thread_id=None, text=trigger.trigger_text)
        try:
            await gateway_dispatch.handle_inbound(
                conn,
                event,
                plugin_set=plugin_set,
                persona=persona,
                provider=provider,
                model=model,
                record_turn=record_turn,
                record_plugin_run=record_plugin_run,
            )
        except Exception:
            logger.warning("scheduled trigger %r failed to fire", trigger.name, exc_info=True)
            continue
        with gateway_dispatch.conn_lock:
            if trigger.interval_seconds is None:
                conversation_store.delete_scheduled_trigger(conn, name=trigger.name)
            else:
                conversation_store.advance_scheduled_trigger(
                    conn, name=trigger.name, next_run_at=_advance(now, trigger.interval_seconds)
                )
        fired += 1
    return fired


def run_tick_loop(
    conn: sqlite3.Connection,
    *,
    interval_seconds: float,
    plugin_set: plugin_dispatch.PluginSet,
    persona: str,
    provider: str,
    model: str,
    record_turn: observability.RecordTurnFn = observability.noop_record,
    record_plugin_run: observability.RecordPluginRunFn = observability.noop_record,
) -> None:
    """Blocks forever, ticking every `interval_seconds`. Meant to run in a
    `daemon=True` thread — needs no coordination with `gateway_daemon.run()`'s
    own SIGTERM/lock/shutdown sequence, since a daemon thread dies with the
    process and this project's own "best-effort, no catch-up" posture
    already makes an abrupt mid-tick kill an accepted, harmless outcome
    rather than a new failure mode to guard against.

    Mirrors `tick()`'s own keyword parameters explicitly rather than
    forwarding `**kwargs` — a self-check pass tried the `**kwargs` shrink
    and a second, more careful pass reversed it: forwarding loses mypy's
    ability to check this call against `tick()`'s real signature (a
    `# type: ignore` would be needed), which this project's own emphasis on
    `make typecheck` makes the wrong trade for five duplicated names."""
    while True:
        asyncio.run(
            tick(
                conn,
                plugin_set=plugin_set,
                persona=persona,
                provider=provider,
                model=model,
                record_turn=record_turn,
                record_plugin_run=record_plugin_run,
            )
        )
        time.sleep(interval_seconds)
