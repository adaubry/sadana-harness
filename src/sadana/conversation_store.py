"""A conversation survives the process that was running it.

CONV-08 of the CONVERSATION block (`docs/reference/conversation_block_blueprint.md`
section 7); its full contract is `docs/tasks/CONV-08-sqlite-transcript-store/spec.md`.

A new module, not another section of `conversation.py`: every function in
that module is pure (values in, values out); this one is the first in the
block to touch real I/O (`sqlite3`, a path on disk) — see CLAUDE.md's rule
that a module touching real I/O is its own file, regardless of line count.

Two tables, not one blob: `conversations` (one row per ``Conversation``)
and `messages` (one row per ``Message``, keyed by ``(conversation_key,
msg_seq)``) — the literal ``MessageKey`` constraint blueprint section 4.1
names, "checkable by scanning a range" at the database level. See
spec.md's Schema section for the full reasoning.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import sqlite3
import threading
import time
from collections.abc import Awaitable, Callable, Iterator
from dataclasses import asdict, dataclass, replace
from pathlib import Path

from sadana import config, context, ids, ledger, plugins
from sadana.conversation import (
    Conversation,
    ConversationKey,
    IterationBudget,
    Message,
    WallClockBudget,
    wall_clock_remaining,
)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS conversations (
    key                            TEXT PRIMARY KEY,
    template_name                  TEXT NOT NULL,
    system_prompt                  TEXT NOT NULL,
    prompt_sha256                  TEXT NOT NULL,
    prompt_epoch                   INTEGER NOT NULL,
    tool_surface_json              TEXT NOT NULL,
    next_turn_seq                  INTEGER NOT NULL,
    iteration_max_total            INTEGER NOT NULL,
    iteration_used                 INTEGER NOT NULL,
    wall_clock_remaining_s         REAL,
    next_child_seq                 INTEGER NOT NULL,
    stable_prompt_len              INTEGER,
    context_total_prompt_tokens    INTEGER NOT NULL DEFAULT 0,
    context_total_completion_tokens INTEGER NOT NULL DEFAULT 0,
    -- H16. `key` stays the immutable natural key; `id` is what a ledger entry
    -- and a foreign key point at; `name` a person reads is `key` for now and
    -- becomes its own column when a rename verb exists (H19). Nullable on
    -- purpose: a row written before H16 gets one from `fill_legacy_identity`
    -- at the next open, not from a backfill migration.
    id                             TEXT,
    created_at                     REAL,
    updated_at                     REAL,
    version                        INTEGER NOT NULL DEFAULT 1,
    state                          TEXT NOT NULL DEFAULT 'active',
    tags_json                      TEXT NOT NULL DEFAULT '{}',
    agent                          TEXT
);

CREATE TABLE IF NOT EXISTS messages (
    conversation_key  TEXT NOT NULL REFERENCES conversations(key),
    msg_seq           INTEGER NOT NULL,
    role              TEXT NOT NULL,
    content           TEXT,
    tool_calls_json   TEXT NOT NULL,
    tool_call_id      TEXT,
    -- H16. No `updated_at` and no `version`: a message row is never edited
    -- once written (`_insert_messages`' own invariant), so its creation time
    -- is also its last-changed time. `run_id` is written by H20, which is
    -- what first has a run id to put in it.
    id                TEXT,
    created_at        REAL,
    state             TEXT NOT NULL DEFAULT 'sent',
    run_id            TEXT,
    PRIMARY KEY (conversation_key, msg_seq)
);

-- GATEWAY-DAEMON-02 (docs/tasks/GATEWAY-DAEMON-02-scheduled-and-resumable-triggers
-- /spec.md § Design #5). New tables, not new columns on an existing one —
-- no `_migrate_columns()`-style guard needed.

CREATE TABLE IF NOT EXISTS scheduled_triggers (
    name              TEXT PRIMARY KEY,
    trigger_text      TEXT NOT NULL,
    next_run_at       REAL NOT NULL,
    interval_seconds  REAL
);

CREATE TABLE IF NOT EXISTS plugin_pauses (
    conversation_key  TEXT PRIMARY KEY REFERENCES conversations(key),
    plugin            TEXT NOT NULL,
    entry             TEXT NOT NULL,
    node              TEXT NOT NULL,
    trace_json        TEXT NOT NULL,
    artifacts_json    TEXT NOT NULL,
    turn_seq          INTEGER,
    seq_in_turn       INTEGER
);

-- PERSONA-02 (docs/tasks/PERSONA-02-who-the-agent-is-talking-to/spec.md
-- requirement 6). Whose conversation this is — a name, never a live
-- reference, and a table rather than a column on `conversations`: the value
-- would otherwise have to become a `Conversation` field to survive `save()`'s
-- upsert, and a child conversation has no answer to give for it. Nothing
-- reads this during a turn; it exists so the accounts listing can answer.
-- A row written before this item simply has no row here, and lists as owned
-- by nobody.

CREATE TABLE IF NOT EXISTS conversation_accounts (
    conversation_key  TEXT PRIMARY KEY REFERENCES conversations(key),
    account_key       TEXT NOT NULL
);
"""

