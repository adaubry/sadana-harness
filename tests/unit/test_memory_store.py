"""Tests for sadana.memory_store — the sqlite half of MEMORY-01.

Uses `conftest.open_conn()` against this test's own isolated state
directory (the autouse `_isolated_state` fixture), never the real store.
"""

from __future__ import annotations

import pytest

from conftest import open_conn
from sadana.memory_store import (
    accounts_with_memories,
    adopt_scheduled_memories,
    delete_entry,
    ensure_schema,
    get_rubric_override,
    list_entries,
    set_rubric_override,
    write_entry,
)


@pytest.fixture
def conn():  # type: ignore[no-untyped-def]
    c = open_conn()
    ensure_schema(c)
    try:
        yield c
    finally:
        c.close()


@pytest.mark.unit
def test_write_entry_then_list_entries(conn) -> None:  # type: ignore[no-untyped-def]
    write_entry(conn, "a1", "dog_name", "Buddy", now=1.0)
    entries = list_entries(conn, "a1")
    assert len(entries) == 1
    assert entries[0].entry_key == "dog_name"
    assert entries[0].content == "Buddy"


@pytest.mark.unit
def test_write_entry_same_key_updates_not_duplicates(conn) -> None:  # type: ignore[no-untyped-def]
    write_entry(conn, "a1", "dog_name", "Buddy", now=1.0)
    write_entry(conn, "a1", "dog_name", "Max", now=2.0)
    entries = list_entries(conn, "a1")
    assert len(entries) == 1
    assert entries[0].content == "Max"


@pytest.mark.unit
def test_list_entries_never_leaks_across_accounts(conn) -> None:  # type: ignore[no-untyped-def]
    write_entry(conn, "a1", "dog_name", "Buddy", now=1.0)
    write_entry(conn, "a2", "dog_name", "Rex", now=1.0)
    assert [e.content for e in list_entries(conn, "a1")] == ["Buddy"]
    assert [e.content for e in list_entries(conn, "a2")] == ["Rex"]


@pytest.mark.unit
def test_delete_entry_removes_it(conn) -> None:  # type: ignore[no-untyped-def]
    write_entry(conn, "a1", "dog_name", "Buddy", now=1.0)
    delete_entry(conn, "a1", "dog_name")
    assert list_entries(conn, "a1") == ()


@pytest.mark.unit
def test_delete_entry_only_affects_its_own_account(conn) -> None:  # type: ignore[no-untyped-def]
    write_entry(conn, "a1", "dog_name", "Buddy", now=1.0)
    write_entry(conn, "a2", "dog_name", "Rex", now=1.0)
    delete_entry(conn, "a1", "dog_name")
    assert list_entries(conn, "a1") == ()
    assert [e.content for e in list_entries(conn, "a2")] == ["Rex"]


@pytest.mark.unit
def test_rubric_override_defaults_to_empty_string(conn) -> None:  # type: ignore[no-untyped-def]
    assert get_rubric_override(conn, "a1") == ""


@pytest.mark.unit
def test_set_rubric_override_then_get(conn) -> None:  # type: ignore[no-untyped-def]
    set_rubric_override(conn, "a1", "also remember their timezone", now=1.0)
    assert get_rubric_override(conn, "a1") == "also remember their timezone"


@pytest.mark.unit
def test_rubric_override_same_account_updates_not_duplicates(conn) -> None:  # type: ignore[no-untyped-def]
    set_rubric_override(conn, "a1", "first", now=1.0)
    set_rubric_override(conn, "a1", "second", now=2.0)
    assert get_rubric_override(conn, "a1") == "second"


@pytest.mark.unit
def test_rubric_override_never_leaks_across_accounts(conn) -> None:  # type: ignore[no-untyped-def]
    set_rubric_override(conn, "a1", "a1's own rubric", now=1.0)
    assert get_rubric_override(conn, "a2") == ""


# ── PERSONA-02: what the schedules remembered becomes the owner's ────────


@pytest.mark.unit
def test_adoption_moves_every_scheduled_account_onto_the_owner(conn) -> None:  # type: ignore[no-untyped-def]
    write_entry(conn, "schedule:daily", "standup_time", "09:00", now=1.0)
    write_entry(conn, "schedule:weekly", "report_format", "bullets", now=2.0)
    write_entry(conn, "webhook:42", "their_name", "Sam", now=3.0)

    assert adopt_scheduled_memories(conn, "adam") == 2

    adopted = {e.entry_key: e.content for e in list_entries(conn, "adam")}
    assert adopted == {"standup_time": "09:00", "report_format": "bullets"}
    assert accounts_with_memories(conn) == frozenset({"adam", "webhook:42"})


@pytest.mark.unit
def test_adoption_keeps_both_texts_when_the_owner_already_holds_the_key(conn) -> None:  # type: ignore[no-untyped-def]
    """The intent's "loses nothing" is stronger than tidiness: the incoming
    entry gets an uglier name rather than overwriting one the owner had."""
    write_entry(conn, "adam", "standup_time", "whenever I get up", now=1.0)
    write_entry(conn, "schedule:daily", "standup_time", "09:00", now=2.0)

    assert adopt_scheduled_memories(conn, "adam") == 1

    adopted = {e.entry_key: e.content for e in list_entries(conn, "adam")}
    assert adopted["standup_time"] == "whenever I get up"
    assert adopted["standup_time--schedule:daily"] == "09:00"


@pytest.mark.unit
def test_adoption_is_idempotent(conn) -> None:  # type: ignore[no-untyped-def]
    write_entry(conn, "schedule:daily", "standup_time", "09:00", now=1.0)
    assert adopt_scheduled_memories(conn, "adam") == 1

    assert adopt_scheduled_memories(conn, "adam") == 0
    assert [e.entry_key for e in list_entries(conn, "adam")] == ["standup_time"]


@pytest.mark.unit
def test_adoption_leaves_a_scheduled_accounts_settings_alone(conn) -> None:  # type: ignore[no-untyped-def]
    """Only things the assistant *learned* move. A rubric addition is
    something a person typed, and moving it would overwrite one the owner
    chose (spec.md requirement 5)."""
    set_rubric_override(conn, "adam", "what the owner asked for", now=1.0)
    set_rubric_override(conn, "schedule:daily", "what the trigger asked for", now=2.0)
    write_entry(conn, "schedule:daily", "standup_time", "09:00", now=3.0)

    adopt_scheduled_memories(conn, "adam")

    assert get_rubric_override(conn, "adam") == "what the owner asked for"
    assert get_rubric_override(conn, "schedule:daily") == "what the trigger asked for"


@pytest.mark.unit
def test_adoption_refuses_to_run_when_the_owner_is_itself_a_schedule(conn) -> None:
    """A configuration nobody should have, and one that would otherwise
    migrate rows onto themselves."""
    write_entry(conn, "schedule:daily", "standup_time", "09:00", now=1.0)

    assert adopt_scheduled_memories(conn, "schedule:daily") == 0
    assert [e.entry_key for e in list_entries(conn, "schedule:daily")] == ["standup_time"]


@pytest.mark.unit
def test_accounts_with_memories_is_empty_on_a_fresh_store(conn) -> None:  # type: ignore[no-untyped-def]
    assert accounts_with_memories(conn) == frozenset()
