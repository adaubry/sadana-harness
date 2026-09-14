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

from sadana import client_surface, conversation_store, cron, gateway_dispatch, memory
from sadana.door.nouns import approvals as door_approvals
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


def next_schedule_run(row: conversation_store.ScheduleRow, *, now: float) -> float:
    """`cron` wins whenever it's set — including on a migrated legacy row
    that has since been given a `cron` through the door, which is what
    makes updating one of those rows quietly convert it from then on
    (spec.md § Design: "`cron` vs `interval_seconds`"). A schedule with
    neither (malformed data no validated write could have produced) is a
    bug this raises on rather than silently never firing again."""
    if row.cron is not None:
        parsed = cron.parse(row.cron)
        if isinstance(parsed, cron.CronError):
            raise ValueError(f"schedule {row.id} has an unparseable cron expression: {parsed.message}")
        return cron.next_after(parsed, row.timezone, now)
    if row.interval_seconds is not None:
        return _advance(now, row.interval_seconds)
    raise ValueError(f"schedule {row.id} has neither cron nor interval_seconds set")


async def tick(runtime: client_surface.Runtime, *, now: float | None = None) -> int:
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

    Every direct touch of the store this function makes is now narrower than
    it was. Before H16 the due-triggers query and the post-fire advance/delete
    were all wrapped in one process-wide `conn_lock`, because a deploy-stage
    cold review found this loop — which runs on its own background thread —
    racing the single connection against every webhook request's thread. The
    guarantee is unchanged and the mechanism is smaller: the query reads
    through this thread's own read-only connection, so it waits for nobody, and
    the two writes go through `write_txn`, which takes the writer lock for the
    length of a transaction.

    `handle_inbound()` is still called holding no lock of ours. It reaches
    `take_turn`, which takes that conversation's own lock internally, and
    taking it here first would deadlock against it.

    `now` is `None` in every real caller (`run_tick_loop` never passes it) —
    reading the clock is the default, not the norm being overridden. A test
    passes a fixed value instead of monkeypatching `time.time`, the same
    "environment as data" shape `door/router.py`'s own `ctx.clock` already
    uses (testing-conventions bans faking the clock at the module level).

    H27's `schedules` loop runs after the legacy `due_triggers` loop, in the
    same per-item try/except isolation: one schedule's failure is logged and
    skipped, never advanced (so it stays due and is retried), and never lets
    a broken schedule stop any other trigger or schedule in this tick."""
    conn = runtime.conn
    now = now if now is not None else time.time()
    # H18. Expiry first: it touches no `scheduled_triggers` row, so its own
    # ordering relative to trigger firing has no observable interaction —
    # placed first as the simpler, cheaper-to-reason-about check.
    await door_approvals.expire_due(runtime.connections, now)
    due = conversation_store.due_triggers(runtime.connections.reader(), now=now)
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
        if trigger.interval_seconds is None:
            conversation_store.delete_scheduled_trigger(conn, name=trigger.name)
        else:
            conversation_store.advance_scheduled_trigger(
                conn, name=trigger.name, next_run_at=_advance(now, trigger.interval_seconds)
            )
        fired += 1

    for schedule in conversation_store.due_schedules(runtime.connections.reader(), now=now):
        try:
            # Computed before firing, not after: a schedule whose cadence
            # can't be computed (a cron gone unparseable somehow, though
            # `door/nouns/schedules.py` validates every write) must not fire
            # and then have nothing to advance to — that would fire it again
            # every tick forever instead of just staying due and logging,
            # the same self-healing shape the trigger loop above already has.
            next_run_at = next_schedule_run(schedule, now=now)
        except ValueError:
            # The only two failure modes `next_schedule_run` documents —
            # an unparseable cron, or neither cron nor interval_seconds set
            # — both raise exactly this. A bare `Exception` here would also
            # swallow a real bug in `cron.next_after` as a silent skip.
            logger.warning("scheduled %r has an unfireable cadence, skipping", schedule.name, exc_info=True)
            continue
        event = MessageEvent(
            platform="schedule",
            chat_id=schedule.name,
            thread_id=schedule.conversation_key,
            text=schedule.trigger_text,
        )
        try:
            ok, _text = await gateway_dispatch.handle_inbound(runtime, event, account=schedule.account_key)
        except Exception:
            logger.warning("scheduled %r failed to fire", schedule.name, exc_info=True)
            continue
        # `handle_inbound` exposes only `(ok, text)` — H20's own mapping from
        # the harness's eight `exit_reason` values down to the console's five
        # (wire.md § 6) does not exist yet to reuse, and reaching further
        # into the turn than `handle_inbound` already goes would reopen the
        # "one door onto a turn" rule this file's own module docstring
        # already commits to. `last_state` is the one bit that bridge
        # actually promises, until H20 lands a real mapping this can switch
        # to.
        last_state = "completed" if ok else "failed"
        conversation_store.advance_schedule(
            conn, id=schedule.id, next_run_at=next_run_at, last_run_at=now, last_state=last_state, now=now
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