#: Indexes on columns H16 added, run **after** ``migrate_columns`` and never
#: from ``_SCHEMA``.
#:
#: ``_SCHEMA`` is executed first and its ``CREATE TABLE IF NOT EXISTS`` is a
#: no-op on a store that already exists — so on a pre-H16 store the `id` column
#: does not exist yet at that point, and an index over it fails with *no such
#: column*. A self-check caught exactly that. The ordering is the contract:
#: columns, then indexes over them.
#:
#: NULLs are distinct to SQLite's UNIQUE, so a unique index is safe on rows
#: ``fill_legacy_identity`` has not reached yet.
_INDEXES = """
CREATE UNIQUE INDEX IF NOT EXISTS idx_conversations_id ON conversations (id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_messages_id ON messages (id);
"""

#: Every word `conversations.state` may hold, and every word
#: `messages.state` may hold. Closed sets, one per table, next to the table —
#: H16's P1 acceptance criterion ("a state from a written closed set"). A
#: console branches on these words, so a fourteenth reaches it as an unhandled
#: case rather than as a feature. Nothing can move a conversation off `active`
#: or a message off `sent` yet: H18 owns waiting, H19 owns closing.
CONVERSATION_STATES = frozenset({"active"})
MESSAGE_STATES = frozenset({"sent"})

_CONVERSATION_COLUMNS = (
    "key",
    "template_name",
    "system_prompt",
    "prompt_sha256",
    "prompt_epoch",
    "tool_surface_json",
    "next_turn_seq",
    "iteration_max_total",
    "iteration_used",
    "wall_clock_remaining_s",
    "next_child_seq",
    "stable_prompt_len",
    "context_total_prompt_tokens",
    "context_total_completion_tokens",
)
_CONVERSATION_COLUMNS_SQL = ", ".join(_CONVERSATION_COLUMNS)
_CONVERSATION_PLACEHOLDERS_SQL = ", ".join("?" * len(_CONVERSATION_COLUMNS))


class ConversationAlreadyExists(Exception):
    """``create()`` called with a ``key`` that already has a saved row."""


class ConversationNotFound(Exception):
    """``load()`` called with a ``key`` nobody has ever saved."""


def store_path_from_config() -> Path:
    """``SADANA_CONVERSATION_STORE_PATH``, default
    ``config.get_paths().state_dir / "conversations.sqlite3"``."""
    return config.env_path(
        "SADANA_CONVERSATION_STORE_PATH",
        default=config.get_paths().state_dir / "conversations.sqlite3",
    )


def open_store(path: Path) -> sqlite3.Connection:
    """Opens ``path`` (creating its parent directory and both tables if
    needed), sets ``journal_mode=WAL`` — adopted on its own merits
    (crash-safety, non-blocking reads), not hermes's NFS/version-fallback
    matrix: WSL-only means the filesystem and SQLite version are fixed.
    ``isolation_level=None`` puts the connection in autocommit mode so
    ``write_txn``'s own explicit ``BEGIN IMMEDIATE`` is never nested
    inside one Python's ``sqlite3`` module would otherwise open for us.
    ``check_same_thread=False``: ``bind_persist()``'s callback runs each
    call via ``asyncio.to_thread`` (a thread-pool worker, not necessarily
    the same one twice), so the one connection this function returns must
    be usable from more than the thread that opened it. Until H16 that was
    also all it survived: one thread at a time, different call to call. It now
    survives genuine concurrency, by construction rather than by nobody trying.

    This is the process's one **writer**. H16 made that structural rather than
    circumstantial: ``write_txn`` serializes every transaction on it, and a
    caller that only reads takes a thread-local connection from
    ``stores.Connections.reader()`` instead.

    What this survives: two turns on two conversations in two threads, and a
    reader listing conversations while a turn runs. What it does not: two turns
    on one conversation on one event loop; a call node at its approval gate
    (H18 removes this).

    ``busy_timeout`` is 5 seconds so a reader arriving mid-checkpoint waits
    rather than failing. The write lock already excludes writer-on-writer
    contention, so this covers only SQLite's own brief internal exclusions."""
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, isolation_level=None, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.executescript(_SCHEMA)
    migrate_columns(conn, _MIGRATED_COLUMNS)
    conn.executescript(_INDEXES)
    # The one place the ledger's own table is created: every connection that
    # will ever write comes through here, and `record_change` needs it present
    # before the first write, not before the first read.
    ledger.ensure_schema(conn)
    fill_legacy_identity(conn, "conversations", "conv", time_columns=("created_at", "updated_at"))
    fill_legacy_identity(conn, "messages", "msg", time_columns=("created_at",))
    return conn


def fill_legacy_identity(conn: sqlite3.Connection, table: str, prefix: str, *, time_columns: tuple[str, ...]) -> None:
    """Give every row of ``table`` written before H16 an ``id`` and a timestamp,
    once, idempotently.

    **The time it writes is a floor, not a fact.** The row is older — often far
    older — than the instant recorded here, which is simply the moment somebody
    first opened the store after this work item landed. Anywhere that timestamp
    surfaces it means "no later than", never "at". One ``time.time()`` read
    covers the whole pass, so rows filled together share it and a person reading
    them can tell they were filled rather than created.

    Not a backfill migration (CLAUDE.md, and `console_fit_plan.md` §5(b)): the
    guarded ``ALTER TABLE`` in ``migrate_columns`` adds the column, and this
    fills it in place at the next open. ``WHERE id IS NULL`` is what makes it
    idempotent, and what makes it free on a fresh store — the common case
    updates zero rows. Precedent: ``client_surface._adopt_scheduled_memories``,
    which runs on the open path for the same reason, because a step somebody
    has to remember to run is a step that does not get run.

    **Writes no ledger rows.** A mirror meeting this box for the first time
    reads the whole inventory anyway, so thousands of change rows about rows
    that did not really change would be noise standing in for news.

    Every call site passes literals for ``table``, ``prefix`` and
    ``time_columns``; none is reachable from outside this package, which is
    what makes the identifier interpolation safe.
    """
    rowids = [r[0] for r in conn.execute(f"SELECT rowid FROM {table} WHERE id IS NULL")]
    if not rowids:
        return
    now = time.time()
    assignments = ", ".join(["id = ?", *(f"{col} = ?" for col in time_columns)])
    logger.info("filling identity for %d pre-H16 rows in %s; their timestamps are a floor", len(rowids), table)
    with write_txn(conn) as c:
        c.executemany(
            f"UPDATE {table} SET {assignments} WHERE rowid = ?",
            [(ids.make_id(prefix), *([now] * len(time_columns)), rowid) for rowid in rowids],
        )


