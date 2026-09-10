"""Tests for sadana.memory_store — the sqlite half of MEMORY-01.

Uses `conftest.open_conn()` against this test's own isolated state
directory (the autouse `_isolated_state` fixture), never the real store.
"""

from __future__ import annotations

import pytest

from conftest import open_conn
from sadana.memory_store import (
    delete_entry,
    ensure_plugin_seeded,
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


@pytest.mark.unit
def test_ensure_plugin_seeded_materializes_plugin_toml(tmp_path) -> None:  # type: ignore[no-untyped-def]
    plugins_root = tmp_path / "plugins"
    ensure_plugin_seeded(plugins_root)
    assert (plugins_root / "memory" / "plugin.toml").is_file()
    assert (plugins_root / "memory" / "init.py").is_file()
    assert (plugins_root / "memory" / "schema" / "remember.json").is_file()


@pytest.mark.unit
def test_ensure_plugin_seeded_is_idempotent(tmp_path) -> None:  # type: ignore[no-untyped-def]
    plugins_root = tmp_path / "plugins"
    ensure_plugin_seeded(plugins_root)
    marker = plugins_root / "memory" / "plugin.toml"
    original = marker.read_text(encoding="utf-8")
    marker.write_text(original + "\n# local edit\n", encoding="utf-8")
    ensure_plugin_seeded(plugins_root)  # must not overwrite an existing directory
    assert marker.read_text(encoding="utf-8") == original + "\n# local edit\n"
