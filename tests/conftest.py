"""Shared fixtures.

The autouse fixture below is the one testing-conventions calls the highest-
value fixture in the suite: no test may touch the real state directory. It is
here from the first commit so it can never be retrofitted onto a suite that
has already learned to depend on the developer's machine.
"""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _isolated_state(tmp_path, monkeypatch):  # type: ignore[no-untyped-def]
    """Every test runs against a temporary home. No exceptions."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("XDG_STATE_HOME", str(home / ".local" / "state"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(home / ".config"))
    monkeypatch.setenv("SADANA_STATE_DIR", str(home / ".sadana"))
    return home
