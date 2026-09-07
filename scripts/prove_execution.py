#!/usr/bin/env python3
"""Standalone proof that EXECUTION's run_http() reaches the real network.

Not a pytest test — testing-conventions bars the network from the unit
suite. This script is the CLAUDE.md-approved alternative: run it manually,
paste its output into review.md's ## Evidence. See
docs/tasks/E1-call-node-execution/spec.md ## Acceptance criteria.

Two real, unmocked round trips: a GET against a stable, IANA-reserved host
(Success), and a GET against a hostname that cannot resolve (Failure) —
both exercise run_http's real code path, not a mock.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from sadana.execution import Failure, HttpRequest, Success, run_http  # noqa: E402


def _print_outcome(label: str, outcome: object) -> None:
    print(f"\n=== {label} ===")
    print(repr(outcome))


def scenario_success() -> None:
    outcome = run_http(HttpRequest(method="GET", url="https://example.com"))
    _print_outcome("success (real GET against example.com)", outcome)
    assert isinstance(outcome, Success), f"expected Success, got {outcome!r}"
    assert outcome.status == 200, f"expected 200, got {outcome.status}"
    assert b"Example Domain" in outcome.body, "expected the real example.com body"


def scenario_failure() -> None:
    outcome = run_http(HttpRequest(method="GET", url="https://does-not-resolve.invalid"))
    _print_outcome("failure (real DNS resolution failure)", outcome)
    assert isinstance(outcome, Failure), f"expected Failure, got {outcome!r}"


if __name__ == "__main__":
    scenario_success()
    scenario_failure()
    print("\nboth scenarios matched their expected Outcome type.")
