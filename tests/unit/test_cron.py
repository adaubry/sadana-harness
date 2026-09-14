"""Tests for sadana.cron: parse() on every operator, next_after()'s
day-field OR semantics, one real DST transition, and the search ceiling."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from sadana.cron import Cron, CronError, next_after, parse


@pytest.mark.unit
def test_star_means_unrestricted_on_every_field() -> None:
    cron = parse("* * * * *")
    assert cron == Cron(minute=None, hour=None, day_of_month=None, month=None, day_of_week=None)


@pytest.mark.unit
def test_comma_list() -> None:
    cron = parse("0,15,30,45 * * * *")
    assert isinstance(cron, Cron)
    assert cron.minute == frozenset({0, 15, 30, 45})


@pytest.mark.unit
def test_range() -> None:
    cron = parse("* 9-17 * * *")
    assert isinstance(cron, Cron)
    assert cron.hour == frozenset(range(9, 18))


@pytest.mark.unit
def test_step_over_the_full_range() -> None:
    cron = parse("*/15 * * * *")
    assert isinstance(cron, Cron)
    assert cron.minute == frozenset({0, 15, 30, 45})


@pytest.mark.unit
def test_step_over_a_range() -> None:
    cron = parse("* * 1-10/2 * *")
    assert isinstance(cron, Cron)
    assert cron.day_of_month == frozenset({1, 3, 5, 7, 9})


@pytest.mark.unit
def test_month_and_day_of_week_fields() -> None:
    cron = parse("0 0 * 1,6,12 1-5")
    assert isinstance(cron, Cron)
    assert cron.month == frozenset({1, 6, 12})
    assert cron.day_of_week == frozenset({1, 2, 3, 4, 5})


@pytest.mark.unit
def test_wrong_field_count_is_a_cron_error_not_an_exception() -> None:
    result = parse("* * * *")
    assert isinstance(result, CronError)


@pytest.mark.unit
@pytest.mark.parametrize(
    "expr",
    [
        "60 * * * *",  # minute out of range
        "* 24 * * *",  # hour out of range
        "* * 0 * *",  # day-of-month out of range (1-31)
        "* * * 13 *",  # month out of range
        "* * * * 7",  # day-of-week out of range (0-6, 0=Sunday)
        "* , * * *",  # empty list item
        "* */0 * * *",  # a zero step
    ],
)
def test_out_of_range_or_malformed_is_a_cron_error(expr: str) -> None:
    assert isinstance(parse(expr), CronError)


@pytest.mark.unit
def test_next_after_a_plain_daily_time() -> None:
    cron = parse("0 9 * * *")
    assert isinstance(cron, Cron)
    after = datetime(2026, 1, 1, 10, 0, tzinfo=UTC).timestamp()
    result = next_after(cron, "UTC", after)
    assert datetime.fromtimestamp(result, tz=UTC) == datetime(2026, 1, 2, 9, 0, tzinfo=UTC)


@pytest.mark.unit
def test_next_after_returns_the_same_day_when_the_time_has_not_passed_yet() -> None:
    cron = parse("0 9 * * *")
    assert isinstance(cron, Cron)
    after = datetime(2026, 1, 1, 8, 0, tzinfo=UTC).timestamp()
    result = next_after(cron, "UTC", after)
    assert datetime.fromtimestamp(result, tz=UTC) == datetime(2026, 1, 1, 9, 0, tzinfo=UTC)


@pytest.mark.unit
def test_day_of_month_and_day_of_week_both_restricted_is_or_not_and() -> None:
    """Standard five-field cron: when both day fields are restricted, a day
    matching *either* qualifies — not a day matching both. 2026-01-01 is a
    Thursday; day_of_month={1} matches it even though day_of_week={1}
    (Monday) does not."""
    cron = parse("0 0 1 * 1")
    assert isinstance(cron, Cron)
    after = datetime(2025, 12, 31, 23, 0, tzinfo=UTC).timestamp()
    result = next_after(cron, "UTC", after)
    assert datetime.fromtimestamp(result, tz=UTC) == datetime(2026, 1, 1, 0, 0, tzinfo=UTC)


@pytest.mark.unit
def test_dst_fall_back_never_fires_the_repeated_hour_twice() -> None:
    """America/New_York falls back at 2026-11-01 02:00 local (clocks become
    01:00 again). A schedule firing at 01:30 daily must produce a single,
    strictly increasing sequence of instants across the transition — never
    the same instant twice, never a step backwards — because every
    candidate is compared by its own timestamp, not by wall-clock label."""
    cron = parse("30 1 * * *")
    assert isinstance(cron, Cron)
    before = datetime(2026, 10, 31, 12, 0, tzinfo=UTC).timestamp()

    first = next_after(cron, "America/New_York", before)
    second = next_after(cron, "America/New_York", first)
    third = next_after(cron, "America/New_York", second)

    assert first < second < third
    # Exactly 24h apart in real elapsed time, both across and away from the
    # transition — the mechanism, not a snapshot of a specific date's math.
    assert second - first in (23 * 3600, 24 * 3600, 25 * 3600)
    assert third - second in (23 * 3600, 24 * 3600, 25 * 3600)


@pytest.mark.unit
def test_dst_spring_forward_still_advances() -> None:
    """2026-03-08 is America/New_York's spring-forward date (02:00 local
    skips to 03:00). A schedule whose only field lands inside the skipped
    hour must not hang or go backwards — next_after still returns a valid,
    later instant."""
    cron = parse("30 2 * * *")
    assert isinstance(cron, Cron)
    before = datetime(2026, 3, 7, 12, 0, tzinfo=UTC).timestamp()

    result = next_after(cron, "America/New_York", before)
    following = next_after(cron, "America/New_York", result)

    assert result > before
    assert following > result


@pytest.mark.unit
def test_unknown_timezone_raises() -> None:
    cron = parse("0 0 * * *")
    assert isinstance(cron, Cron)
    with pytest.raises(ValueError):
        next_after(cron, "Nowhere/Imaginary", 0.0)


@pytest.mark.unit
def test_calendrically_impossible_expression_raises_within_the_search_ceiling() -> None:
    """February 31st never exists. Rather than hanging, next_after raises
    once its bounded search is exhausted (spec.md's four-year ceiling)."""
    cron = parse("0 0 31 2 *")
    assert isinstance(cron, Cron)
    with pytest.raises(ValueError):
        next_after(cron, "UTC", 0.0)
