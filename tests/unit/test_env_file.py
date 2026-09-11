"""Tests for sadana.env_file — the write half of state_dir/.env.

These moved out of test_subcommands_setup.py with the code they cover.
Everything still in that file is about `sadana setup`'s own behaviour and
passes unedited, which is what makes that move a move.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from sadana.env_file import drop_key, env_line_key, env_path, quote_env_value, upsert_key


def _read_env(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if "=" in line:
            key, _, value = line.partition("=")
            out[key.strip()] = value.strip().strip('"')
    return out


@pytest.mark.unit
def test_env_path_is_under_the_state_directory() -> None:
    from sadana import config

    assert env_path() == config.get_paths().state_dir / ".env"


@pytest.mark.unit
def test_upsert_creates_the_file_when_it_does_not_exist() -> None:
    path = env_path()
    upsert_key(path, "SADANA_PLUGIN__WEATHER__UNITS", "metric")
    assert _read_env(path) == {"SADANA_PLUGIN__WEATHER__UNITS": "metric"}


@pytest.mark.unit
def test_upsert_creates_the_file_readable_only_by_its_owner() -> None:
    """The file holds secrets, so it must never be world-readable even for
    the instant between creation and any later chmod."""
    path = env_path()
    upsert_key(path, "SADANA_PLUGIN__WEATHER__API_KEY", "value")
    assert path.stat().st_mode & 0o777 == 0o600


@pytest.mark.unit
def test_upsert_replaces_an_existing_assignment_and_leaves_the_rest() -> None:
    path = env_path()
    upsert_key(path, "A", "one")
    upsert_key(path, "B", "two")
    upsert_key(path, "A", "three")
    text = path.read_text(encoding="utf-8")
    assert text.count("A=") == 1
    assert _read_env(path) == {"A": "three", "B": "two"}


@pytest.mark.unit
def test_upsert_replaces_a_hand_spaced_assignment_rather_than_shadowing_it() -> None:
    """`load_dotenv` strips the key's whitespace, so `KEY = v` and `KEY=v`
    are the same assignment; appending a second would shadow the first and
    which one wins would depend on file order."""
    path = env_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('A = "old"\nOTHER="x"\n', encoding="utf-8")
    upsert_key(path, "A", "new")
    text = path.read_text(encoding="utf-8")
    assert text.count("A") == text.count('A="new"')
    assert 'OTHER="x"' in text


@pytest.mark.unit
def test_drop_removes_every_assignment_of_a_key() -> None:
    path = env_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('A="one"\nB="two"\nA = "again"\n', encoding="utf-8")
    drop_key(path, "A")
    assert _read_env(path) == {"B": "two"}


@pytest.mark.unit
def test_drop_on_a_missing_file_does_nothing() -> None:
    path = env_path()
    drop_key(path, "A")
    assert not path.exists()


@pytest.mark.unit
def test_env_line_key_reads_an_assignment_and_ignores_anything_else() -> None:
    assert env_line_key('A="one"') == "A"
    assert env_line_key("  A = one") == "A"
    assert env_line_key("# a comment") == ""
    assert env_line_key("") == ""


@pytest.mark.unit
def test_quote_env_value_strips_newlines_so_a_value_cannot_inject_a_second_line() -> None:
    value = 'abc"\nexport OPENROUTER_API_KEY="PWNED'
    quoted = quote_env_value(value)
    assert "\n" not in quoted
    assert "\r" not in quoted
    path = env_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"OPENROUTER_API_KEY={quoted}\n", encoding="utf-8")
    assert _read_env(path)["OPENROUTER_API_KEY"] == 'abc"export OPENROUTER_API_KEY="PWNED'
