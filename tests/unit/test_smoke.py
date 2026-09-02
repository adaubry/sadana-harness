"""The suite must never be empty — an empty run exits 5 and is a hard failure.

This asserts a contract (the package imports and exposes a parseable version),
not a snapshot (the version's literal value), per testing-conventions.
"""

import pytest

import sadana


@pytest.mark.unit
def test_package_exposes_a_parseable_version() -> None:
    parts = sadana.__version__.split(".")
    assert len(parts) == 3
    assert all(p.isdigit() for p in parts)


@pytest.mark.unit
def test_state_is_isolated(_isolated_state) -> None:  # type: ignore[no-untyped-def]
    import os

    assert os.environ["SADANA_STATE_DIR"].startswith(str(_isolated_state))
