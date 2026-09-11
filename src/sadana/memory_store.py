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

import shutil
import sqlite3
import tempfile
from dataclasses import dataclass
from pathlib import Path

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

# The on-disk source of truth for the first-party `memory` plugin, shipped
# inside this package so `ensure_plugin_seeded` can materialize it into a
# fresh `_plugins_root()` without a network fetch — plugin distribution
# (PLUGIN-INSTALL-01) is unaffected: this is not installed *through* that
# pipeline, only placed where `discover_plugins()` already looks.
_BUILTIN_PLUGIN_SOURCE = Path(__file__).resolve().parent / "builtin_plugins" / "memory"


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


#: The account-key prefix a scheduled trigger used to run under, before
#: PERSONA-02 made scheduled work the owner's own. Only ever read, by the
#: adoption below.
_SCHEDULE_PREFIX = "schedule:"


def accounts_with_memories(conn: sqlite3.Connection) -> frozenset[memory.AccountKey]:
    """Every account something has been remembered about."""
    rows = conn.execute("SELECT DISTINCT account_key FROM memory_entries").fetchall()
    return frozenset(row["account_key"] for row in rows)


def adopt_scheduled_memories(conn: sqlite3.Connection, owner: memory.AccountKey) -> int:
    """Move what scheduled runs remembered under their own names into
    `owner`'s memory, and return how many entries moved.

    PERSONA-02: a trigger the owner wrote is the owner's own machinery, so
    what it learned is theirs. Before that item, every scheduled run
    remembered under `schedule:<trigger name>`, where nothing would ever read
    it again once scheduling started running as the owner.

    Loses nothing, which is the intent's own constraint and the reason this
    is not a bare `UPDATE`: where the owner already holds an entry under the
    same key, the incoming one is kept under `<entry_key>--<source account>`
    rather than overwriting a fact the owner already had. Both texts survive;
    one of them gets an uglier name.

    Idempotent: after one pass no `schedule:` row is left, so every later call
    finds nothing and returns 0. That is what makes it safe to run on every
    process start rather than from a command somebody has to remember.

    Does nothing at all if the owner's own account starts with `schedule:` —
    a configuration nobody should have, and one that would otherwise have
    rows migrating onto themselves.
    """
    if owner.startswith(_SCHEDULE_PREFIX):
        return 0
    rows = conn.execute(
        "SELECT account_key, entry_key, content, updated_at FROM memory_entries WHERE account_key LIKE ?",
        (_SCHEDULE_PREFIX + "%",),
    ).fetchall()
    if not rows:
        return 0
    with write_txn(conn) as c:
        for row in rows:
            taken = c.execute(
                "SELECT 1 FROM memory_entries WHERE account_key = ? AND entry_key = ?",
                (owner, row["entry_key"]),
            ).fetchone()
            entry_key = f"{row['entry_key']}--{row['account_key']}" if taken else row["entry_key"]
            c.execute(
                "INSERT INTO memory_entries (account_key, entry_key, content, updated_at) VALUES (?, ?, ?, ?) "
                "ON CONFLICT (account_key, entry_key) "
                "DO UPDATE SET content = excluded.content, updated_at = excluded.updated_at",
                (owner, entry_key, row["content"], row["updated_at"]),
            )
            c.execute(
                "DELETE FROM memory_entries WHERE account_key = ? AND entry_key = ?",
                (row["account_key"], row["entry_key"]),
            )
    return len(rows)


def ensure_plugin_seeded(plugins_root: Path) -> None:
    """If `plugins_root / "memory"` doesn't exist yet, copies the shipped
    plugin source there — the "if missing, write the default" idiom, for a
    directory tree rather than one file. A no-op on every call after the first.

    Copies into a sibling temp directory first and renames it into place,
    so a process killed mid-copy never leaves a half-written `memory/`
    directory for `discover_plugins()` to trip over — the rename is one
    atomic filesystem op, same as `open()`+`os.replace()`-style writes
    elsewhere in this project. Not guarding against a second *process*
    racing this one: this project's own posture is single-writer until a
    real concurrent-caller incident shows up (CLAUDE.md), not before."""
    destination = plugins_root / "memory"
    if destination.exists():
        return
    plugins_root.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(dir=plugins_root))
    shutil.copytree(_BUILTIN_PLUGIN_SOURCE, staging, dirs_exist_ok=True)
    try:
        staging.rename(destination)
    except OSError:
        shutil.rmtree(staging, ignore_errors=True)
