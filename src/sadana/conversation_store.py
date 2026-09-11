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
from dataclasses import asdict, dataclass, replace
from pathlib import Path

from sadana import config, context, plugins
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
    artifacts_json    TEXT NOT NULL
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


def save_pause(
    conn: sqlite3.Connection,
    *,
    conversation_key: str,
    plugin: str,
    entry: str,
    node: str,
    trace: tuple[plugins.NodeTrace, ...],
    artifacts: tuple[plugins.Artifact, ...],
) -> None:
    """Upserts by ``conversation_key`` — a run that pauses a second time
    (at a second `wait` node) overwrites its own prior pause row cleanly
    rather than leaving two, matching ``PRIMARY KEY``'s own "at most one
    outstanding pause per conversation" constraint."""
    with write_txn(conn) as c:
        c.execute(
            "INSERT INTO plugin_pauses (conversation_key, plugin, entry, node, trace_json, artifacts_json) "
            "VALUES (?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(conversation_key) DO UPDATE SET "
            "plugin=excluded.plugin, entry=excluded.entry, node=excluded.node, "
            "trace_json=excluded.trace_json, artifacts_json=excluded.artifacts_json",
            (
                conversation_key,
                plugin,
                entry,
                node,
                json.dumps([asdict(t) for t in trace]),
                json.dumps([asdict(a) for a in artifacts]),
            ),
        )


def save_pause_from_result(conn: sqlite3.Connection, *, conversation_key: str, result: plugins.DagResult) -> None:
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
    )


def load_pause(conn: sqlite3.Connection, *, conversation_key: str) -> Pause | None:
    """``None`` when the conversation has no outstanding pause — the normal
    case every inbound message not resuming something checks first."""
    row = conn.execute(
        "SELECT plugin, entry, node, trace_json, artifacts_json FROM plugin_pauses WHERE conversation_key = ?",
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
    )


def delete_pause(conn: sqlite3.Connection, *, conversation_key: str) -> None:
    """A key with no row is a silent no-op, same as ``delete_scheduled_trigger``."""
    with write_txn(conn) as c:
        c.execute("DELETE FROM plugin_pauses WHERE conversation_key = ?", (conversation_key,))
