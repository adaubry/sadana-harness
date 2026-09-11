"""The sqlite half of MEMORY-01 — the one that touches real I/O.

`docs/tasks/MEMORY-01-write-recall-and-forget/spec.md`. Its own file per
CLAUDE.md's rule that a module touching real I/O never shares a file with a
block's pure-function module (`memory.py`).

Reuses `conversation_store`'s own connection and file: no second SQLite
store, no second writer (CLAUDE.md's warning about a second writer racing
one SQLite WAL file). Two tables, `memory_entries` and
`memory_rubric_overrides`, each keyed by the natural key spec.md names —
`(account_key, entry_key)` and `account_key` respectively — never a
synthetic id.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from sadana import memory
from sadana.conversation_store import write_txn

_SCHEMA = """
CREATE TABLE IF NOT EXISTS memory_entries (
    account_key TEXT NOT NULL,
    entry_key   TEXT NOT NULL,
    content     TEXT NOT NULL,
    updated_at  REAL NOT NULL,
    PRIMARY KEY (account_key, entry_key)
);

CREATE TABLE IF NOT EXISTS memory_rubric_overrides (
    account_key TEXT NOT NULL PRIMARY KEY,
    rubric_text TEXT NOT NULL,
    updated_at  REAL NOT NULL
);
"""


@dataclass(frozen=True)
class DispatchContext:
    """What `plugin_dispatch.build_dispatch()`'s new `memory_context`
    parameter carries into the memory plugin's own `call` node — the
    trusted account identity plus the already-open connection to write
    through, never a model-suppliable value (spec.md's identity-channel
    design)."""

    account_key: memory.AccountKey
    conn: sqlite3.Connection


def ensure_schema(conn: sqlite3.Connection) -> None:
    """Idempotent — safe on a fresh store or one that predates this item,
    the same "create tables on open" posture `conversation_store.open_store()`
    and `observability.make_recorder()` already take."""
    conn.executescript(_SCHEMA)


def write_entry(
    conn: sqlite3.Connection, account_key: memory.AccountKey, entry_key: str, content: str, *, now: float
) -> None:
    """Writing the same `(account_key, entry_key)` again updates the row in
    place — the primary key enforces this at the database level, not by any
    check in this function."""
    with write_txn(conn) as c:
        c.execute(
            "INSERT INTO memory_entries (account_key, entry_key, content, updated_at) VALUES (?, ?, ?, ?) "
            "ON CONFLICT (account_key, entry_key) "
            "DO UPDATE SET content = excluded.content, updated_at = excluded.updated_at",
            (account_key, entry_key, content, now),
        )


def delete_entry(conn: sqlite3.Connection, account_key: memory.AccountKey, entry_key: str) -> None:
    with write_txn(conn) as c:
        c.execute(
            "DELETE FROM memory_entries WHERE account_key = ? AND entry_key = ?",
            (account_key, entry_key),
        )


def list_entries(conn: sqlite3.Connection, account_key: memory.AccountKey) -> tuple[memory.MemoryEntry, ...]:
    """Every entry for `account_key`, and only that account's — this `WHERE`
    clause is the entire enforcement of spec.md requirement 6 (no
    cross-account leakage) on the read side."""
    rows = conn.execute(
        "SELECT account_key, entry_key, content, updated_at FROM memory_entries "
        "WHERE account_key = ? ORDER BY entry_key",
        (account_key,),
    ).fetchall()
    return tuple(
        memory.MemoryEntry(
            account_key=row["account_key"],
            entry_key=row["entry_key"],
            content=row["content"],
            updated_at=row["updated_at"],
        )
        for row in rows
    )


def get_rubric_override(conn: sqlite3.Connection, account_key: memory.AccountKey) -> str:
    """`""` when the account has never set one — the deployer's default
    (`memory.default_rubric()`) is what fills that gap, not this function."""
    row = conn.execute(
        "SELECT rubric_text FROM memory_rubric_overrides WHERE account_key = ?",
        (account_key,),
    ).fetchone()
    return row["rubric_text"] if row is not None else ""


def set_rubric_override(conn: sqlite3.Connection, account_key: memory.AccountKey, text: str, *, now: float) -> None:
    with write_txn(conn) as c:
        c.execute(
            "INSERT INTO memory_rubric_overrides (account_key, rubric_text, updated_at) VALUES (?, ?, ?) "
            "ON CONFLICT (account_key) "
            "DO UPDATE SET rubric_text = excluded.rubric_text, updated_at = excluded.updated_at",
            (account_key, text, now),
        )