# Columns added after the original schema shipped — ``CREATE TABLE IF NOT
# EXISTS`` above only covers a brand-new store; a store that already exists
# needs each one added explicitly. C12 (docs/tasks/C12-context-resume-round-trip)
# added the three below.
# Columns added to a table that already shipped, by table. Applied with the
# idempotent ``PRAGMA table_info`` + guarded ``ALTER TABLE ... ADD COLUMN``
# shape CLAUDE.md prescribes (adapted from hermes-agent's own
# ``gateway/delivery_ledger.py:130-141``, kind: production-code), never a
# backfill migration.
#
# ``plugin_pauses``' two are nullable on purpose. A pause row written before
# ``ARTIFACT-STORE-01`` has no idea which turn it belonged to and there is no
# safe value to invent, so it reads back ``None`` and the run resumed from it
# gets no output directory — exactly how it behaved before these existed
# (CLAUDE.md: "a column with no safe default falls back to matching pre-fix
# behavior at read time, so legacy rows keep loading and self-correct only
# once genuinely rewritten").
#
# H16's own additions are the seven on ``conversations`` and the four on
# ``messages``. ``id``, ``created_at`` and ``updated_at`` are nullable with no
# default: there is no safe value to invent for a row older than the column, so
# they read back ``NULL`` until ``fill_legacy_identity`` reaches them, which is
# CLAUDE.md's own "falls back to matching pre-fix behavior at read time" applied
# to identity. ``version``/``state``/``tags_json`` carry defaults that *are*
# safe — a row nobody has edited is at version 1 and active.
_MIGRATED_COLUMNS: dict[str, tuple[tuple[str, str], ...]] = {
    "conversations": (
        ("stable_prompt_len", "INTEGER"),
        ("context_total_prompt_tokens", "INTEGER NOT NULL DEFAULT 0"),
        ("context_total_completion_tokens", "INTEGER NOT NULL DEFAULT 0"),
        ("id", "TEXT"),
        ("created_at", "REAL"),
        ("updated_at", "REAL"),
        ("version", "INTEGER NOT NULL DEFAULT 1"),
        ("state", "TEXT NOT NULL DEFAULT 'active'"),
        ("tags_json", "TEXT NOT NULL DEFAULT '{}'"),
        ("agent", "TEXT"),
        # H20 (docs/tasks/H20-door-nouns-turn-side/spec.md). Nullable, no
        # default: there is no safe name to invent for a row older than this
        # column, so it reads back `NULL` and `door/nouns/conversations.py`
        # renders `name or key` at read time — CLAUDE.md's own rule for a
        # column with no safe default.
        ("name", "TEXT"),
    ),
    "messages": (
        ("id", "TEXT"),
        ("created_at", "REAL"),
        ("state", "TEXT NOT NULL DEFAULT 'sent'"),
        ("run_id", "TEXT"),
    ),
    "plugin_pauses": (
        ("turn_seq", "INTEGER"),
        ("seq_in_turn", "INTEGER"),
    ),
}

#: Serializes every write transaction in this process against every other.
#:
#: H16. One writer connection per process means one lock per process — the
#: same fact stated once, not a registry of one (CLAUDE.md's rule about a
#: dispatch seam earning its cost only when a second member exists). It lives
#: here rather than on ``Connections`` because ``write_txn`` is called from
#: roughly twenty sites holding a bare connection, and ``sqlite3.Connection``
#: is a C type that cannot carry an attribute.
#:
#: ``RLock``, not ``Lock``. Nothing nests ``write_txn`` today and nothing
#: should: SQLite answers a nested ``BEGIN IMMEDIATE`` with *cannot start a
#: transaction within a transaction*, which is a clear error with a stack
#: trace. Under a plain ``Lock`` the same mistake would deadlock the process
#: instead, and a hang is the worse of the two failures by a long way. The
#: reference reached the same conclusion for the same reason
#: (``plugins/memory/holographic/store.py:112``, one shared connection and one
#: re-entrant lock, after independent WAL writers raced).
_WRITE_LOCK = threading.RLock()

logger = logging.getLogger(__name__)


