"""Tests for sadana.persona_store — the characters directory and the
per-account selection."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest

from conftest import conversation as build_conversation
from conftest import open_conn, write_character
from sadana import conversation_store, ledger, memory_store, persona, persona_store, stores
from sadana.persona import CharacterError


@pytest.fixture
def conn() -> Iterator[sqlite3.Connection]:
    connection = open_conn()
    try:
        # Every add-on table, not just PERSONA's: `known_accounts` reads all
        # three, which is the gap `stores.ensure_schemas` exists to close.
        stores.ensure_schemas(connection)
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


# ── PERSONA-02: who this assistant has spoken with ───────────────────────


@pytest.mark.unit
def test_known_accounts_sees_an_account_that_only_has_a_conversation(conn: sqlite3.Connection) -> None:
    conversation_store.create(
        conn,
        build_conversation(key="webhook:chat-9"),
        now=0.0,
        account_key="webhook:chat-9",  # pragma: allowlist secret
    )

    assert persona_store.known_accounts(conn) == ("webhook:chat-9",)


@pytest.mark.unit
def test_known_accounts_sees_an_account_that_only_has_a_memory(conn: sqlite3.Connection) -> None:
    memory_store.write_entry(conn, "adam", "tz", "UTC+2", now=1.0)

    assert persona_store.known_accounts(conn) == ("adam",)


@pytest.mark.unit
def test_known_accounts_sees_an_account_that_only_has_a_selection(conn: sqlite3.Connection) -> None:
    persona_store.set_selection(conn, "adam", "working", now=1.0)

    conversation_store.create(
        conn,
        build_conversation(key="c1"),
        now=0.0,
        account_key="adam",  # pragma: allowlist secret
    )


@pytest.mark.unit
def test_known_accounts_unions_the_three_sources_without_duplicating(conn: sqlite3.Connection) -> None:
    conversation_store.create(
        conn,
        build_conversation(key="c1"),
        now=0.0,
        account_key="adam",  # pragma: allowlist secret
    )
    memory_store.write_entry(conn, "adam", "tz", "UTC+2", now=1.0)
    persona_store.set_selection(conn, "adam", "working", now=1.0)
    memory_store.write_entry(conn, "webhook:9", "their_name", "Sam", now=1.0)

    assert persona_store.known_accounts(conn) == ("adam", "webhook:9")


@pytest.mark.unit
def test_account_exists_is_false_for_a_name_nothing_has_used(conn: sqlite3.Connection) -> None:
    assert persona_store.account_exists(conn, "nobody") is False


# ── H16: the selections ledger and the agents index ────────────────────────


@pytest.mark.unit
def test_selecting_and_clearing_a_character_record_changes(conn: sqlite3.Connection) -> None:
    persona_store.set_selection(conn, "a1", "reviewer", now=1.0)
    persona_store.set_selection(conn, "a1", "pirate", now=2.0)
    selection_id = conn.execute("SELECT id FROM persona_selections").fetchone()["id"]

    persona_store.clear_selection(conn, "a1")

    changes = [c for c in ledger.changes_since(conn, 0, 20) if c.id == selection_id]
    assert [c.kind for c in changes] == ["created", "changed", "deleted"]


@pytest.mark.unit
def test_reconciling_indexes_a_character_that_appeared(conn: sqlite3.Connection, tmp_path: Path) -> None:
    characters_dir = tmp_path / "characters"
    write_character("reviewer", characters_dir=characters_dir)

    persona_store.reconcile_agents(conn, characters_dir)

    row = conn.execute("SELECT id, name, version, state FROM agents").fetchone()
    assert (row["name"], row["version"], row["state"]) == ("reviewer", 1, "active")
    assert [(c.noun, c.kind, c.id) for c in ledger.changes_since(conn, 0, 10)] == [("agents", "created", row["id"])]


@pytest.mark.unit
def test_reconciling_an_unchanged_character_announces_nothing(conn: sqlite3.Connection, tmp_path: Path) -> None:
    """The common case — every open, every character, no change. A
    reconciliation that announced itself would drown the ledger in news about
    nothing."""
    characters_dir = tmp_path / "characters"
    write_character("reviewer", characters_dir=characters_dir)
    persona_store.reconcile_agents(conn, characters_dir)
    head = ledger.ledger_head(conn)

    persona_store.reconcile_agents(conn, characters_dir)

    assert ledger.ledger_head(conn) == head


@pytest.mark.unit
def test_reconciling_an_edited_character_bumps_its_version(conn: sqlite3.Connection, tmp_path: Path) -> None:
    """By content hash, not modification time: a file restored from a backup
    has an old mtime and new bytes."""
    characters_dir = tmp_path / "characters"
    write_character("reviewer", characters_dir=characters_dir)
    persona_store.reconcile_agents(conn, characters_dir)
    before = conn.execute("SELECT id, content_sha256 FROM agents").fetchone()

    write_character("reviewer", body="You read the tests first.", characters_dir=characters_dir)
    persona_store.reconcile_agents(conn, characters_dir)

    after = conn.execute("SELECT id, content_sha256, version FROM agents").fetchone()
    assert after["id"] == before["id"]
    assert after["content_sha256"] != before["content_sha256"]
    assert after["version"] == 2
    assert ledger.changes_since(conn, 0, 10)[-1].kind == "changed"


@pytest.mark.unit
def test_reconciling_a_vanished_character_tombstones_it(conn: sqlite3.Connection, tmp_path: Path) -> None:
    characters_dir = tmp_path / "characters"
    path = write_character("reviewer", characters_dir=characters_dir)
    persona_store.reconcile_agents(conn, characters_dir)
    agent_id = conn.execute("SELECT id FROM agents").fetchone()["id"]

    path.unlink()
    persona_store.reconcile_agents(conn, characters_dir)

    assert conn.execute("SELECT COUNT(*) FROM agents").fetchone()[0] == 0
    last = ledger.changes_since(conn, 0, 10)[-1]
    assert (last.kind, last.id) == ("deleted", agent_id)


@pytest.mark.unit
def test_a_character_that_does_not_parse_gets_no_row(conn: sqlite3.Connection, tmp_path: Path) -> None:
    """Matching `list_characters`' own posture: a half-written file is not yet
    a character, and a row would put it in front of the console as one."""
    characters_dir = tmp_path / "characters"
    characters_dir.mkdir(parents=True)
    (characters_dir / "broken.md").write_text("no front matter here", encoding="utf-8")

    persona_store.reconcile_agents(conn, characters_dir)

    assert conn.execute("SELECT COUNT(*) FROM agents").fetchone()[0] == 0


# ── H26: the door's own writes — draft agents, activation, description ────


@pytest.mark.unit
def test_create_character_writes_a_findable_draft(conn: sqlite3.Connection, tmp_path: Path) -> None:
    row = persona_store.create_character(conn, tmp_path, "helper", persona_store._TEMPLATE)

    assert row.state == "draft"
    assert [c.name for c in persona_store.list_characters(tmp_path)] == ["helper"]


@pytest.mark.unit
def test_create_character_refuses_to_overwrite(conn: sqlite3.Connection, tmp_path: Path) -> None:
    _write(tmp_path, "mine")
    with pytest.raises(CharacterError):
        persona_store.create_character(conn, tmp_path, "mine", persona_store._TEMPLATE)


@pytest.mark.unit
def test_create_character_records_template_and_description(conn: sqlite3.Connection, tmp_path: Path) -> None:
    row = persona_store.create_character(
        conn, tmp_path, "helper", persona_store._TEMPLATE, template="default", description="a helper voice"
    )

    assert (row.template, row.description) == ("default", "a helper voice")


@pytest.mark.unit
def test_create_character_survives_a_reconcile_pass_as_a_draft(conn: sqlite3.Connection, tmp_path: Path) -> None:
    """The property the whole design depends on: a reconciliation right
    after `create_character` must not silently promote the draft to
    `active`, because its content hash already matches the file."""
    row = persona_store.create_character(conn, tmp_path, "helper", persona_store._TEMPLATE)

    persona_store.reconcile_agents(conn, tmp_path)

    after = persona_store.get_agent_row(conn, row.id)
    assert after is not None
    assert after.state == "draft"
    assert after.version == 1


@pytest.mark.unit
def test_update_character_rewrites_content_and_bumps_version(conn: sqlite3.Connection, tmp_path: Path) -> None:
    row = persona_store.create_character(conn, tmp_path, "helper", persona_store._TEMPLATE)

    updated = persona_store.update_character(conn, tmp_path, "helper", content="A brand new voice.")

    assert updated.version == row.version + 1
    text = persona_store.read_character_text(tmp_path, "helper")
    assert "A brand new voice." in text
    assert persona_store.load_character(tmp_path, "helper").body == "A brand new voice."


@pytest.mark.unit
def test_update_character_wraps_plain_text_so_it_still_parses(conn: sqlite3.Connection, tmp_path: Path) -> None:
    """The bug `test_activate_requires_draft_state` first caught: content
    with no frontmatter of its own must not make the file unparseable, or
    the next `reconcile_agents` pass deletes the row out from under it."""
    persona_store.create_character(conn, tmp_path, "helper", "plain instructions, no frontmatter")

    persona_store.reconcile_agents(conn, tmp_path)

    assert [c.name for c in persona_store.list_characters(tmp_path)] == ["helper"]


@pytest.mark.unit
def test_update_character_description_is_independent_of_the_file(conn: sqlite3.Connection, tmp_path: Path) -> None:
    """`description` here is the door's own column — editing it must not
    touch the file's own frontmatter `description:` at all."""
    persona_store.create_character(conn, tmp_path, "helper", persona_store._TEMPLATE)
    before_text = persona_store.read_character_text(tmp_path, "helper")

    updated = persona_store.update_character(conn, tmp_path, "helper", description="a new description")

    assert updated.description == "a new description"
    assert persona_store.read_character_text(tmp_path, "helper") == before_text


