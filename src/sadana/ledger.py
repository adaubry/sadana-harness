"""Everything that changed, in order, so an observer can catch up.

H16 (`docs/tasks/H16-store-identity-ledger-locks/spec.md` requirements 12-19),
serving P3 of `docs/reference/console_fit_plan.md` §4 and the console's own
register entries B3 (a mirror drifts and nobody notices) and B10 (a delete is
invisible to an observer).

Two reads, for the two situations a mirror is ever in. `changes_since()` after
a short gap: give me everything after the cursor I last saw. `inventory()`
after a long one, or a lost cursor: give me what exists right now with each
row's `updated_at` and `version`, and I will compare and repair. Neither is a
substitute for the other, which is why both are here.

**Imports no store module** (spec.md requirement 19). Stores import this; if
it imported them back, `plugins.py`'s own acyclic-leaf rule would be the next
thing to go. The price is `_NOUNS` below — a table of SQL naming columns those
modules own, with nothing but a test keeping the two true. That test
(`test_ledger.py`, every noun's SQL executed against a fresh store) is the
whole mitigation, and spec.md § Concerns says so out loud.

Nothing in the reference corpus is this. `gateway/delivery_ledger.py` is a
per-obligation delivery state machine and `gateway/lifecycle_ledger.py` is
crash forensics over a JSON sentinel; neither is an ordered change feed with a
cursor, and a search for `AUTOINCREMENT` across their core finds schema files
and tests. What was taken from `delivery_ledger.py:47` is its module-level
write lock, which lives in `conversation_store.write_txn` rather than here.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from sadana import ids

_SCHEMA = """
CREATE TABLE IF NOT EXISTS changes (
    cursor   INTEGER PRIMARY KEY AUTOINCREMENT,
    noun     TEXT    NOT NULL,
    id       TEXT    NOT NULL,
    kind     TEXT    NOT NULL,
    state    TEXT,
    version  INTEGER,
    at       REAL    NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_changes_noun_id ON changes (noun, id);
"""

#: The three things that can happen to a row. Closed: an observer branches on
#: this word, and a fourth value would reach it as an unhandled case rather
#: than as a new feature.
KINDS = frozenset({"created", "changed", "deleted"})


@dataclass(frozen=True)
class _Noun:
    """One plural noun: the id prefix its rows carry, and the queries that
    enumerate them.

    ``sources`` is a tuple because three nouns are backed by more than one
    table — `agents` by the characters index and the per-account selections,
    `plugins` by the registry, the installed state and the marketplace
    releases, `runs` by turn runs and plugin runs. Every id carries its own
    prefix and is unique across the store, so a union needs no disambiguation.
    spec.md § Open questions records that a console may want these split; the
    property that matters here is that every id the ledger mentions resolves
    against the inventory, and a union is what keeps that true.

    An empty ``sources`` is a noun registered now and written later —
    `schedules` by H27, `approvals` by H18, `operations` and `harness` by H19,
    `traces` by H20. It inventories as nothing, which is the honest answer.

    Every query filters ``id IS NOT NULL``. After each store's own legacy fill
    there are no such rows; the guard is for a connection some path opened
    without ever ensuring a schema, where emitting a null id would be worse
    than omitting the row — the next fill repairs it.
    """

    prefix: str
    sources: tuple[str, ...]


_NOUNS: dict[str, _Noun] = {
    "conversations": _Noun(
        "conv",
        ("SELECT id, updated_at, version FROM conversations WHERE id IS NOT NULL",),
    ),
    "messages": _Noun(
        "msg",
        # No `updated_at` and no `version`: a message row is never edited once
        # written (`conversation_store._insert_messages`' own invariant, which
        # is why it is `INSERT OR IGNORE` and never `REPLACE`). Its creation
        # time *is* its last-changed time, and its version is always 1.
        ("SELECT id, created_at AS updated_at, 1 AS version FROM messages WHERE id IS NOT NULL",),
    ),
    "memory_entries": _Noun(
        "mem",
        ("SELECT id, updated_at, version FROM memory_entries WHERE id IS NOT NULL",),
    ),
    "memory_policies": _Noun(
        "rub",
        ("SELECT id, updated_at, version FROM memory_rubric_overrides WHERE id IS NOT NULL",),
    ),
    "agents": _Noun(
        "agt",
        (
            "SELECT id, updated_at, version FROM agents",
            "SELECT id, updated_at, version FROM persona_selections WHERE id IS NOT NULL",
        ),
    ),
    "plugins": _Noun(
        "plg",
        (
            "SELECT id, updated_at, version FROM plugin_state",
            "SELECT id, updated_at, version FROM plugin_registry WHERE id IS NOT NULL",
            "SELECT id, updated_at, version FROM marketplace_releases WHERE id IS NOT NULL",
        ),
    ),
    "artifacts": _Noun("art", ("SELECT id, updated_at, version FROM artifacts",)),
    "runs": _Noun(
        "run",
        # `recorded_at` as the last-changed time: both tables are written once,
        # post hoc, and H21 is what makes `started_at`/`ended_at` live.
        (
            "SELECT id, recorded_at AS updated_at, version FROM turn_runs WHERE id IS NOT NULL",
            "SELECT id, recorded_at AS updated_at, version FROM plugin_runs WHERE id IS NOT NULL",
        ),
    ),
    "traces": _Noun("trc", ()),
    "schedules": _Noun("sch", ()),
    "approvals": _Noun("appr", ()),
    "operations": _Noun("op", ()),
    "harness": _Noun("hrn", ()),
}

#: The closed list, in the order the console's own plan names them.
NOUNS: tuple[str, ...] = tuple(_NOUNS)

# At import, not at the first write: a noun whose prefix was never registered
# is a typo, and a typo that waits for a write site to run is one that ships.
# This is the import-time check spec.md requirement 3 asks for — `ids.make_id`
# itself can only fail when somebody calls it.
_unregistered = sorted(n for n, spec in _NOUNS.items() if spec.prefix not in ids.PREFIXES)
if _unregistered:
    raise ValueError(f"ledger nouns with no registered id prefix: {_unregistered}")


@dataclass(frozen=True)
class Change:
    """One row of `changes`. `state` and `version` are nullable because a
    `deleted` has neither to report, and `messages` carries no version."""

    cursor: int
    noun: str
    id: str
    kind: str
    state: str | None
    version: int | None
    at: float


@dataclass(frozen=True)
class InventoryRow:
    """What a mirror compares against its own copy: is this row newer than
    mine, and by how many edits."""

    id: str
    updated_at: float
    version: int


def ensure_schema(conn: sqlite3.Connection) -> None:
    """Idempotent, the same "create tables on open" posture every other store
    module takes. Called from `conversation_store.open_store()`, which is the
    one door every writing connection comes through.

    `AUTOINCREMENT`, not a bare `INTEGER PRIMARY KEY`: SQLite reuses the
    largest rowid after a delete, so a plain rowid can go backwards, and a
    cursor that goes backwards silently re-serves changes a mirror already
    applied — or worse, skips past ones it has not. Nothing deletes from this
    table today; the guarantee is what a mirror is allowed to depend on, and
    it costs one `sqlite_sequence` row.
    """
    conn.executescript(_SCHEMA)


def record_change(
    c: sqlite3.Connection,
    *,
    noun: str,
    id: str,
    kind: str,
    state: str | None,
    version: int | None,
    at: float,
) -> None:
    """Write one change row, on a connection **already inside a
    `write_txn`**.

    That is the whole design and the reason this takes a connection rather
    than opening its own transaction: the change row and the change it
    describes commit or roll back together, so there is no interleaving in
    which one exists without the other. A caller that opens its own
    transaction here would produce exactly that gap.

    An unknown `noun` or `kind` raises rather than writing a row nothing can
    interpret — both reach this function as literals, so a wrong one is a bug
    to fix, never data to tolerate.

    So does an `id` whose prefix does not match the noun's. That check is what
    makes `_Noun.prefix` load-bearing rather than decorative, and it is the
    only thing standing between a mirror and a change row pointing at an id
    that will never appear under that noun in `inventory()` — a conversation's
    id recorded under `messages`, say, from a copy-pasted call site. It holds
    for the three nouns backed by several tables too: every table behind
    `plugins` mints `plg`, behind `agents` `agt`, behind `runs` `run`.
    """
    if noun not in _NOUNS:
        raise ValueError(f"{noun!r} is not a ledger noun; the closed list is ledger.NOUNS")
    if kind not in KINDS:
        raise ValueError(f"{kind!r} is not a change kind; the closed list is ledger.KINDS")
    if ids.parse_id(_NOUNS[noun].prefix, id) is None:
        raise ValueError(f"{id!r} is not an id of noun {noun!r} (expected prefix {_NOUNS[noun].prefix!r})")
    c.execute(
        "INSERT INTO changes (noun, id, kind, state, version, at) VALUES (?, ?, ?, ?, ?, ?)",
        (noun, id, kind, state, version, at),
    )


def changes_since(conn: sqlite3.Connection, cursor: int, limit: int) -> tuple[Change, ...]:
    """Every change after `cursor`, oldest first, at most `limit` of them.

    Strictly after, so a caller passes back the `cursor` of the last change it
    applied and gets the next ones — never that one again. `cursor=0` is the
    beginning, which is why `ledger_head()` returns 0 for an empty ledger
    rather than `None`.

    A full page means there may be more: the caller pages by passing the last
    returned cursor back. This never blocks and never waits for new changes;
    a caller that wants to follow polls, and H21 is what makes that a stream.
    """
    rows = conn.execute(
        "SELECT cursor, noun, id, kind, state, version, at FROM changes WHERE cursor > ? ORDER BY cursor LIMIT ?",
        (cursor, limit),
    ).fetchall()
    return tuple(
        Change(
            cursor=r["cursor"],
            noun=r["noun"],
            id=r["id"],
            kind=r["kind"],
            state=r["state"],
            version=r["version"],
            at=r["at"],
        )
        for r in rows
    )


def ledger_head(conn: sqlite3.Connection) -> int:
    """The newest cursor, or 0 when nothing has been recorded. What a mirror
    reads *before* taking an inventory, so that the changes it then misses
    while reading are ones it will be handed on its next `changes_since`."""
    row = conn.execute("SELECT COALESCE(MAX(cursor), 0) AS head FROM changes").fetchone()
    return int(row["head"])


def inventory(conn: sqlite3.Connection) -> dict[str, tuple[InventoryRow, ...]]:
    """Everything that exists right now, keyed by plural noun.

    What a mirror reads when it has been away too long for `changes_since()`
    to be cheap, or has lost its cursor entirely: compare `version` per `id`,
    fetch what moved, forget what is no longer listed. Every noun in `NOUNS`
    appears as a key, including the five nothing writes yet, which map to an
    empty tuple — a caller iterating the result then needs no separate list of
    which nouns are real.
    """
    return {
        noun: tuple(
            InventoryRow(id=r["id"], updated_at=r["updated_at"], version=r["version"])
            for sql in spec.sources
            for r in conn.execute(sql).fetchall()
        )
        for noun, spec in _NOUNS.items()
    }