def migrate_columns(conn: sqlite3.Connection, columns: dict[str, tuple[tuple[str, str], ...]]) -> None:
    """Adds every column in ``columns`` missing from the table that owns it.

    Takes the map as a parameter rather than reading a module global, because
    H16 gave four other store modules the same need and the alternative was
    four copies of this function. Each of them keeps its own map beside its own
    ``_SCHEMA``, which is what CLAUDE.md's "the migration maps stay the single
    source of what was added later" asks for — one map per module, not one map
    for the database.

    The ``PRAGMA table_info`` check is what makes a second ``open_store()``
    call in this same process a no-op: a column already present — in a store
    this function already migrated, or one created fresh with it already in
    ``_SCHEMA`` — is simply skipped. No exception guard is needed for that;
    declined hermes's own concurrent-first-use ``sqlite3.OperationalError``
    catch, since this store's single-writer posture (spec.md's Non-goals)
    already excludes the race it exists to survive.

    One ``write_txn`` per table with anything missing, rather than one per
    column: a self-check caught that the realistic first-run case (a store
    predating all three ``conversations`` columns) would otherwise cost three
    separate autocommitted DDL statements — three WAL commits for one logical
    migration, where one does the job.

    ``table`` is never caller-supplied; every key comes from a map written as a
    literal in the module that owns the table. That is what makes the f-string
    interpolation safe, since SQLite takes no parameter in a DDL identifier
    position.
    """
    for table, table_columns in columns.items():
        existing = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
        missing = [(name, ddl_type) for name, ddl_type in table_columns if name not in existing]
        if not missing:
            continue
        with write_txn(conn) as c:
            for name, ddl_type in missing:
                c.execute(f"ALTER TABLE {table} ADD COLUMN {name} {ddl_type}")


