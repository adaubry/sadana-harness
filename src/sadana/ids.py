"""Every long-lived thing gets a name that sorts by when it was made.

H16 (`docs/tasks/H16-store-identity-ledger-locks/spec.md` requirements 1-3),
serving P1 of `docs/reference/console_fit_plan.md` §4.

Not pure, despite being a leaf. It reads the clock and holds a counter behind
a lock, so it is its own file — which is what CLAUDE.md's rule actually asks
for (a module touching real I/O never shares a file with a block's
pure-function module). The intent brief called this module pure; it is not,
and `spec.md` § Concerns records the correction rather than leaving the label
standing.

UUIDv7 (RFC 9562 §5.7) rather than UUIDv4 because the console sorts and pages
by identity: a v7's leading 48 bits are the millisecond it was minted, so
`ORDER BY id` is `ORDER BY created_at` without reading a second column or
trusting two machines' clocks to agree about ties. Hand-written rather than
installed: `console_fit_plan.md` §5(c) closes the dependency list at three,
and this is forty lines of bit-shifting.

Minted locally with no coordination, which is the point — a box that has to
ask anything for an id cannot answer while the tether is down.
"""

from __future__ import annotations

import os
import re
import threading
import time

#: The closed registry. A prefix is the kind of thing an id names, carried in
#: the id itself so a value pasted into a bug report says what it is, and so
#: `ledger.py` can hold ids from thirteen nouns in one column without them
#: ever colliding. Closed on purpose: adding a kind is a work item, not a
#: string somebody types at a call site.
PREFIXES = frozenset(
    {
        "hrn",  # the harness itself
        "conv",  # a conversation
        "msg",  # a message
        "run",  # a turn run or a plugin run
        "trc",  # a trace
        "spn",  # a span
        "appr",  # an approval
        "sch",  # a schedule
        "agt",  # a character, or an account's selection of one
        "tmpl",  # a conversation template
        "mem",  # a remembered entry
        "rub",  # a rubric override
        "art",  # an artifact
        "plg",  # a plugin, its registration, or a release of it
        "wfl",  # a workflow
        "node",  # a node in one
        "tool",  # a tool
        "prv",  # a provider
        "bdg",  # a budget
        "intg",  # an integration
        "sec",  # a secret
        "op",  # an operation
        "insp",  # an inspection
    }
)

# RFC 9562 §6.2 "method 1": a fixed-length dedicated counter in `rand_a`. Two
# ids minted in the same millisecond must still sort against each other, and
# 62 random bits do not do that — they sort randomly, which is worse than not
# sorting, because it looks ordered until two rows land in one millisecond.
# 12 bits wide, at bits 64..75. Not a knob: `uuid7()` hardcodes the same layout
# in its shifts, so changing this alone would mint malformed UUIDs rather than a
# wider counter.
_COUNTER_MAX = (1 << 12) - 1

_lock = threading.Lock()
_last_ms = -1
_counter = 0

_HEX32 = re.compile(r"[0-9a-f]{32}")


def advance(last_ms: int, counter: int, now_ms: int) -> tuple[int, int]:
    """The next `(millisecond, counter)` pair, as a pure function of the last
    one and what the clock says.

    Pure, and separate from the clock read below, because this is the part
    with the interesting cases and `testing-conventions` asks for exactly
    this shape rather than a faked clock: a function taking the environment
    as *data* is input-to-output, where patching `time.time` is a fake.

    Never goes backwards, and the two ways it can be asked to are both
    handled here rather than at the call site: a wall clock stepped backwards
    (NTP, a suspended laptop), and overflow, which borrows from the future.
    Either way the millisecond returned is at least `last_ms`, so sort order
    survives a clock that does not.

    Overflow past 4095 ids inside one millisecond advances the millisecond
    instead of wrapping the counter. Wrapping would produce a *smaller* id
    than the one before it, which is the one thing this counter exists to
    prevent; borrowing a millisecond from the future costs an id whose
    timestamp reads up to a few milliseconds early, under a burst rate
    nothing in this repository can currently reach.
    """
    if now_ms > last_ms:
        return now_ms, 0
    if counter >= _COUNTER_MAX:
        return last_ms + 1, 0
    return last_ms, counter + 1


def uuid7() -> str:
    """One UUIDv7, in the canonical dashed form.

    Layout, most significant bit first: 48 bits of Unix milliseconds, 4 bits
    of version (`0b0111`), 12 bits of counter, 2 bits of variant (`0b10`),
    62 bits from `os.urandom`. 74 random-or-counter bits in total, which is
    what RFC 9562 §5.7 specifies.

    `os.urandom` rather than `random`: the module-level `random` is a shared,
    seedable Mersenne Twister, and an id that a second process can predict is
    an id somebody can guess their way to. This costs nothing here — one
    8-byte read per id.

    The lock covers the read-modify-write of the counter and nothing else. Two
    threads minting at the same instant would otherwise share a counter value
    and stop sorting against each other; the random half needs no coordination.
    """
    global _last_ms, _counter
    with _lock:
        _last_ms, _counter = advance(_last_ms, _counter, int(time.time() * 1000))
        ms, counter = _last_ms, _counter
    rand_b = int.from_bytes(os.urandom(8), "big") & ((1 << 62) - 1)
    value = (ms << 80) | (0x7 << 76) | (counter << 64) | (0b10 << 62) | rand_b
    h = f"{value:032x}"
    return f"{h[:8]}-{h[8:12]}-{h[12:16]}-{h[16:20]}-{h[20:]}"


def _check_prefix(prefix: str) -> None:
    if prefix not in PREFIXES:
        raise ValueError(f"{prefix!r} is not a registered id prefix; add it to ids.PREFIXES in its own work item")


def make_id(prefix: str) -> str:
    """`<prefix>_<32 hex digits>` — the console's `makeId`.

    Dashes are stripped so the whole id is one token: it survives a URL path
    segment, a shell argument and a double-click without anybody quoting it,
    and a fixed-width lowercase-hex tail means a plain lexicographic sort of
    two ids with the same prefix is a sort by mint time.

    Raises `ValueError` for a prefix that is not registered. That is a bug in
    the caller, not bad data, which is why it raises where `parse_id` returns
    `None` for a malformed *string*.
    """
    _check_prefix(prefix)
    return f"{prefix}_{uuid7().replace('-', '')}"


def parse_id(prefix: str, s: str) -> str | None:
    """The hex half of `s` when it is an id of `prefix`'s kind, else `None`.

    `None` covers every way a *string* can be wrong — the wrong prefix, no
    separator, the wrong length, a non-hex character, upper case — and a value
    that is not a string at all, which is how a `NULL` id read back from a
    legacy row arrives. That last case used to raise `AttributeError` from
    `partition`, and `ledger.record_change` calls this *inside* a write
    transaction, so an identity-bookkeeping gap rolled back the real write it
    was only meant to annotate.

    An unregistered `prefix` still raises, because the caller asking is the one
    thing that cannot be data.
    """
    _check_prefix(prefix)
    if not isinstance(s, str):
        return None
    head, sep, tail = s.partition("_")
    if not sep or head != prefix or not _HEX32.fullmatch(tail):
        return None
    return tail
