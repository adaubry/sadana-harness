"""`tether/backoff.py` — full-jitter exponential backoff, pure."""

from __future__ import annotations

import random

import pytest

from sadana.tether import backoff


@pytest.mark.unit
@pytest.mark.parametrize("attempt", range(11))
def test_next_delay_stays_within_bounds(attempt: int) -> None:
    rng = random.Random(attempt)
    delay = backoff.next_delay(attempt, rng=rng)
    assert 0 <= delay <= min(60.0, 1.0 * 2**attempt)


@pytest.mark.unit
def test_next_delay_is_capped_for_large_attempts() -> None:
    rng = random.Random(0)
    for _ in range(50):
        assert backoff.next_delay(20, rng=rng) <= 60.0


@pytest.mark.unit
def test_next_delay_honours_custom_base_and_cap() -> None:
    rng = random.Random(1)
    for _ in range(50):
        delay = backoff.next_delay(3, base=2.0, cap=10.0, rng=rng)
        assert 0 <= delay <= 10.0
