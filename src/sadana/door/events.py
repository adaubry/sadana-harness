"""H21's ephemeral-frame hand-off — one bounded queue, written to by a
turn's own observer and drained by nobody yet.

`docs/tasks/H21-watched-streaming-live-runs-stop/spec.md`. Nothing here
writes to SQLite or the ledger — an ephemeral frame is never ledgered, per
this item's own rule (Requirement 7: "what streams or is recorded live is
not durably stored beyond the turn's own finished answer and run/span
records"). One queue, one eventual drainer (H30's own job of relaying it
onto a real socket) — not a fan-out mechanism for multiple callbacks, the
narrower thing `plugin_stream_hooks.py`'s own declined-elsewhere pattern
would otherwise suggest (spec.md's own `## Rejected alternatives`).
"""

from __future__ import annotations

import contextlib
import queue

from sadana import config

#: The process's one ephemeral-frame queue. Bounded so a turn nobody is
#: watching cannot grow this without limit; `push()` below drops the oldest
#: queued frame under sustained backpressure rather than blocking the turn
#: that produced it.
ephemeral_queue: queue.Queue[dict] = queue.Queue(maxsize=config.env_int("SADANA_DOOR_EPHEMERAL_QUEUE_SIZE", 1000))


def push(frame: dict) -> None:
    """Puts `frame` on `ephemeral_queue`. Under sustained backpressure (the
    queue is full — nobody has drained it in a while) the oldest queued
    frame is dropped to make room, rather than blocking the turn that
    produced this one: a delta arriving late is worse than one arriving
    dropped, for a stream that is explicitly not recoverable after the
    fact (Requirement 7)."""
    try:
        ephemeral_queue.put_nowait(frame)
    except queue.Full:
        with contextlib.suppress(queue.Empty):
            ephemeral_queue.get_nowait()
        ephemeral_queue.put_nowait(frame)
