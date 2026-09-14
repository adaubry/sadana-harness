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
def test_declared_is_append_only_grammar_changes_inventory_then_artifacts_download() -> None:
    """H19 declared the first three (the door's own framework); H20
    (`docs/tasks/H20-door-nouns-turn-side/spec.md` requirement 27) appends
    `artifacts.download`, its one capability-gated action — never reordering
    or removing what H19 already declared."""
    assert capabilities.declared() == ("grammar.v1", "changes", "inventory", "artifacts.download")
