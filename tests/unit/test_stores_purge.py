"""`stores.PURGE_STEPS`/`purge_account` — P11's "everything held for an
account can be enumerated for a purge", proven two ways: every row for the
named account is gone and every other account's row survives, and the
completeness test that would have caught this list going stale."""

from __future__ import annotations

from pathlib import Path

import pytest

from sadana import stores


def _conns(tmp_path: Path) -> stores.Connections:
    return stores.Connections(tmp_path / "p.db")


def _seed(conns: stores.Connections, account_key: str) -> None:
    c = conns.writer
    c.execute(
        "INSERT INTO memory_entries (account_key, entry_key, content, updated_at) VALUES (?, 'k', 'v', 1.0)",
        (account_key,),
    )
    c.execute(
        "INSERT INTO memory_rubric_overrides (account_key, rubric_text, updated_at) VALUES (?, 'r', 1.0)",
        (account_key,),
    )
    c.execute("INSERT INTO persona_selections (account_key, name, updated_at) VALUES (?, 'n', 1.0)", (account_key,))
    c.execute(
        "INSERT INTO schedules (id, name, trigger_text, account_key, next_run_at, created_at, updated_at) "
        "VALUES (?, ?, 't', ?, 1.0, 1.0, 1.0)",
        (f"sch_{account_key}", f"name_{account_key}", account_key),
    )
    c.execute(
        "INSERT INTO conversation_accounts (conversation_key, account_key) VALUES (?, ?)",
        (f"conv_{account_key}", account_key),
    )


def _row_counts(conns: stores.Connections, account_key: str) -> dict[str, int]:
    c = conns.writer
    return {
        table: c.execute(f"SELECT COUNT(*) FROM {table} WHERE {column} = ?", (account_key,)).fetchone()[0]  # noqa: S608
        for table, column in stores.PURGE_STEPS
    }


@pytest.mark.unit
def test_purge_account_removes_every_row_for_that_account_only(tmp_path: Path) -> None:
    conns = _conns(tmp_path)
    _seed(conns, "acct_purge_me")
    _seed(conns, "acct_keep_me")

    stores.purge_account(conns, "acct_purge_me")

    purged = _row_counts(conns, "acct_purge_me")
    kept = _row_counts(conns, "acct_keep_me")
    assert all(count == 0 for count in purged.values()), purged
    assert all(count == 1 for count in kept.values()), kept


@pytest.mark.unit
def test_purge_account_on_an_account_with_no_rows_is_a_no_op(tmp_path: Path) -> None:
    conns = _conns(tmp_path)
    stores.purge_account(conns, "acct_never_existed")  # must not raise


@pytest.mark.unit
def test_purge_steps_names_every_table_with_an_account_key_column(tmp_path: Path) -> None:
    """The mechanism Requirement 15 asks for: a table added later with an
    `account_key` column and forgotten here fails this test by name,
    instead of silently keeping that account's data past a purge."""
    conns = _conns(tmp_path)
    rows = conns.writer.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()
    named_in_purge_steps = {table for table, _column in stores.PURGE_STEPS}

    missing: list[str] = []
    for row in rows:
        table = row["name"]
        columns = {c["name"] for c in conns.writer.execute(f"PRAGMA table_info({table})")}  # noqa: S608
        if "account_key" in columns and table not in named_in_purge_steps:
            missing.append(table)

    assert not missing, f"tables with an account_key column missing from stores.PURGE_STEPS: {missing}"
