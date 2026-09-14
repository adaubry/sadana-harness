"""`door/capabilities.py` — the closed capability list and what H19 declares."""

from __future__ import annotations

import pytest

from sadana.door import capabilities


@pytest.mark.unit
def test_all_has_exactly_sixteen_entries() -> None:
    assert len(capabilities.ALL) == 16
    assert len(set(capabilities.ALL)) == 16  # no duplicates


@pytest.mark.unit
def test_declared_is_a_subset_of_all() -> None:
    assert set(capabilities.DECLARED) <= set(capabilities.ALL)


@pytest.mark.unit
def test_declared_includes_at_least_h19s_own_grammar_changes_inventory() -> None:
    # Not an exact-list check: `DECLARED` is append-only, and every later
    # work item's own capability (H18's own two, and whatever lands beside
    # them) grows it further. This proves H19's own contribution is still
    # there, not that nothing has been added since.
    assert set(capabilities.declared()) >= {"grammar.v1", "changes", "inventory"}


@pytest.mark.unit
def test_declared_includes_h21s_own_three() -> None:
    assert set(capabilities.declared()) >= {"streaming", "runs.live", "runs.stop"}
