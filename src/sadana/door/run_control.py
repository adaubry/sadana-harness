"""H21's stop registry — one `threading.Event` per running turn, addressed
by its own `run_id`.

`docs/tasks/H21-watched-streaming-live-runs-stop/spec.md`. A lock-guarded
`dict[str, threading.Event]` — the same "plain dict, bounded by
concurrently-running turns, not total history" shape `stores.py`'s own
`_conversation_locks` already uses and justifies (ponytail comment carried
over). The difference: this dict *is* bounded — a run's own entry is
removed the moment its turn ends (`unregister`), unlike
`_conversation_locks`, which is deliberately never evicted because it is
bounded by every conversation a process has ever seen, not by how many are
running right now.
"""

from __future__ import annotations

import threading

# ponytail: a plain dict guarded by one lock, not a per-key lock like
# `stores.conversation_lock` — every operation here (register/request_stop/
# unregister) is a quick dict read or write, never a call that blocks while
# holding this lock, so contention between unrelated runs costs nothing
# worth a second lock. Upgrade to per-key locking only if that stops being
# true.
_events: dict[str, threading.Event] = {}
_guard = threading.Lock()


def register(run_id: str) -> threading.Event:
    """Creates and stores a fresh `Event` for `run_id`, and returns it.

    The caller — the door's own `TurnObserver` (`door/nouns/messages.py`) —
    holds onto this exact instance for the run's own lifetime;
    `should_stop()` reads it directly and never looks it up by id a second
    time."""
    event = threading.Event()
    with _guard:
        _events[run_id] = event
    return event


def request_stop(run_id: str) -> bool:
    """`True` and sets the event, if `run_id` is currently registered.
    `False` otherwise — the caller (`door/nouns/runs.py`'s `act()`) turns
    that into a `409 CONFLICT`: this process is not holding a run by that
    id, whether it already finished or the process restarted since it
    started."""
    with _guard:
        event = _events.get(run_id)
    if event is None:
        return False
    event.set()
    return True


def unregister(run_id: str) -> None:
    """Pops `run_id`'s entry, if any — a no-op if it is already gone.

    Called once from `turn_finished`, in a `finally`-equivalent position,
    on every terminal path a turn can take: `COMPLETED`, every failure
    `ExitReason`, and `INTERRUPTED` itself. That keeps a run's `Event` from
    outliving the run it belongs to. Run ids are UUIDv7 (`ids.make_id`),
    never reused, so there is no collision this guards against even
    without it — the entry would otherwise simply leak for the life of the
    process."""
    with _guard:
        _events.pop(run_id, None)
