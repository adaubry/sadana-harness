"""The door's capability vocabulary — a closed list, and what this harness
version actually turns on.

`docs/tasks/H19-door-framework-token-conformance/spec.md` requirements 35-36.
`ALL` is fixed by the console's own contract; `DECLARED` is append-only —
every later work item's whole contribution to this file is adding its own
name to the end, never removing or reordering one already there.
"""

from __future__ import annotations

#: The closed sixteen. A name outside this raises at import, below.
ALL: tuple[str, ...] = (
    "grammar.v1",
    "changes",
    "inventory",
    "artifacts.download",
    "plugins.install",
    "plugins.inspect",
    "plugins.write",
    "schedules.write",
    "approvals.wait",
    "approvals.call",
    "streaming",
    "runs.live",
    "runs.stop",
    "settings.write",
    "secrets.write",
    "upgrade",
)

if len(ALL) != 16:
    raise ValueError(f"capabilities.ALL has {len(ALL)} entries; the closed set is exactly sixteen")

#: What H19 turns on: the door's own framework, the change feed, and
#: inventory enumeration. Every later work item appends its own name here.
DECLARED: tuple[str, ...] = (
    "grammar.v1",
    "changes",
    "inventory",
    # H20 (docs/tasks/H20-door-nouns-turn-side/spec.md requirement 27):
    # artifacts.py's one action.
    "artifacts.download",
    "approvals.wait",
    "approvals.call",
    "schedules.write",
    # H21 (docs/tasks/H21-watched-streaming-live-runs-stop/spec.md): a
    # turn can stream, a run's live state is real, and a run can be asked
    # to stop.
    "streaming",
    "runs.live",
    "runs.stop",
    # H24's own three, added here because H24 had not landed on a shared
    # branch when this chain reached this file (docs/tasks/
    # H30-tether-enroll-frames-lifecycle/review.md § Findings) — H24's own
    # agent rebases onto this rather than re-adding them.
    "plugins.install",
    "plugins.inspect",
    "plugins.write",
    # H30 (docs/tasks/H30-tether-enroll-frames-lifecycle/spec.md): the
    # remote upgrade action.
    "upgrade",
)

_unknown = [name for name in DECLARED if name not in ALL]
if _unknown:
    raise ValueError(f"capabilities.DECLARED names outside the closed list: {_unknown}")


def declared() -> tuple[str, ...]:
    return DECLARED
