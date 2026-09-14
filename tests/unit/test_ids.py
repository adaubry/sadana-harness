"""`ids.py` — the identity every console-visible row carries.

The interesting logic is `advance()`, which is pure and takes the clock as
data, so the backwards-clock and overflow cases are asserted without faking
anything (`testing-conventions`: "Pure functions that take environment as
data are fine … the same rule covers faking the clock").

The whole-id tests assert the *property* — minted ids sort in mint order —
against the real clock rather than a controlled one, which is what the
property actually claims.
"""

from __future__ import annotations

import pytest

from sadana.ids import _COUNTER_MAX, PREFIXES, advance, make_id, parse_id, uuid7


@pytest.mark.unit
def test_advance_starts_a_new_millisecond_at_zero() -> None:
    assert advance(100, 7, 101) == (101, 0)


@pytest.mark.unit
def test_advance_counts_up_inside_one_millisecond() -> None:
    assert advance(100, 0, 100) == (100, 1)
    assert advance(100, 41, 100) == (100, 42)


@pytest.mark.unit
def test_advance_borrows_a_millisecond_rather_than_wrapping_the_counter() -> None:
    """Wrapping to zero would mint an id smaller than the one before it,
    which is the single failure this counter exists to prevent."""
    assert advance(100, _COUNTER_MAX, 100) == (101, 0)


@pytest.mark.unit
def test_advance_never_goes_backwards_when_the_clock_does() -> None:
    """A clock stepped backwards — NTP, a resumed laptop — must not produce
    an id that sorts before one already handed out."""
    ms, counter = advance(5000, 3, 4000)
    assert ms == 5000
    assert counter == 4


@pytest.mark.unit
def test_ids_sort_in_the_order_they_were_minted() -> None:
    """The whole promise, against the real clock. A thousand mints span
    several milliseconds and crowd many into one, so this covers both the
    across-millisecond and within-millisecond cases at once."""
    minted = [make_id("conv") for _ in range(1000)]

    assert minted == sorted(minted)
    assert len(set(minted)) == len(minted)


@pytest.mark.unit
def test_the_within_one_millisecond_case_is_actually_exercised() -> None:
    """Guards the test above from passing vacuously. The first twelve hex
    digits are the 48-bit millisecond; if every id landed in its own, the
    sort assertion would prove nothing about the counter."""
    heads = [make_id("conv").removeprefix("conv_")[:12] for _ in range(1000)]

    assert len(set(heads)) < len(heads)


@pytest.mark.unit
def test_uuid7_sets_the_version_and_variant_bits() -> None:
    value = uuid7()

    assert len(value) == 36
    assert value[14] == "7"
    assert value[19] in "89ab"


@pytest.mark.unit
def test_make_id_and_parse_id_round_trip() -> None:
    made = make_id("msg")

    assert made.startswith("msg_")
    assert parse_id("msg", made) == made.removeprefix("msg_")


@pytest.mark.unit
@pytest.mark.parametrize(
    "bad",
    [
        "conv_" + "0" * 32,  # right shape, wrong prefix
        "msg" + "0" * 32,  # no separator
        "msg_" + "0" * 31,  # too short
        "msg_" + "0" * 33,  # too long
        "msg_" + "g" * 32,  # not hex
        "msg_" + "A" * 32,  # upper case
        "",
    ],
)
def test_parse_id_returns_none_for_anything_malformed(bad: str) -> None:
    assert parse_id("msg", bad) is None


@pytest.mark.unit
def test_an_unregistered_prefix_is_a_caller_error_not_bad_data() -> None:
    """It raises where a malformed string returns `None`: a prefix reaches
    these functions as a literal, so a wrong one is a bug to fix, never
    input to tolerate."""
    with pytest.raises(ValueError):
        make_id("nope")
    with pytest.raises(ValueError):
        parse_id("nope", "nope_" + "0" * 32)


@pytest.mark.unit
def test_the_registry_holds_every_prefix_the_ledger_will_need() -> None:
    """A contract about how two pieces of data relate, not a snapshot: every
    noun H16 and the work items after it address has a prefix to address it
    by."""
    assert {"hrn", "conv", "msg", "run", "art", "plg", "agt", "mem"} <= PREFIXES


@pytest.mark.unit
def test_parse_id_returns_none_for_a_value_that_is_not_a_string() -> None:
    """A `NULL` id read back from a legacy row arrives here as `None`.
    `ledger.record_change` calls this inside a write transaction, so raising
    would roll back the real write it was only meant to annotate."""
    assert parse_id("msg", None) is None  # type: ignore[arg-type]
    assert parse_id("msg", 17) is None  # type: ignore[arg-type]
