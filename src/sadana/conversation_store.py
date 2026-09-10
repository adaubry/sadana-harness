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
import sqlite3
from collections.abc import Awaitable, Callable, Iterator
from dataclasses import replace
from pathlib import Path

from sadana import config, context
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
    context_total_completion_tokens INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS messages (
    conversation_key  TEXT NOT NULL REFERENCES conversations(key),
    msg_seq           INTEGER NOT NULL,
    role              TEXT NOT NULL,
    content           TEXT,
    tool_calls_json   TEXT NOT NULL,
    tool_call_id      TEXT,
    PRIMARY KEY (conversation_key, msg_seq)
);
"""

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
_INSERT_CONVERSATION_SQL = (
    f"INSERT INTO conversations ({_CONVERSATION_COLUMNS_SQL}) VALUES ({_CONVERSATION_PLACEHOLDERS_SQL})"
)


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
    be usable from more than the thread that opened it — sadana-harness's
    own single-process, single-caller posture (spec.md's Non-goals) means
    this is still never touched by two threads *at once*, only by one
    thread at a time, different call to call."""
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, isolation_level=None, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(_SCHEMA)
    _migrate_columns(conn)
    return conn


# Columns added after the original schema shipped — ``CREATE TABLE IF NOT
# EXISTS`` above only covers a brand-new store; a store that already exists
# needs each one added explicitly. C12 (docs/tasks/C12-context-resume-round-trip)
# added the three below.
_MIGRATED_COLUMNS = (
    ("stable_prompt_len", "INTEGER"),
    ("context_total_prompt_tokens", "INTEGER NOT NULL DEFAULT 0"),
    ("context_total_completion_tokens", "INTEGER NOT NULL DEFAULT 0"),
)


def _migrate_columns(conn: sqlite3.Connection) -> None:
    """Adds any column in ``_MIGRATED_COLUMNS`` missing from an existing
    ``conversations`` table — the ``PRAGMA table_info`` check below (adapted
    from hermes-agent's own guarded ``ALTER TABLE ... ADD COLUMN`` pattern,
    ``gateway/delivery_ledger.py:130-141``, kind: production-code) is what
    makes a second ``open_store()`` call in this same process a no-op:
    a column already present (a store this function already migrated, or
    one created fresh with it already in ``_SCHEMA``) is simply skipped.
    No exception guard is needed for that — declined hermes's own
    concurrent-first-use ``sqlite3.OperationalError`` catch, since this
    store's single-writer posture (spec.md's Non-goals) already excludes
    the race it exists to survive. Runs any missing columns' ``ALTER
    TABLE``s inside one ``write_txn`` — self-check caught that the
    realistic first-run case (a store predating all three columns) would
    otherwise cost three separate autocommitted DDL statements, three WAL
    commits for one logical migration, where one does the job."""
    existing = {row[1] for row in conn.execute("PRAGMA table_info(conversations)")}
    missing = [(name, ddl_type) for name, ddl_type in _MIGRATED_COLUMNS if name not in existing]
    if not missing:
        return
    with write_txn(conn) as c:
        for name, ddl_type in missing:
            c.execute(f"ALTER TABLE conversations ADD COLUMN {name} {ddl_type}")


@contextlib.contextmanager
def write_txn(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    """An IMMEDIATE write transaction: adapted from
    `hermes-agent/hermes_cli/sqlite_util.py:31-49` (audited: stdlib-only,
    ~19 lines, no hidden dependency on hermes's own state/config
    machinery — safe to adapt rather than reinvent). The explicit
    ``ROLLBACK`` is guarded so a SQLite auto-rollback (no active
    transaction left under lock contention) cannot shadow the original
    exception with a spurious rollback error."""
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


def _insert_messages(conn: sqlite3.Connection, conversation: Conversation, *, start_seq: int = 0) -> None:
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
    instead of re-attempting every row already durable."""
    conn.executemany(
        "INSERT OR IGNORE INTO messages "
        "(conversation_key, msg_seq, role, content, tool_calls_json, tool_call_id) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        [
            (conversation.key, seq, m.role, m.content, json.dumps(list(m.tool_calls)), m.tool_call_id)
            for seq, m in enumerate(conversation.messages[start_seq:], start=start_seq)
        ],
    )


def create(conn: sqlite3.Connection, conversation: Conversation, *, now: float) -> None:
    """Inserts one ``conversations`` row. Raises ``ConversationAlreadyExists``
    on a primary-key collision, leaving the prior row untouched."""
    with write_txn(conn) as c:
        try:
            c.execute(_INSERT_CONVERSATION_SQL, _conversation_row(conversation, now))
        except sqlite3.IntegrityError as exc:
            raise ConversationAlreadyExists(conversation.key) from exc
        _insert_messages(c, conversation)


def save(conn: sqlite3.Connection, conversation: Conversation, *, now: float, start_seq: int = 0) -> None:
    """Upserts the ``conversations`` row — always succeeds for a key the
    caller already owns (already created or already loaded), never raises
    for that reason alone. ``start_seq``: see ``_insert_messages``."""
    with write_txn(conn) as c:
        c.execute(
            _INSERT_CONVERSATION_SQL
            + " ON CONFLICT(key) DO UPDATE SET "
            + ", ".join(f"{col}=excluded.{col}" for col in _CONVERSATION_COLUMNS if col != "key"),
            _conversation_row(conversation, now),
        )
        _insert_messages(c, conversation, start_seq=start_seq)


def load(conn: sqlite3.Connection, key: ConversationKey, *, now: float) -> Conversation:
    """Raises ``ConversationNotFound`` if ``key`` has no row. ``now`` turns
    the stored remaining-seconds figure back into a fresh
    ``WallClockBudget.deadline`` for the resuming process's own clock."""
    row = conn.execute(f"SELECT {_CONVERSATION_COLUMNS_SQL} FROM conversations WHERE key = ?", (key,)).fetchone()
    if row is None:
        raise ConversationNotFound(key)

    msg_rows = conn.execute(
        "SELECT role, content, tool_calls_json, tool_call_id FROM messages "
        "WHERE conversation_key = ? ORDER BY msg_seq",
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
