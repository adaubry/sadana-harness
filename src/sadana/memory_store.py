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
import time
from dataclasses import dataclass

from sadana import ids, ledger, memory
from sadana.conversation_store import fill_legacy_identity, migrate_columns, write_txn

_SCHEMA = """
CREATE TABLE IF NOT EXISTS memory_entries (
    account_key TEXT NOT NULL,
    entry_key   TEXT NOT NULL,
    content     TEXT NOT NULL,
    updated_at  REAL NOT NULL,
    -- H16. `kind`, `source` and `conversation_key` have no writer here: they
    -- are what H26 fills when memory gains provenance. `state` does: 'kept'
    -- until `forget_entry` makes it 'forgotten'.
    id          TEXT,
    created_at  REAL,
    version     INTEGER NOT NULL DEFAULT 1,
    state       TEXT NOT NULL DEFAULT 'kept',
    kind        TEXT,
    source      TEXT,
    conversation_key TEXT,
    PRIMARY KEY (account_key, entry_key)
);

CREATE TABLE IF NOT EXISTS memory_rubric_overrides (
    account_key TEXT NOT NULL PRIMARY KEY,
    rubric_text TEXT NOT NULL,
    updated_at  REAL NOT NULL,
    -- H16. `updated_by` is written by H19, which is the first thing that knows
    -- which principal made a change.
    id          TEXT,
    created_at  REAL,
    version     INTEGER NOT NULL DEFAULT 1,
    updated_by  TEXT
);
"""

#: See `conversation_store._MIGRATED_COLUMNS`. One map per module, beside the
#: `_SCHEMA` it mirrors.
_MIGRATED_COLUMNS: dict[str, tuple[tuple[str, str], ...]] = {
    "memory_entries": (
        ("id", "TEXT"),
        ("created_at", "REAL"),
        ("version", "INTEGER NOT NULL DEFAULT 1"),
        ("state", "TEXT NOT NULL DEFAULT 'kept'"),
        ("kind", "TEXT"),
        ("source", "TEXT"),
        ("conversation_key", "TEXT"),
    ),
    "memory_rubric_overrides": (
        ("id", "TEXT"),
        ("created_at", "REAL"),
        ("version", "INTEGER NOT NULL DEFAULT 1"),
        ("updated_by", "TEXT"),
    ),
}


#: Every word `memory_entries.state` may hold. `forgotten` is a fact the
#: assistant has been asked to stop using but has not destroyed — see
#: `forget_entry`.
ENTRY_STATES = frozenset({"kept", "forgotten"})


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
    migrate_columns(conn, _MIGRATED_COLUMNS)
    fill_legacy_identity(conn, "memory_entries", "mem", time_columns=("created_at",))
    fill_legacy_identity(conn, "memory_rubric_overrides", "rub", time_columns=("created_at",))


def write_entry(
    conn: sqlite3.Connection, account_key: memory.AccountKey, entry_key: str, content: str, *, now: float
) -> None:
    """Writing the same `(account_key, entry_key)` again updates the row in
    place — the primary key enforces this at the database level, not by any
    check in this function.

    Rewriting a forgotten entry brings it back: `state` returns to 'kept'.
    Somebody telling the assistant the same fact again means they want it
    known, and leaving it forgotten would make the write silently do nothing.
    """
    at = time.time()
    with write_txn(conn) as c:
        existed = c.execute(
            "SELECT id FROM memory_entries WHERE account_key = ? AND entry_key = ?", (account_key, entry_key)
        ).fetchone()
        c.execute(
            "INSERT INTO memory_entries (account_key, entry_key, content, updated_at, id, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?) "
            "ON CONFLICT (account_key, entry_key) "
            "DO UPDATE SET content = excluded.content, updated_at = excluded.updated_at, "
            "state = 'kept', version = memory_entries.version + 1",
            (account_key, entry_key, content, now, ids.make_id("mem"), at),
        )
        _record(c, account_key, entry_key, kind="changed" if existed else "created", at=at)


def _record(c: sqlite3.Connection, account_key: memory.AccountKey, entry_key: str, *, kind: str, at: float) -> None:
    """The ledger row for one entry, reading back what the write just made it.

    Inside the caller's transaction, so the row and its announcement commit or
    roll back together. The read is what makes the announced `version` the one
    that actually committed rather than one this function guessed.
    """
    row = c.execute(
        "SELECT id, state, version FROM memory_entries WHERE account_key = ? AND entry_key = ?",
        (account_key, entry_key),
    ).fetchone()
    if row is None or row["id"] is None:
        return
    ledger.record_change(
        c, noun="memory_entries", id=row["id"], kind=kind, state=row["state"], version=row["version"], at=at
    )


