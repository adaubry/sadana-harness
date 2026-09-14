"""Five-field cron: parsing and "what fires next" — nothing else.

`docs/tasks/H27-door-nouns-schedules/spec.md`. Pure: no I/O, no clock read
(`next_after` takes `after` as a value), no import of anything under
`sadana.door` or `sadana.conversation_store`. Declined `croniter` (spec.md §
Design, § Rejected alternatives) — this is the whole dependency-free
replacement for it, deliberately scoped to standard five-field cron only:
`*`, comma lists, `a-b` ranges, `a-b/n` and `*/n` steps. No names, no
`L`/`W`/`#`.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

#: (field name, minimum, maximum) — day_of_week is 0-6, 0 = Sunday, matching
#: standard cron (and never a "7 also means Sunday" alias: nothing in this
#: work item's grammar asks for it).
_FIELDS: tuple[tuple[str, int, int], ...] = (
    ("minute", 0, 59),
    ("hour", 0, 23),
    ("day_of_month", 1, 31),
    ("month", 1, 12),
    ("day_of_week", 0, 6),
)

#: A syntactically valid but calendrically impossible expression (day 31 of
#: February, say) never matches. Four years covers every leap-year phase;
#: past that `next_after` raises rather than searching forever.
# ponytail: four-year ceiling on next_after's search catches an
# unsatisfiable expression without hanging; raise, don't loop forever.
_MAX_SEARCH_DAYS = 4 * 366


@dataclass(frozen=True)
class Cron:
    """One parsed five-field expression. Each field is the set of values it
    matches, or `None` for `*` (no restriction) — `None` rather than "every
    value in range" both reads as "unrestricted" at every call site and
    keeps `day_of_month`/`day_of_week`'s OR-when-both-restricted rule
    (`next_after` below) a direct `is None` check."""

    minute: frozenset[int] | None
    hour: frozenset[int] | None
    day_of_month: frozenset[int] | None
    month: frozenset[int] | None
    day_of_week: frozenset[int] | None


@dataclass(frozen=True)
class CronError:
    message: str


def _parse_field(text: str, *, name: str, lo: int, hi: int) -> frozenset[int] | None:
    if text == "*":
        return None
    values: set[int] = set()
    for part in text.split(","):
        if not part:
            raise ValueError(f"{name}: empty item in {text!r}")
        base, _, step_text = part.partition("/")
        step = 1
        if step_text:
            if not step_text.isdigit() or int(step_text) <= 0:
                raise ValueError(f"{name}: bad step {step_text!r} in {part!r}")
            step = int(step_text)
        if base == "*":
            lo_b, hi_b = lo, hi
        elif "-" in base:
            lo_s, _, hi_s = base.partition("-")
            if not (lo_s.isdigit() and hi_s.isdigit()):
                raise ValueError(f"{name}: bad range {base!r}")
            lo_b, hi_b = int(lo_s), int(hi_s)
            if lo_b > hi_b:
                raise ValueError(f"{name}: range {base!r} counts down, not up")
        else:
            if not base.isdigit():
                raise ValueError(f"{name}: {base!r} is not a number")
            lo_b = hi_b = int(base)
        if lo_b < lo or hi_b > hi:
            raise ValueError(f"{name}: {base!r} is outside {lo}-{hi}")
        values.update(range(lo_b, hi_b + 1, step))
    if not values:
        raise ValueError(f"{name}: {text!r} matches nothing")
    return frozenset(values)


def parse(expr: str) -> Cron | CronError:
    """Returns a value rather than raising — unlike `door/filter.py`'s own
    exception-based parser — because `schedules.create` validates `cron` and
    `timezone` independently and reports both as separate `errors[]`
    entries in one response; a returned `CronError` composes with a second,
    unrelated `zoneinfo` check without either living inside the other's
    `try`/`except` (spec.md § Design)."""
    parts = expr.split()
    if len(parts) != 5:
        return CronError(f"expected 5 fields (minute hour day-of-month month day-of-week), got {len(parts)}")
    try:
        parsed = [
            _parse_field(part, name=name, lo=lo, hi=hi) for part, (name, lo, hi) in zip(parts, _FIELDS, strict=False)
        ]
    except ValueError as exc:
        return CronError(str(exc))
    return Cron(*parsed)


def _day_matches(cron: Cron, d: date) -> bool:
    month_ok = cron.month is None or d.month in cron.month
    if not month_ok:
        return False
    dom_ok = cron.day_of_month is None or d.day in cron.day_of_month
    dow_ok = cron.day_of_week is None or (d.isoweekday() % 7) in cron.day_of_week
    if cron.day_of_month is None or cron.day_of_week is None:
        return dom_ok and dow_ok
    return dom_ok or dow_ok  # standard cron's day-field OR, when both are restricted


def next_after(cron: Cron, tz: str, after: float) -> float:
    """The first occurrence strictly after `after`, in `tz`.

    Every candidate is a `zoneinfo`-aware `datetime`, compared by its own
    `.timestamp()` — never by wall-clock label. That is what makes a DST
    transition self-correcting rather than a special case (spec.md §
    Design): a repeated wall-clock hour (fall-back) still resolves to a
    single, monotonically increasing sequence of instants under Python's
    default `fold=0`, and a candidate day is searched by computing every
    matching `(hour, minute)` pair's real timestamp and taking the smallest
    one greater than `after`, rather than assuming wall-clock order implies
    epoch order — which a spring-forward day's nonexistent hour otherwise
    breaks (its wall-clock-later minutes can resolve to a real instant past
    the very next, existing hour).
    """
    try:
        zone = ZoneInfo(tz)
    except ZoneInfoNotFoundError as exc:
        raise ValueError(f"unknown timezone {tz!r}") from exc

    hours = cron.hour if cron.hour is not None else range(24)
    minutes = cron.minute if cron.minute is not None else range(60)

    start = datetime.fromtimestamp(after, tz=zone).date()
    for offset in range(_MAX_SEARCH_DAYS):
        d = start + timedelta(days=offset)
        if not _day_matches(cron, d):
            continue
        candidates = (datetime(d.year, d.month, d.day, h, m, tzinfo=zone).timestamp() for h in hours for m in minutes)
        # Every candidate on a later day is already `> after` by construction
        # (a day boundary always advances real time by ~24h, DST's ±1h
        # nowhere close to closing that gap) — the filter only ever has
        # anything to do on the starting day itself.
        if offset == 0:
            candidates = (ts for ts in candidates if ts > after)
        result = min(candidates, default=None)
        if result is not None:
            return result
    raise ValueError(f"no occurrence of this expression found within {_MAX_SEARCH_DAYS} days of {after}")