@pytest.mark.unit
def test_update_character_requires_the_file_to_exist(conn: sqlite3.Connection, tmp_path: Path) -> None:
    with pytest.raises(CharacterError):
        persona_store.update_character(conn, tmp_path, "absent", content="anything")


@pytest.mark.unit
def test_update_character_with_neither_field_is_a_no_op(conn: sqlite3.Connection, tmp_path: Path) -> None:
    """An empty-body `PATCH` returns the row unchanged, and bumps nothing —
    the branch review found reading the writer connection outside `write_txn`
    (an unlocked read the door's `ThreadingHTTPServer` could race)."""
    row = persona_store.create_character(conn, tmp_path, "helper", persona_store._TEMPLATE)
    before_text = persona_store.read_character_text(tmp_path, "helper")

    unchanged = persona_store.update_character(conn, tmp_path, "helper")

    assert unchanged == row
    assert persona_store.read_character_text(tmp_path, "helper") == before_text


@pytest.mark.unit
def test_set_agent_state_activates_a_draft(conn: sqlite3.Connection, tmp_path: Path) -> None:
    row = persona_store.create_character(conn, tmp_path, "helper", persona_store._TEMPLATE)

    activated = persona_store.set_agent_state(conn, row.id, "active")

    assert activated.state == "active"
    assert activated.version == row.version + 1
    assert [(c.noun, c.kind) for c in ledger.changes_since(conn, 0, 10)][-1] == ("agents", "changed")


@pytest.mark.unit
def test_set_agent_state_rejects_an_unknown_state(conn: sqlite3.Connection, tmp_path: Path) -> None:
    row = persona_store.create_character(conn, tmp_path, "helper", persona_store._TEMPLATE)

    with pytest.raises(ValueError):
        persona_store.set_agent_state(conn, row.id, "retired")


@pytest.mark.unit
def test_list_agent_rows_reflects_the_index(conn: sqlite3.Connection, tmp_path: Path) -> None:
    characters_dir = tmp_path / "characters"
    write_character("reviewer", characters_dir=characters_dir)
    persona_store.reconcile_agents(conn, characters_dir)

    rows = persona_store.list_agent_rows(conn)

    assert [row.name for row in rows] == ["reviewer"]
    assert rows[0].state == "active"