def forget_entry(conn: sqlite3.Connection, account_key: memory.AccountKey, entry_key: str, *, now: float) -> None:
    """Stop using an entry without destroying it — H16 requirement 18.

    Forgetting is a **state**, not a deletion. Two reasons, and the second is
    the one that made it a requirement. A person who says "forget that" is
    asking the assistant to stop acting on a fact, which is not the same as
    asking for the record of it to be unrecoverable. And a mirror that only
    ever sees rows vanish cannot tell a forget from a purge from a bug — the
    console's own register calls that B10.

    `delete_entry` remains, and is now genuinely a purge.
    """
    at = time.time()
    with write_txn(conn) as c:
        c.execute(
            "UPDATE memory_entries SET state = 'forgotten', updated_at = ?, version = version + 1 "
            "WHERE account_key = ? AND entry_key = ?",
            (now, account_key, entry_key),
        )
        _record(c, account_key, entry_key, kind="changed", at=at)


def delete_entry(conn: sqlite3.Connection, account_key: memory.AccountKey, entry_key: str) -> None:
    """The purge. Leaves a tombstone: the ledger row is written **before** the
    row goes, inside the same transaction, because afterwards there is no id
    left to name it by."""
    at = time.time()
    with write_txn(conn) as c:
        row = c.execute(
            "SELECT id FROM memory_entries WHERE account_key = ? AND entry_key = ?", (account_key, entry_key)
        ).fetchone()
        if row is not None and row["id"] is not None:
            ledger.record_change(
                c, noun="memory_entries", id=row["id"], kind="deleted", state=None, version=None, at=at
            )
        c.execute(
            "DELETE FROM memory_entries WHERE account_key = ? AND entry_key = ?",
            (account_key, entry_key),
        )


def list_entries(conn: sqlite3.Connection, account_key: memory.AccountKey) -> tuple[memory.MemoryEntry, ...]:
    """Every entry for `account_key`, and only that account's — this `WHERE`
    clause is the entire enforcement of spec.md requirement 6 (no
    cross-account leakage) on the read side.

    Forgotten entries are excluded, which is what makes `forget_entry` mean
    anything (H16 requirement 18). Both readers go through here — the `memory
    list` command and `client_surface._create`, which composes the system
    message — so a forgotten fact stops being shown to the person *and* stops
    being told to the model, from one clause. The row is still on disk and
    still in the inventory; it is no longer recalled.
    """
    rows = conn.execute(
        "SELECT account_key, entry_key, content, updated_at FROM memory_entries "
        "WHERE account_key = ? AND state != 'forgotten' ORDER BY entry_key",
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
    at = time.time()
    with write_txn(conn) as c:
        existed = c.execute("SELECT id FROM memory_rubric_overrides WHERE account_key = ?", (account_key,)).fetchone()
        c.execute(
            "INSERT INTO memory_rubric_overrides (account_key, rubric_text, updated_at, id, created_at) "
            "VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT (account_key) "
            "DO UPDATE SET rubric_text = excluded.rubric_text, updated_at = excluded.updated_at, "
            "version = memory_rubric_overrides.version + 1",
            (account_key, text, now, ids.make_id("rub"), at),
        )
        row = c.execute(
            "SELECT id, version FROM memory_rubric_overrides WHERE account_key = ?", (account_key,)
        ).fetchone()
        ledger.record_change(
            c,
            noun="memory_policies",
            id=row["id"],
            kind="changed" if existed else "created",
            state=None,
            version=row["version"],
            at=at,
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
        "SELECT account_key, entry_key, content, updated_at, id, state FROM memory_entries WHERE account_key LIKE ?",
        (_SCHEDULE_PREFIX + "%",),
    ).fetchall()
    if not rows:
        return 0
    at = time.time()
    with write_txn(conn) as c:
        for row in rows:
            taken = c.execute(
                "SELECT 1 FROM memory_entries WHERE account_key = ? AND entry_key = ?",
                (owner, row["entry_key"]),
            ).fetchone()
            entry_key = f"{row['entry_key']}--{row['account_key']}" if taken else row["entry_key"]
            # An id here, not from `fill_legacy_identity`: adoption runs on the
            # open path *after* the fill has already passed, so a row inserted
            # without one would keep a NULL id until some later open.
            c.execute(
                "INSERT INTO memory_entries (account_key, entry_key, content, updated_at, id, created_at, state) "
                "VALUES (?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT (account_key, entry_key) "
                "DO UPDATE SET content = excluded.content, updated_at = excluded.updated_at, "
                "state = excluded.state",
                (owner, entry_key, row["content"], row["updated_at"], ids.make_id("mem"), at, row["state"]),
            )
            c.execute(
                "DELETE FROM memory_entries WHERE account_key = ? AND entry_key = ?",
                (row["account_key"], row["entry_key"]),
            )
            # An entry moved account: to anything holding a copy that is one row
            # appearing and another going, and both have to be said. Missed on
            # the first pass of H16 and caught by a review — which is the cost
            # of recording a change at each write site rather than under them,
            # written down here so the next reader sees the failure mode rather
            # than only the fix.
            _record(c, owner, entry_key, kind="created", at=at)
            ledger.record_change(
                c, noun="memory_entries", id=row["id"], kind="deleted", state=None, version=None, at=at
            )
    return len(rows)