@contextlib.contextmanager
def write_txn(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    """An IMMEDIATE write transaction, serialized by ``_WRITE_LOCK``: adapted
    from `hermes-agent/hermes_cli/sqlite_util.py:31-49` (audited: stdlib-only,
    ~19 lines, no hidden dependency on hermes's own state/config
    machinery — safe to adapt rather than reinvent). The explicit
    ``ROLLBACK`` is guarded so a SQLite auto-rollback (no active
    transaction left under lock contention) cannot shadow the original
    exception with a spurious rollback error.

    H16 added the lock, and *where* it is held is the whole point: inside a
    transaction, never around a model round trip. A write takes microseconds,
    so two turns on two conversations contend here for no measurable time —
    which is what lets them run at once at all (P8). The lock a turn holds for
    its whole length is a different one, per conversation,
    ``stores.conversation_lock()``."""
    with _WRITE_LOCK:
        conn.execute("BEGIN IMMEDIATE")
        try:
            yield conn
        except Exception:
            with contextlib.suppress(sqlite3.OperationalError):
                conn.execute("ROLLBACK")
            raise
        else:
            conn.execute("COMMIT")


def _conversation_row(conversation: Conversation, now: float) -> tuple:
    remaining = (
        wall_clock_remaining(conversation.wall_clock_budget, now)
        if conversation.wall_clock_budget is not None
        else None
    )
    return (
        conversation.key,
        conversation.template_name,
        conversation.system_prompt,
        conversation.prompt_sha256,
        conversation.prompt_epoch,
        json.dumps(list(conversation.tool_surface)),
        conversation.next_turn_seq,
        conversation.iteration_budget.max_total,
        conversation.iteration_budget.used,
        remaining,
        conversation.next_child_seq,
        conversation.stable_prompt_len,
        conversation.context_state.total_prompt_tokens,
        conversation.context_state.total_completion_tokens,
    )


#: The columns H16 added to ``conversations`` that ``save()`` must set when it
#: *inserts* and must leave alone when it *updates*. ``version`` and
#: ``updated_at`` are deliberately not here: they move on every write and are
#: handled by their own clause.
_IDENTITY_COLUMNS = ("id", "created_at", "state", "tags_json", "agent")


#: The insert both ``create()`` and ``save()`` issue. One constant rather than
#: two f-strings built at each call site, so the two statements cannot drift
#: apart — which is what it was before H16 added the identity columns, and the
#: identity columns did not change the argument.
_INSERT_CONVERSATION_SQL = (
    f"INSERT INTO conversations ({_CONVERSATION_COLUMNS_SQL}, {', '.join(_IDENTITY_COLUMNS)}, updated_at, version) "
    f"VALUES ({_CONVERSATION_PLACEHOLDERS_SQL}, {', '.join('?' * len(_IDENTITY_COLUMNS))}, ?, 1)"
)

#: ``save()``'s upsert. The identity columns appear on the **insert** branch
#: only and are absent from ``DO UPDATE``: that asymmetry is the whole of
#: "``key`` immutable, ``id`` unique" in one statement. Joined once at import
#: rather than on every call.
_UPSERT_CONVERSATION_SQL = (
    _INSERT_CONVERSATION_SQL
    + " ON CONFLICT(key) DO UPDATE SET "
    + ", ".join(f"{col}=excluded.{col}" for col in _CONVERSATION_COLUMNS if col != "key")
    + ", updated_at=excluded.updated_at, version=conversations.version + 1"
)


def _insert_messages(conn: sqlite3.Connection, conversation: Conversation, *, start_seq: int = 0) -> tuple[str, ...]:
    """One ``INSERT OR IGNORE`` per message from ``start_seq`` on. Never
    ``REPLACE``: a message row is never edited once written (``append()``'s
    own invariant — history only grows), so silently skipping an
    already-present ``msg_seq`` is the correct idempotent behaviour;
    ``REPLACE`` would instead accept silently rewriting a historical row.
    ``start_seq`` is an efficiency knob, not a correctness one — the
    default (0) re-inserts the whole history and ``OR IGNORE`` alone keeps
    that idempotent, which is what a caller with no memory of what it last
    flushed needs; ``bind_persist()`` passes its own running offset so a
    turn's repeated persist calls insert only the new tail each time,
    instead of re-attempting every row already durable.

    Returns the ids of the rows **actually inserted**, which is what the caller
    writes ledger rows for. H16 made the skip explicit rather than leaving it
    to ``OR IGNORE``: a minted id is only real if its row landed, and SQLite
    will not say which of an ``executemany``'s rows it ignored. One indexed
    ``SELECT`` of the sequence numbers already present answers that before the
    insert, and ``OR IGNORE`` stays as the backstop it always was."""
    present = {
        row[0]
        for row in conn.execute(
            "SELECT msg_seq FROM messages WHERE conversation_key = ? AND msg_seq >= ?",
            (conversation.key, start_seq),
        )
    }
    now_wall = time.time()
    rows = [
        (
            conversation.key,
            seq,
            m.role,
            m.content,
            json.dumps(list(m.tool_calls)),
            m.tool_call_id,
            ids.make_id("msg"),
            now_wall,
        )
        for seq, m in enumerate(conversation.messages[start_seq:], start=start_seq)
        if seq not in present
    ]
    conn.executemany(
        "INSERT OR IGNORE INTO messages "
        "(conversation_key, msg_seq, role, content, tool_calls_json, tool_call_id, id, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        rows,
    )
    return tuple(row[6] for row in rows)


def create(
    conn: sqlite3.Connection,
    conversation: Conversation,
    *,
    now: float,
    account_key: str,
    agent: str | None = None,
    id: str | None = None,
) -> None:
    """Inserts one ``conversations`` row, and one ``conversation_accounts``
    row naming whose it is. Raises ``ConversationAlreadyExists`` on a
    primary-key collision, leaving the prior row untouched.

    ``account_key`` is required rather than defaulted: a conversation always
    belongs to somebody, and the next client to be written should not be able
    to leave that unanswered by omission (PERSONA-02 requirement 6). Both rows
    go in the one transaction this function already opened, so a conversation
    can never exist without an owner recorded beside it.

    ``agent`` is the **name** of the character this conversation speaks in,
    resolved for ``account_key`` at the moment its voice was (H16 requirement
    6). ``None`` means the account had no selection, which is an answer and not
    a missing value. A name, never the rendered text and never a live
    reference: the file it names can change under us and this row must not
    silently mean something else. Passed in rather than looked up here, because
    ``persona_store`` imports this module and the reverse would be a cycle —
    ``client_surface._create`` already has both the account and the connection.

    ``id`` defaults to a freshly minted one (every existing caller). H20's
    door passes its own pre-minted value so a console-created conversation's
    ``id`` equals its ``key`` (spec.md's "Decisions already made" — a
    terminal-created conversation keeps its own auto-minted, key-independent
    ``id``, unaffected by this parameter existing).
    """
    now_wall = time.time()
    with write_txn(conn) as c:
        conversation_id = id if id is not None else ids.make_id("conv")
        try:
            c.execute(
                _INSERT_CONVERSATION_SQL,
                (*_conversation_row(conversation, now), conversation_id, now_wall, "active", "{}", agent, now_wall),
            )
        except sqlite3.IntegrityError as exc:
            raise ConversationAlreadyExists(conversation.key) from exc
        c.execute(
            "INSERT INTO conversation_accounts (conversation_key, account_key) VALUES (?, ?) "
            "ON CONFLICT (conversation_key) DO UPDATE SET account_key = excluded.account_key",
            (conversation.key, account_key),
        )
        message_ids = _insert_messages(c, conversation)
        ledger.record_change(
            c, noun="conversations", id=conversation_id, kind="created", state="active", version=1, at=now_wall
        )
        _record_messages(c, message_ids, at=now_wall)


def _record_messages(c: sqlite3.Connection, message_ids: tuple[str, ...], *, at: float) -> None:
    """One ``messages``/``created`` per row that genuinely landed. Version 1
    always: a message is never edited once written."""
    for message_id in message_ids:
        ledger.record_change(c, noun="messages", id=message_id, kind="created", state="sent", version=1, at=at)


def _record_conversation_change(c: sqlite3.Connection, key: ConversationKey, *, at: float) -> None:
    """Bump one conversation's ``updated_at``/``version`` and announce it.

    What every write that changes a conversation without inserting it calls.
    The bump and the read of its result are both inside the caller's
    transaction, which is what makes the announced ``version`` the one that
    actually committed rather than one this function guessed — there is no
    window in which another writer could have moved it in between.

    A key with no row, or a legacy row the fill has not reached, announces
    nothing: there is no id to name it by. ``RETURNING`` would fold the update
    and the read into one statement, and is left alone deliberately — it would
    be this module's only use of it, to save one indexed primary-key lookup.
    """
    c.execute("UPDATE conversations SET updated_at = ?, version = version + 1 WHERE key = ?", (at, key))
    row = c.execute("SELECT id, state, version FROM conversations WHERE key = ?", (key,)).fetchone()
    if row is None or row["id"] is None:
        return
    ledger.record_change(
        c, noun="conversations", id=row["id"], kind="changed", state=row["state"], version=row["version"], at=at
    )


def accounts_with_conversations(conn: sqlite3.Connection) -> frozenset[str]:
    """Every account that has started at least one conversation. Empty for a
    store written before PERSONA-02, whose rows have no account recorded —
    that is the honest answer, not a failure."""
    rows = conn.execute("SELECT DISTINCT account_key FROM conversation_accounts").fetchall()
    return frozenset(row["account_key"] for row in rows)


def save(conn: sqlite3.Connection, conversation: Conversation, *, now: float, start_seq: int = 0) -> None:
    """Upserts the ``conversations`` row — always succeeds for a key the
    caller already owns (already created or already loaded), never raises
    for that reason alone. ``start_seq``: see ``_insert_messages``.

    The identity columns are set on the **insert** branch and left alone on the
    update branch. That asymmetry is the whole of H16's "``key`` immutable,
    ``id`` unique, ``name`` mutable" in one statement: a save of a key that
    already exists must not mint a second id for it, and must not move its
    ``created_at``. ``_CONVERSATION_COLUMNS`` is deliberately still only the
    columns that carry a ``Conversation``'s own fields, so that ``load()``
    keeps reconstructing exactly the value ``conversation.py`` defines and that
    module needs no change.

    Saving a key that does not exist yet does insert one, with a fresh id and
    no agent — that is upsert behaviour this function already had, and the
    ledger row it writes says ``created`` rather than ``changed``, because that
    is what happened.
    """
    now_wall = time.time()
    with write_txn(conn) as c:
        c.execute(
            _UPSERT_CONVERSATION_SQL,
            (*_conversation_row(conversation, now), ids.make_id("conv"), now_wall, "active", "{}", None, now_wall),
        )
        message_ids = _insert_messages(c, conversation, start_seq=start_seq)
        row = c.execute("SELECT id, state, version FROM conversations WHERE key = ?", (conversation.key,)).fetchone()
        ledger.record_change(
            c,
            noun="conversations",
            id=row["id"],
            # `version` is 1 only on the branch that inserted: the upsert sets
            # it to 1 on insert and `version + 1` on conflict, so the row just
            # read answers which branch ran without a second probe before it.
            kind="created" if row["version"] == 1 else "changed",
            state=row["state"],
            version=row["version"],
            at=now_wall,
        )
        _record_messages(c, message_ids, at=now_wall)


def exists(conn: sqlite3.Connection, key: ConversationKey) -> bool:
    """Whether ``key`` names a saved conversation, without reading it.

    ``load()`` answers the same question but pays for the whole history —
    every message row, a ``json.loads`` per message, the tool surface and the
    budgets — which is waste for a caller that only wants to know whether a
    name is taken (``client_surface.open_conversation()``)."""
    return conn.execute("SELECT 1 FROM conversations WHERE key = ?", (key,)).fetchone() is not None


def load(conn: sqlite3.Connection, key: ConversationKey, *, now: float) -> Conversation:
    """Raises ``ConversationNotFound`` if ``key`` has no row. ``now`` turns
    the stored remaining-seconds figure back into a fresh
    ``WallClockBudget.deadline`` for the resuming process's own clock."""
    row = conn.execute(f"SELECT {_CONVERSATION_COLUMNS_SQL} FROM conversations WHERE key = ?", (key,)).fetchone()
    if row is None:
        raise ConversationNotFound(key)

    msg_rows = conn.execute(
        "SELECT role, content, tool_calls_json, tool_call_id FROM messages WHERE conversation_key = ? ORDER BY msg_seq",
        (key,),
    ).fetchall()
    messages = tuple(
        Message(
            role=r["role"],
            content=r["content"],
            tool_calls=tuple(json.loads(r["tool_calls_json"])),
            tool_call_id=r["tool_call_id"],
        )
        for r in msg_rows
    )

    wall_clock_budget = None
    if row["wall_clock_remaining_s"] is not None:
        wall_clock_budget = WallClockBudget(deadline=now + row["wall_clock_remaining_s"])

    return Conversation(
        key=row["key"],
        template_name=row["template_name"],
        system_prompt=row["system_prompt"],
        prompt_sha256=row["prompt_sha256"],
        prompt_epoch=row["prompt_epoch"],
        tool_surface=tuple(json.loads(row["tool_surface_json"])),
        messages=messages,
        next_turn_seq=row["next_turn_seq"],
        iteration_budget=IterationBudget(max_total=row["iteration_max_total"], used=row["iteration_used"]),
        wall_clock_budget=wall_clock_budget,
        # C12 (docs/tasks/C12-context-resume-round-trip): both of these are
        # now genuinely persisted, not reset. ``stable_prompt_len`` only
        # falls back to the full ``system_prompt`` length for a row written
        # before this fix (``_migrate_columns`` leaves it ``NULL`` on an
        # existing store rather than backfilling a guessed value) — the
        # same too-wide boundary that row already had. ``context_state``
        # needs no such fallback: ``_MIGRATED_COLUMNS``' ``DEFAULT 0``
        # already matches the old reset-to-zero value exactly.
        stable_prompt_len=(
            row["stable_prompt_len"] if row["stable_prompt_len"] is not None else len(row["system_prompt"])
        ),
        context_state=context.ContextState(
            total_prompt_tokens=row["context_total_prompt_tokens"],
            total_completion_tokens=row["context_total_completion_tokens"],
        ),
        next_child_seq=row["next_child_seq"],
    )


def _escape_like(query: str) -> str:
    """Backslash-escapes a literal ``\\``, ``%`` or ``_`` in ``query`` —
    backslash first, so escaping ``%``/``_`` afterward can't be
    double-escaped by that first step — so ``search_conversations``'s
    ``LIKE ... ESCAPE '\\'`` clause treats them as literal characters
    instead of SQL wildcards."""
    return query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def search_conversations(conn: sqlite3.Connection, query: str, *, now: float) -> list[Conversation]:
    """Every saved conversation whose ``key``, ``template_name``, or any
    message's ``content`` contains ``query`` as a case-insensitive
    substring (SQLite's ``LIKE`` is case-insensitive for ASCII by
    default), ordered by key. ``query == ""`` matches every row — this
    doubles as ``search_conversations``'s own "list everything" behaviour
    (CONV-08 spec.md requirement 7's deferred half), with no second
    function or row-mapping to keep in sync with this one. Each match is
    the same full ``Conversation`` ``load()`` returns for its key, not a
    lighter summary — never raises: an empty result is a normal search
    outcome, unlike ``load()``'s ``ConversationNotFound`` for one
    specifically-named key that isn't there."""
    pattern = f"%{_escape_like(query)}%"
    rows = conn.execute(
        "SELECT DISTINCT c.key FROM conversations c "
        "LEFT JOIN messages m ON m.conversation_key = c.key "
        "WHERE c.key LIKE ? ESCAPE '\\' "
        "OR c.template_name LIKE ? ESCAPE '\\' "
        "OR m.content LIKE ? ESCAPE '\\' "
        "ORDER BY c.key",
        (pattern, pattern, pattern),
    ).fetchall()
    return [load(conn, row["key"], now=now) for row in rows]


def bind_persist(
    conn: sqlite3.Connection, conversation: Conversation, *, now: float
) -> Callable[[tuple[Message, ...]], Awaitable[None]]:
    """Returns an async function matching ``run_turn``'s ``persist``
    parameter exactly (`conversation.py:708`). Each call re-saves
    ``conversation`` with ``messages`` replaced by the growing history the
    loop hands it, via ``asyncio.to_thread`` — same wrapping precedent as
    C6's ``complete()`` (`conversation.py:501`) over a sync client.

    Tracks how many messages it has already flushed so a turn's repeated
    calls (once per tool round) insert only the new tail each time,
    instead of re-attempting every already-durable row — a turn's total
    persistence work is then O(messages), not O(messages²). The captured
    ``conversation`` is stripped of its own (soon-stale) ``messages``
    first, so the closure doesn't hold a growing tuple it never reads."""
    template = replace(conversation, messages=())
    flushed = 0

    async def _persist(messages: tuple[Message, ...]) -> None:
        nonlocal flushed
        await asyncio.to_thread(save, conn, replace(template, messages=messages), now=now, start_seq=flushed)
        flushed = len(messages)

    return _persist


# ── GATEWAY-DAEMON-02: scheduled triggers and plugin pauses ────────────────
# Its full contract is `docs/tasks/GATEWAY-DAEMON-02-scheduled-and-resumable-triggers/spec.md`.


@dataclass(frozen=True)
class ScheduledTrigger:
    """One row of ``scheduled_triggers``. ``name`` is the natural key a
    plugin's own setup code, or a future CLI command, addresses this by —
    CLAUDE.md's names-not-pointers rule, backed here by a real ``PRIMARY
    KEY``. ``interval_seconds`` of ``None`` means fire once."""

    name: str
    trigger_text: str
    next_run_at: float
    interval_seconds: float | None


def upsert_scheduled_trigger(
    conn: sqlite3.Connection, *, name: str, trigger_text: str, next_run_at: float, interval_seconds: float | None
) -> None:
    """Insert-or-replace by ``name`` — registering an existing trigger again
    updates it in place rather than raising, the same posture ``save()``
    already takes toward an existing ``conversations`` row."""
    with write_txn(conn) as c:
        c.execute(
            "INSERT INTO scheduled_triggers (name, trigger_text, next_run_at, interval_seconds) "
            "VALUES (?, ?, ?, ?) "
            "ON CONFLICT(name) DO UPDATE SET "
            "trigger_text=excluded.trigger_text, next_run_at=excluded.next_run_at, "
            "interval_seconds=excluded.interval_seconds",
            (name, trigger_text, next_run_at, interval_seconds),
        )


def due_triggers(conn: sqlite3.Connection, *, now: float) -> tuple[ScheduledTrigger, ...]:
    """Every trigger whose ``next_run_at`` has arrived, ordered by name for
    a deterministic firing order within one tick."""
    rows = conn.execute(
        "SELECT name, trigger_text, next_run_at, interval_seconds FROM scheduled_triggers "
        "WHERE next_run_at <= ? ORDER BY name",
        (now,),
    ).fetchall()
    return tuple(
        ScheduledTrigger(
            name=r["name"],
            trigger_text=r["trigger_text"],
            next_run_at=r["next_run_at"],
            interval_seconds=r["interval_seconds"],
        )
        for r in rows
    )


def advance_scheduled_trigger(conn: sqlite3.Connection, *, name: str, next_run_at: float) -> None:
    """Moves a recurring trigger's own next firing forward. The caller
    (``scheduling.tick()``) always computes ``next_run_at`` as
    ``now + interval_seconds``, never ``old_next_run_at + interval_seconds``
    — that's what keeps a long gap (the daemon was down) from producing a
    backlog of already-past firings instead of exactly one."""
    with write_txn(conn) as c:
        c.execute("UPDATE scheduled_triggers SET next_run_at = ? WHERE name = ?", (next_run_at, name))


def delete_scheduled_trigger(conn: sqlite3.Connection, *, name: str) -> None:
    """Removes a one-shot trigger once it has fired. A name with no row is
    a silent no-op, matching ``DELETE``'s own natural idempotence."""
    with write_txn(conn) as c:
        c.execute("DELETE FROM scheduled_triggers WHERE name = ?", (name,))


@dataclass(frozen=True)
class Pause:
    """One row of ``plugin_pauses`` — everything ``plugin_dispatch.
    resume_paused_run()`` needs to continue a `wait`-paused
    ``run_graph()`` walk later. Never the plugin's directory or ``Manifest``
    object, only its name — re-resolved fresh at resume time (see
    ``plugins.ResumeState``'s own docstring)."""

    plugin: str
    entry: str
    node: str
    trace: tuple[plugins.NodeTrace, ...]
    artifacts: tuple[plugins.Artifact, ...]
    # Where the paused half wrote, so the resumed half writes there too.
    # Trailing and defaulted so every construction predating them still means
    # what it meant; `None` is a row written before they existed.
    turn_seq: int | None = None
    seq_in_turn: int | None = None


def save_pause(
    conn: sqlite3.Connection,
    *,
    conversation_key: str,
    plugin: str,
    entry: str,
    node: str,
    trace: tuple[plugins.NodeTrace, ...],
    artifacts: tuple[plugins.Artifact, ...],
    turn_seq: int | None = None,
    seq_in_turn: int | None = None,
) -> None:
    """Upserts by ``conversation_key`` — a run that pauses a second time
    (at a second `wait` node) overwrites its own prior pause row cleanly
    rather than leaving two, matching ``PRIMARY KEY``'s own "at most one
    outstanding pause per conversation" constraint."""
    with write_txn(conn) as c:
        c.execute(
            "INSERT INTO plugin_pauses (conversation_key, plugin, entry, node, trace_json, artifacts_json, "
            "turn_seq, seq_in_turn) VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(conversation_key) DO UPDATE SET "
            "plugin=excluded.plugin, entry=excluded.entry, node=excluded.node, "
            "trace_json=excluded.trace_json, artifacts_json=excluded.artifacts_json, "
            "turn_seq=excluded.turn_seq, seq_in_turn=excluded.seq_in_turn",
            (
                conversation_key,
                plugin,
                entry,
                node,
                json.dumps([asdict(t) for t in trace]),
                json.dumps([asdict(a) for a in artifacts]),
                turn_seq,
                seq_in_turn,
            ),
        )
        # The conversation itself moved: something is now waiting on it. H18
        # gives that a state word and an addressable approval; until then the
        # honest record is that the row changed, not that it entered a state
        # this codebase cannot yet leave.
        _record_conversation_change(c, conversation_key, at=time.time())


def save_pause_from_result(
    conn: sqlite3.Connection,
    *,
    conversation_key: str,
    result: plugins.DagResult,
    turn_seq: int | None = None,
    seq_in_turn: int | None = None,
) -> None:
    """`save_pause()`, unpacking a `paused_node`-bearing `DagResult` — the
    one place that knows how a paused run's fields map onto a
    `plugin_pauses` row, called from both of this project's two paths that
    can produce one (`plugin_dispatch.build_dispatch()`'s own `dispatch`
    closure, for a run's first pause; `plugin_dispatch.resume_paused_run()`,
    for a second). `result.paused_node is None` is the caller's own error —
    this function trusts it was already checked, the same posture
    `save_pause()` itself takes toward its own arguments."""
    assert result.paused_node is not None, "save_pause_from_result called with a non-paused DagResult"
    save_pause(
        conn,
        conversation_key=conversation_key,
        plugin=result.plugin,
        entry=result.entry,
        node=result.paused_node,
        trace=result.trace,
        artifacts=result.artifacts,
        turn_seq=turn_seq,
        seq_in_turn=seq_in_turn,
    )


def load_pause(conn: sqlite3.Connection, *, conversation_key: str) -> Pause | None:
    """``None`` when the conversation has no outstanding pause — the normal
    case every inbound message not resuming something checks first."""
    row = conn.execute(
        "SELECT plugin, entry, node, trace_json, artifacts_json, turn_seq, seq_in_turn "
        "FROM plugin_pauses WHERE conversation_key = ?",
        (conversation_key,),
    ).fetchone()
    if row is None:
        return None
    return Pause(
        plugin=row["plugin"],
        entry=row["entry"],
        node=row["node"],
        trace=tuple(plugins.NodeTrace(**t) for t in json.loads(row["trace_json"])),
        artifacts=tuple(plugins.Artifact(**a) for a in json.loads(row["artifacts_json"])),
        turn_seq=row["turn_seq"],
        seq_in_turn=row["seq_in_turn"],
    )


def delete_pause(conn: sqlite3.Connection, *, conversation_key: str) -> None:
    """A key with no row is a silent no-op, same as ``delete_scheduled_trigger``.

    The ledger row is written against the *conversation*, not the pause: a
    pause is not one of `ledger.NOUNS` and has no id of its own, and what an
    observer needs to learn is that the conversation stopped waiting. A key
    with no pause still bumps the conversation, which is the cost of keeping
    this a no-op rather than a lookup — it is called once per resume, always
    for a pause that existed."""
    with write_txn(conn) as c:
        c.execute("DELETE FROM plugin_pauses WHERE conversation_key = ?", (conversation_key,))
        _record_conversation_change(c, conversation_key, at=time.time())
