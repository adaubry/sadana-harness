"""Full-jitter exponential backoff for the tether's reconnect loop.

`docs/console/wire.md`: exponential backoff with full jitter, capped at 60s;
a `429` on `/connect` is obeyed exactly, via `Retry-After`, never backed off
further. Pure — the caller supplies the `random.Random` instance, so a test
can seed it rather than this module faking the environment.
"""

from __future__ import annotations

import random


def next_delay(attempt: int, *, base: float = 1.0, cap: float = 60.0, rng: random.Random) -> float:
    """Full jitter (AWS's own term for this shape): a uniform draw between
    zero and the capped exponential, not the exponential itself. `attempt`
    is the number of consecutive failed connections so far (0 for the
    first retry after an initial failure)."""
    return rng.uniform(0, min(cap, base * 2**attempt))
