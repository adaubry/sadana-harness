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
`gateway_dispatch.handle_inbound()`. No second bridge, no new turn-loop
entry point; since CLIENT-SURFACE-01 that bridge is itself only a
translation onto `client_surface.take_turn()`, so a scheduled trigger and a
webhook message and a terminal line all reach the same one door.
"""

from __future__ import annotations

import asyncio
import logging
import time

from sadana import client_surface, conversation_store, gateway_dispatch, memory
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


async def tick(runtime: client_surface.Runtime) -> int:
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

    Every direct touch of `runtime.conn` this function makes (the
    due-triggers query, the post-fire advance/delete) is wrapped in
    `client_surface.conn_lock` — the same lock `take_turn()` already holds
    for its own whole body. `run_tick_loop` runs on its own
    background thread, and without this a deploy-stage cold review found it
    racing `conn` against every webhook request's own thread, violating
    `conversation_store.open_store()`'s documented single-writer-at-a-time
    contract. `handle_inbound()` itself is called *outside* any lock this
    function holds — it acquires the same, non-reentrant lock internally,
    so nesting would deadlock."""
    conn = runtime.conn
    now = time.time()
    with client_surface.conn_lock:
        due = conversation_store.due_triggers(conn, now=now)
    fired = 0
    for trigger in due:
        event = MessageEvent(platform="schedule", chat_id=trigger.name, thread_id=None, text=trigger.trigger_text)
        try:
            # A trigger the owner wrote is the owner's own machinery, so it
            # runs as the owner — their voice, their memory (PERSONA-02). The
            # conversation key is still `schedule:<name>`: whose the work is
            # and which thread it continues are different questions, and only
            # the first one changed.
            await gateway_dispatch.handle_inbound(runtime, event, account=memory.owner_account())
        except Exception:
            logger.warning("scheduled trigger %r failed to fire", trigger.name, exc_info=True)
            continue
        with client_surface.conn_lock:
            if trigger.interval_seconds is None:
                conversation_store.delete_scheduled_trigger(conn, name=trigger.name)
            else:
                conversation_store.advance_scheduled_trigger(
                    conn, name=trigger.name, next_run_at=_advance(now, trigger.interval_seconds)
                )
        fired += 1
    return fired


def run_tick_loop(runtime: client_surface.Runtime, *, interval_seconds: float) -> None:
    """Blocks forever, ticking every `interval_seconds`. Meant to run in a
    `daemon=True` thread — needs no coordination with `gateway_daemon.run()`'s
    own SIGTERM/lock/shutdown sequence, since a daemon thread dies with the
    process and this project's own "best-effort, no catch-up" posture
    already makes an abrupt mid-tick kill an accepted, harmless outcome
    rather than a new failure mode to guard against.

    Takes the `Runtime` rather than mirroring `tick()`'s parameters:
    CLIENT-SURFACE-01 moved the seven values this used to forward — the
    connection, the plugin set, the provider, the model and the two
    recording callables — into one value assembled once by
    `client_surface.open_runtime()`. The earlier note here, about why the
    five duplicated keyword names beat a `**kwargs` shrink, is obsolete along
    with the names themselves."""
    while True:
        asyncio.run(tick(runtime))
        time.sleep(interval_seconds)
