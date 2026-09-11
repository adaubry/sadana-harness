"""Tests for sadana.persona_store — the characters directory and the
per-account selection."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest

from conftest import open_conn, write_character
from sadana import persona, persona_store
from sadana.persona import CharacterError


@pytest.fixture
def conn() -> Iterator[sqlite3.Connection]:
    connection = open_conn()
    try:
        persona_store.ensure_schema(connection)
        yield connection
    finally:
        connection.close()


def _write(characters_dir: Path, name: str) -> Path:
    return write_character(
        name, description="how it sounds when I'm working", tone="dry", characters_dir=characters_dir
    )


@pytest.mark.unit
def test_character_path_refuses_a_bad_name_before_building_a_path(tmp_path: Path) -> None:
    for name in ["../escape", "with/slash", "None", persona.NEUTRAL_NAME]:
        with pytest.raises(CharacterError):
            persona_store.character_path(tmp_path, name)


@pytest.mark.unit
def test_character_path_refuses_a_symlink_that_resolves_out_of_the_directory(tmp_path: Path) -> None:
    characters = tmp_path / "characters"
    characters.mkdir()
    outside = write_character("outside", characters_dir=tmp_path)
    (characters / "sneaky.md").symlink_to(outside)
    with pytest.raises(CharacterError):
        persona_store.character_path(characters, "sneaky")


@pytest.mark.unit
def test_load_character_reads_a_file_written_by_hand(tmp_path: Path) -> None:
    _write(tmp_path, "working")
    assert persona_store.load_character(tmp_path, "working").tone == "dry"


@pytest.mark.unit
def test_load_character_raises_for_a_name_with_no_file(tmp_path: Path) -> None:
    with pytest.raises(CharacterError):
        persona_store.load_character(tmp_path, "absent")


@pytest.mark.unit
def test_list_characters_skips_a_file_that_does_not_parse(tmp_path: Path) -> None:
    _write(tmp_path, "good")
    (tmp_path / "broken.md").write_text("no frontmatter here\n", encoding="utf-8")
    assert [character.name for character in persona_store.list_characters(tmp_path)] == ["good"]


@pytest.mark.unit
def test_list_characters_is_empty_when_the_directory_does_not_exist(tmp_path: Path) -> None:
    assert persona_store.list_characters(tmp_path / "never-created") == ()


@pytest.mark.unit
def test_write_template_creates_a_character_that_immediately_parses(tmp_path: Path) -> None:
    path = persona_store.write_template(tmp_path, "fresh")
    assert path.exists()
    assert persona_store.load_character(tmp_path, "fresh").name == "fresh"


@pytest.mark.unit
def test_write_template_refuses_to_overwrite(tmp_path: Path) -> None:
    mine = _write(tmp_path, "mine")
    before = mine.read_text(encoding="utf-8")
    with pytest.raises(CharacterError):
        persona_store.write_template(tmp_path, "mine")
    assert mine.read_text(encoding="utf-8") == before


@pytest.mark.unit
def test_selection_round_trips_and_clears(conn: sqlite3.Connection) -> None:
    assert persona_store.get_selection(conn, "someone") is None
    persona_store.set_selection(conn, "someone", "working", now=1.0)
    assert persona_store.get_selection(conn, "someone") == "working"
    persona_store.set_selection(conn, "someone", "terse", now=2.0)
    assert persona_store.get_selection(conn, "someone") == "terse"
    persona_store.clear_selection(conn, "someone")
    assert persona_store.get_selection(conn, "someone") is None


@pytest.mark.unit
def test_selections_are_per_account(conn: sqlite3.Connection) -> None:
    persona_store.set_selection(conn, "one", "working", now=1.0)
    persona_store.set_selection(conn, "two", "terse", now=1.0)
    assert persona_store.get_selection(conn, "one") == "working"
    assert persona_store.get_selection(conn, "two") == "terse"


@pytest.mark.unit
def test_resolve_voice_is_the_selected_character(conn: sqlite3.Connection, tmp_path: Path) -> None:
    _write(tmp_path, "working")
    persona_store.set_selection(conn, "someone", "working", now=1.0)
    expected = persona.render(persona_store.load_character(tmp_path, "working"))
    assert persona_store.resolve_voice(conn, "someone", tmp_path) == expected


@pytest.mark.unit
def test_resolve_voice_is_neutral_with_no_selection(conn: sqlite3.Connection, tmp_path: Path) -> None:
    assert persona_store.resolve_voice(conn, "someone", tmp_path) == persona.NEUTRAL_VOICE


@pytest.mark.unit
def test_resolve_voice_survives_a_selected_character_that_was_deleted(conn: sqlite3.Connection, tmp_path: Path) -> None:
    _write(tmp_path, "working")
    persona_store.set_selection(conn, "someone", "working", now=1.0)
    (tmp_path / "working.md").unlink()
    assert persona_store.resolve_voice(conn, "someone", tmp_path) == persona.NEUTRAL_VOICE


@pytest.mark.unit
def test_resolve_voice_survives_a_selected_character_that_was_broken(conn: sqlite3.Connection, tmp_path: Path) -> None:
    path = _write(tmp_path, "working")
    persona_store.set_selection(conn, "someone", "working", now=1.0)
    path.write_text("someone deleted the frontmatter\n", encoding="utf-8")
    assert persona_store.resolve_voice(conn, "someone", tmp_path) == persona.NEUTRAL_VOICE


@pytest.mark.unit
def test_resolve_voice_survives_a_selection_whose_name_is_no_longer_valid(
    conn: sqlite3.Connection, tmp_path: Path
) -> None:
    persona_store.set_selection(conn, "someone", "../escape", now=1.0)
    assert persona_store.resolve_voice(conn, "someone", tmp_path) == persona.NEUTRAL_VOICE
