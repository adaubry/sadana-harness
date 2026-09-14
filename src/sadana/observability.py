"""A run leaves numbers behind, not just a transcript.

OBSERVABILITY-01 (`docs/tasks/OBSERVABILITY-01-turn-and-plugin-run-records/spec.md`).
The block's first module, and the first to touch real I/O (`sqlite3`, the
clock) — its own file per CLAUDE.md's rule that a module touching real I/O
never shares a file with a block's pure-function module.

Reuses `conversation_store`'s own connection and file: no second SQLite
store, no second writer (CLAUDE.md's warning about a second writer racing
one SQLite WAL file). Two tables, `turn_runs` and `plugin_runs`, each keyed
by a natural key its producing call already minted — `TurnResult.turn_key`
for a turn, plus a closure-local `seq_in_turn` for a plugin-dispatch call —
never a synthetic id (CLAUDE.md: "A derived/observability record's key
reuses whatever unique key its producing call already minted").

A recording write is best-effort: a `sqlite3.Error` is caught and logged,
never raised into the run being observed (CLAUDE.md: "An observability/
recording write's failure is caught and logged, never raised to the caller
of the run it is observing").
"""

from __future__ import annotations

import asyncio
import logging
import mimetypes
import sqlite3
import time
from collections.abc import Awaitable, Callable, Coroutine
from dataclasses import dataclass
from typing import TypeVar

from sadana import artifact_store, ids, ledger, plugins
from sadana.conversation import ExitReason, TurnKey, TurnResult
from sadana.conversation_store import fill_legacy_identity, migrate_columns, write_txn

logger = logging.getLogger(__name__)

_T = TypeVar("_T")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS turn_runs (
    conversation_key    TEXT NOT NULL,
    turn_seq            INTEGER NOT NULL,
    duration_s          REAL NOT NULL,
    model_calls         INTEGER NOT NULL,
    prompt_tokens       INTEGER NOT NULL,
    completion_tokens   INTEGER NOT NULL,
    exit_reason         TEXT NOT NULL,
    recorded_at         REAL NOT NULL,
    -- H16. `started_at`/`ended_at` are *reconstructed* from `recorded_at` and
    -- `duration_s`, because this row is written after the fact; H21 makes them
    -- measured. They are here now so H21 adds a measurement, not a migration.
    id                  TEXT,
    state               TEXT NOT NULL DEFAULT 'done',
    started_at          REAL,
    ended_at            REAL,
    version             INTEGER NOT NULL DEFAULT 1,
    PRIMARY KEY (conversation_key, turn_seq)
);

CREATE TABLE IF NOT EXISTS plugin_runs (
    conversation_key    TEXT NOT NULL,
    turn_seq            INTEGER NOT NULL,
    seq_in_turn         INTEGER NOT NULL,
    plugin              TEXT NOT NULL,
    entry               TEXT NOT NULL,
    duration_s          REAL NOT NULL,
    node_count          INTEGER NOT NULL,
    failed_node         TEXT,
    recorded_at         REAL NOT NULL,
    -- H16, as above.
    id                  TEXT,
    state               TEXT NOT NULL DEFAULT 'closed',
    started_at          REAL,
    ended_at            REAL,
    version             INTEGER NOT NULL DEFAULT 1,
    PRIMARY KEY (conversation_key, turn_seq, seq_in_turn)
);

-- H21. One row per node a plugin run actually visits, written live rather
-- than reconstructed — the walker itself reads no clock (plugin_manifest.py's
-- own rule), so `started_at`/`ended_at` are timestamped by whichever caller
-- turns two `NodeSink` calls into one row (`plugin_dispatch.py`).
CREATE TABLE IF NOT EXISTS spans (
    id               TEXT PRIMARY KEY,
    conversation_key TEXT NOT NULL,
    turn_seq         INTEGER NOT NULL,
    seq_in_turn      INTEGER NOT NULL,
    node_seq         INTEGER NOT NULL,
    node             TEXT NOT NULL,
    kind             TEXT NOT NULL,
    status           TEXT NOT NULL,
    started_at       REAL NOT NULL,
    ended_at         REAL,
    port             TEXT,
    detail           TEXT,
    input_preview    TEXT,
    output_preview   TEXT,
    error            TEXT,
    created_at       REAL NOT NULL,
    updated_at       REAL NOT NULL,
    version          INTEGER NOT NULL DEFAULT 1
);

-- H16 requirement 11: what a run produced, listed without walking the disk.
--
-- Filled from `DagResult.artifacts` in the same transaction as the plugin-run
-- row it belongs to, so a run and its outputs are never half-recorded. Keyed
-- by the run's own `(conversation_key, turn_seq, seq_in_turn)` plus a position,
-- which is the key `artifact_store` already lays the files out under — read
-- either and they line up.
CREATE TABLE IF NOT EXISTS artifacts (
    id               TEXT PRIMARY KEY,
    conversation_key TEXT NOT NULL,
    turn_seq         INTEGER NOT NULL,
    seq_in_turn      INTEGER NOT NULL,
    name             TEXT NOT NULL,
    kind             TEXT NOT NULL,
    ref              TEXT NOT NULL,
    mime             TEXT,
    size_bytes       INTEGER,
    state            TEXT NOT NULL DEFAULT 'ready',
    created_at       REAL NOT NULL,
    updated_at       REAL NOT NULL,
    version          INTEGER NOT NULL DEFAULT 1
);

-- H16. `fill_legacy_identity` asks `WHERE id IS NULL`, and these two tables are
-- the only unbounded ones in the store: one row per turn and one per plugin
-- run, forever. Without an index that question is a full scan on every open —
-- measured at 7.3 ms over 100,000 rows, for a pass that can only find rows once
-- and never again. A *partial* index costs nothing to store or maintain once no
-- row matches, which after the first upgrade is always.
"""

#: Created after `migrate_columns`, never in `_SCHEMA`: they name `id`, and on
#: a pre-H16 store that column does not exist yet when `_SCHEMA` runs. The same
#: ordering trap `conversation_store._INDEXES` documents.
_INDEXES = """
CREATE INDEX IF NOT EXISTS idx_turn_runs_unfilled ON turn_runs (conversation_key) WHERE id IS NULL;
CREATE INDEX IF NOT EXISTS idx_plugin_runs_unfilled ON plugin_runs (conversation_key) WHERE id IS NULL;
"""

#: See `conversation_store._MIGRATED_COLUMNS`.
_MIGRATED_COLUMNS: dict[str, tuple[tuple[str, str], ...]] = {
    "turn_runs": (
        ("id", "TEXT"),
        ("state", "TEXT NOT NULL DEFAULT 'done'"),
        ("started_at", "REAL"),
        ("ended_at", "REAL"),
        ("version", "INTEGER NOT NULL DEFAULT 1"),
    ),
    "plugin_runs": (
        ("id", "TEXT"),
        ("state", "TEXT NOT NULL DEFAULT 'closed'"),
        ("started_at", "REAL"),
        ("ended_at", "REAL"),
        ("version", "INTEGER NOT NULL DEFAULT 1"),
    ),
}

#: Every word each of this module's `state` columns may hold. H21: a
#: `turn_runs`/`plugin_runs` row now starts `"running"` (or `"waiting"` for a
#: turn with an outstanding pause) and settles into one of the terminal words
#: below, instead of being written once, after the fact, already terminal.
TURN_RUN_STATES = frozenset({"running", "waiting", "done", "failed", "stopped"})
PLUGIN_RUN_STATES = frozenset({"running", "closed", "failed"})
ARTIFACT_STATES = frozenset({"ready"})
SPAN_STATES = frozenset({"ok", "error", "skipped"})

RecordTurnFn = Callable[[TurnResult, float], Awaitable[None]]
RecordPluginRunFn = Callable[[TurnKey, int, "plugins.DagResult", float], Awaitable[None]]
#: H21. `(turn_key, user_message_seq, started_at) -> run_id`.
RecordTurnStartedFn = Callable[[TurnKey, int, float], Awaitable[str]]
#: H21. `(turn_key, seq_in_turn, plugin, entry, started_at) -> run_id`.
RecordPluginRunStartedFn = Callable[[TurnKey, int, str, str, float], Awaitable[str]]
#: H21. One `spans` row. Keyword-only past the first three (the run's own
#: address) — thirteen positional values is a call site nobody can read back.
RecordNodeFn = Callable[..., Awaitable[None]]


@dataclass(frozen=True)
class Recorder:
    """The callables `plugin_dispatch.py`'s `build_dispatch()` and
    `take_turn_and_reconcile()` accept as their `record_*` parameters."""

    record_turn: RecordTurnFn
    record_plugin_run: RecordPluginRunFn
    record_turn_started: RecordTurnStartedFn
    record_plugin_run_started: RecordPluginRunStartedFn
    record_node: RecordNodeFn


async def noop_record(*_args: object, **_kwargs: object) -> None:
    """The default for `record_turn`/`record_plugin_run`/`record_node`
    everywhere (`plugin_dispatch.build_dispatch()`/
    `take_turn_and_reconcile()`, `gateway_dispatch.handle_inbound()`): a
    caller that hasn't opted into OBSERVABILITY-01 keeps behaving exactly as
    before — same posture `conversation._noop_persist` already takes for
    `persist`. One generic no-op, not one per signature: every one of these
    just needs "accepts anything, does nothing" — `**_kwargs` too, since
    `record_node` (H21) calls with several keyword-only arguments."""
    return None


async def noop_record_started(*_args: object) -> str:
    """The default for `record_turn_started`/`record_plugin_run_started`: a
    caller that hasn't opted in gets a run id nobody will ever look up
    (nothing durable was written), rather than `None` — its two real
    callers (the door's own observer, `build_dispatch.ask()`'s child-turn
    bookkeeping) both need a plain `str` back unconditionally, and a second
    optional-vs-required return shape per caller is not worth carrying for
    a value that is never read when this default runs."""
    return ""


async def timed(coro: Coroutine[object, object, _T]) -> tuple[_T, float]:
    """Awaits ``coro``, returning its result and the wall-clock seconds it
    took. The one place a caller need touch the clock for this item — kept
    here, not in `plugin_dispatch.py`, per CLAUDE.md's real-I/O-is-its-own-
    file rule."""
    start = time.monotonic()
    result = await coro
    return result, time.monotonic() - start


def ensure_schema(conn: sqlite3.Connection) -> None:
    """Idempotent, and safe on a store that predates any of these tables.

    Split out of `make_recorder()` by H16: `stores.ensure_schemas` needs the
    tables to exist so `ledger.inventory()` can query them, and has no use for
    a `Recorder`. `make_recorder` still calls this, so no caller changed.
    """
    conn.executescript(_SCHEMA)
    migrate_columns(conn, _MIGRATED_COLUMNS)
    conn.executescript(_INDEXES)
    fill_legacy_identity(conn, "turn_runs", "run", time_columns=())
    fill_legacy_identity(conn, "plugin_runs", "run", time_columns=())


def make_recorder(conn: sqlite3.Connection) -> Recorder:
    """Ensures every table this module owns exists on ``conn`` (idempotent —
    safe on a fresh store or one that predates this item) and returns a
    `Recorder` bound to it. Call once per open connection, the same "create
    tables on open" posture `conversation_store.open_store()` already takes
    — a caller that only wants to read rows back (`subcommands/runs.py`,
    for a conversation nothing has recorded yet) still calls this and just
    doesn't use the `Recorder` it returns, rather than this module owning
    two ways to ensure the schema exists."""
    ensure_schema(conn)

    async def record_turn_started(turn_key: TurnKey, user_message_seq: int, started_at: float) -> str:
        # Minted before the write is attempted, and returned regardless of
        # whether it lands: the caller (the door's observer, a child turn's
        # own bookkeeping) needs a usable id even if the recording write
        # itself fails — the same "never raised into the run" posture every
        # write here already takes, extended to a function whose whole job
        # is to hand one back.
        run_id = ids.make_id("run")
        try:
            await asyncio.to_thread(insert_turn_run_started, conn, turn_key, user_message_seq, run_id, started_at)
        except sqlite3.Error:
            logger.warning("failed to record turn started for %r", turn_key, exc_info=True)
        return run_id

    async def record_turn(result: TurnResult, duration_s: float) -> None:
        recorded_at = time.time()
        try:
            await asyncio.to_thread(insert_turn_run, conn, result, duration_s, recorded_at)
        except sqlite3.Error:
            logger.warning("failed to record turn run for %r", result.turn_key, exc_info=True)

    async def record_plugin_run_started(
        turn_key: TurnKey, seq_in_turn: int, plugin: str, entry: str, started_at: float
    ) -> str:
        run_id = ids.make_id("run")
        try:
            await asyncio.to_thread(
                _insert_plugin_run_started, conn, turn_key, seq_in_turn, plugin, entry, run_id, started_at
            )
        except sqlite3.Error:
            logger.warning("failed to record plugin run started for %r/%d", turn_key, seq_in_turn, exc_info=True)
        return run_id

    async def record_plugin_run(
        turn_key: TurnKey, seq_in_turn: int, result: plugins.DagResult, duration_s: float
    ) -> None:
        recorded_at = time.time()
        try:
            await asyncio.to_thread(_insert_plugin_run, conn, turn_key, seq_in_turn, result, duration_s, recorded_at)
        except sqlite3.Error:
            logger.warning("failed to record plugin run for %r/%d", turn_key, seq_in_turn, exc_info=True)

    async def record_node(
        turn_key: TurnKey,
        seq_in_turn: int,
        node_seq: int,
        node: str,
        kind: str,
        status: str,
        started_at: float,
        ended_at: float,
        *,
        port: str | None = None,
        detail: str | None = None,
        input_preview: str | None = None,
        output_preview: str | None = None,
        error: str | None = None,
    ) -> None:
        try:
            await asyncio.to_thread(
                _insert_span,
                conn,
                turn_key,
                seq_in_turn,
                node_seq,
                node,
                kind,
                status,
                started_at,
                ended_at,
                port,
                detail,
                input_preview,
                output_preview,
                error,
            )
        except sqlite3.Error:
            logger.warning("failed to record span for %r/%d node %r", turn_key, seq_in_turn, node, exc_info=True)

    return Recorder(
        record_turn=record_turn,
        record_plugin_run=record_plugin_run,
        record_turn_started=record_turn_started,
        record_plugin_run_started=record_plugin_run_started,
        record_node=record_node,
    )


#: `save()`'s own upsert asymmetry, applied here: identity (`id`) and the
#: measured `started_at` are set on the **insert** branch only — a
#: `record_turn_started` call already wrote the real ones, and a conflict
#: means this is `record_turn`'s completion of that same row, which must
#: keep them. `version=turn_runs.version + 1` on conflict is what lets the
#: read-back below tell "this call inserted" from "this call completed an
#: already-started row" apart, the same way `conversation_store.save()`
#: already tells its own insert and update branches apart.
_UPSERT_TURN_RUN_SQL = (
    "INSERT INTO turn_runs (conversation_key, turn_seq, duration_s, model_calls, "
    "prompt_tokens, completion_tokens, exit_reason, recorded_at, id, state, started_at, ended_at) "
    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
    "ON CONFLICT(conversation_key, turn_seq) DO UPDATE SET "
    "duration_s=excluded.duration_s, model_calls=excluded.model_calls, "
    "prompt_tokens=excluded.prompt_tokens, completion_tokens=excluded.completion_tokens, "
    "exit_reason=excluded.exit_reason, recorded_at=excluded.recorded_at, state=excluded.state, "
    "ended_at=excluded.ended_at, version=turn_runs.version + 1"
)

_UPSERT_PLUGIN_RUN_SQL = (
    "INSERT INTO plugin_runs (conversation_key, turn_seq, seq_in_turn, plugin, entry, "
    "duration_s, node_count, failed_node, recorded_at, id, state, started_at, ended_at) "
    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
    "ON CONFLICT(conversation_key, turn_seq, seq_in_turn) DO UPDATE SET "
    "plugin=excluded.plugin, entry=excluded.entry, duration_s=excluded.duration_s, "
    "node_count=excluded.node_count, failed_node=excluded.failed_node, "
    "recorded_at=excluded.recorded_at, state=excluded.state, ended_at=excluded.ended_at, "
    "version=plugin_runs.version + 1"
)


def insert_turn_run_started(
    conn: sqlite3.Connection, turn_key: TurnKey, user_message_seq: int, run_id: str, started_at: float
) -> None:
    del user_message_seq  # not a column here; the door's own observer uses it to address the message row.
    with write_txn(conn) as c:
        c.execute(
            "INSERT INTO turn_runs (conversation_key, turn_seq, duration_s, model_calls, "
            "prompt_tokens, completion_tokens, exit_reason, recorded_at, id, state, started_at, ended_at) "
            "VALUES (?, ?, 0, 0, 0, 0, '', ?, ?, 'running', ?, NULL)",
            (turn_key.conversation, turn_key.turn_seq, started_at, run_id, started_at),
        )
        ledger.record_change(c, noun="runs", id=run_id, kind="created", state="running", version=1, at=started_at)


def _insert_plugin_run_started(
    conn: sqlite3.Connection,
    turn_key: TurnKey,
    seq_in_turn: int,
    plugin: str,
    entry: str,
    run_id: str,
    started_at: float,
) -> None:
    with write_txn(conn) as c:
        c.execute(
            "INSERT INTO plugin_runs (conversation_key, turn_seq, seq_in_turn, plugin, entry, "
            "duration_s, node_count, failed_node, recorded_at, id, state, started_at, ended_at) "
            "VALUES (?, ?, ?, ?, ?, 0, 0, NULL, ?, ?, 'running', ?, NULL)",
            (turn_key.conversation, turn_key.turn_seq, seq_in_turn, plugin, entry, started_at, run_id, started_at),
        )
        ledger.record_change(c, noun="runs", id=run_id, kind="created", state="running", version=1, at=started_at)


#: `TURN_RUN_STATES`' terminal words a finished turn's own `ExitReason` maps
#: to. `INTERRUPTED` is the one case `insert_turn_run` cannot answer with
#: the old two-way `done`/`failed` split alone.
_TERMINAL_TURN_STATE = {
    ExitReason.COMPLETED: "done",
    ExitReason.INTERRUPTED: "stopped",
}


def insert_turn_run(conn: sqlite3.Connection, result: TurnResult, duration_s: float, recorded_at: float) -> None:
    run_id = ids.make_id("run")  # used only if no `record_turn_started` row exists yet — see the SQL's own comment.
    state = _TERMINAL_TURN_STATE.get(result.exit_reason, "failed")
    with write_txn(conn) as c:
        c.execute(
            _UPSERT_TURN_RUN_SQL,
            (
                result.turn_key.conversation,
                result.turn_key.turn_seq,
                duration_s,
                result.model_calls,
                result.usage.prompt_tokens,
                result.usage.completion_tokens,
                result.exit_reason.value,
                recorded_at,
                run_id,
                state,
                # Reconstructed, not measured, on the insert branch only — a
                # caller with no `record_turn_started` call has no measured
                # value to keep; the conflict branch leaves the real,
                # measured `started_at` untouched (it is absent from the
                # `DO UPDATE SET` list above).
                recorded_at - duration_s,
                recorded_at,
            ),
        )
        row = c.execute(
            "SELECT id, version FROM turn_runs WHERE conversation_key = ? AND turn_seq = ?",
            (result.turn_key.conversation, result.turn_key.turn_seq),
        ).fetchone()
        ledger.record_change(
            c,
            noun="runs",
            id=row["id"],
            kind="created" if row["version"] == 1 else "changed",
            state=state,
            version=row["version"],
            at=recorded_at,
        )


def _insert_plugin_run(
    conn: sqlite3.Connection,
    turn_key: TurnKey,
    seq_in_turn: int,
    result: plugins.DagResult,
    duration_s: float,
    recorded_at: float,
) -> None:
    run_id = ids.make_id("run")  # used only if no `record_plugin_run_started` row exists yet.
    state = "failed" if result.failed_node is not None else "closed"
    with write_txn(conn) as c:
        c.execute(
            _UPSERT_PLUGIN_RUN_SQL,
            (
                turn_key.conversation,
                turn_key.turn_seq,
                seq_in_turn,
                result.plugin,
                result.entry,
                duration_s,
                len(result.trace),
                result.failed_node,
                recorded_at,
                run_id,
                state,
                recorded_at - duration_s,
                recorded_at,
            ),
        )
        row = c.execute(
            "SELECT id, version FROM plugin_runs WHERE conversation_key = ? AND turn_seq = ? AND seq_in_turn = ?",
            (turn_key.conversation, turn_key.turn_seq, seq_in_turn),
        ).fetchone()
        ledger.record_change(
            c,
            noun="runs",
            id=row["id"],
            kind="created" if row["version"] == 1 else "changed",
            state=state,
            version=row["version"],
            at=recorded_at,
        )
        _insert_artifacts(c, turn_key, seq_in_turn, result, recorded_at)


def _insert_artifacts(
    c: sqlite3.Connection, turn_key: TurnKey, seq_in_turn: int, result: plugins.DagResult, recorded_at: float
) -> None:
    """One `artifacts` row per thing the run produced, inside the plugin-run's
    own transaction.

    Same transaction on purpose: a run and the list of what it made are one
    fact, and a reader that could see the run without its outputs would have to
    guess whether the run made nothing or the recording was interrupted.

    `mime` and `size_bytes` are best-effort and `NULL` when they cannot be had
    — a link has no size, and a file may already be gone. They are stored at
    all (rather than derived on read) precisely because the file may be gone:
    a console listing last month's artifacts should still be able to say how
    big one was.

    The whole of this runs under `make_recorder`'s catch-and-log, like every
    other recording write: failing to note what a run produced must never
    reach the run (CLAUDE.md).
    """
    for artifact in result.artifacts:
        artifact_id = ids.make_id("art")
        c.execute(
            "INSERT INTO artifacts (id, conversation_key, turn_seq, seq_in_turn, name, kind, ref, "
            "mime, size_bytes, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                artifact_id,
                turn_key.conversation,
                turn_key.turn_seq,
                seq_in_turn,
                artifact.name,
                artifact.kind,
                artifact.ref,
                mimetypes.guess_type(artifact.ref)[0],
                _size_of(turn_key, seq_in_turn, artifact),
                recorded_at,
                recorded_at,
            ),
        )
        ledger.record_change(
            c, noun="artifacts", id=artifact_id, kind="created", state="ready", version=1, at=recorded_at
        )


def _size_of(turn_key: TurnKey, seq_in_turn: int, artifact: plugins.Artifact) -> int | None:
    """The artifact's size on disk, or `None` for a link or anything that
    cannot be stat'ed.

    `Artifact.ref` for a `file` is a path *inside the run's own output
    directory*, which `artifact_store.for_run` reconstructs from the same key
    this row is filed under. Not read from `artifact_store.output_dir()`: that
    is a `ContextVar` scoped to the walking run, and this executes after the
    run finished, when it is no longer set.
    """
    if artifact.kind != "file":
        return None
    try:
        path = artifact_store.for_run(turn_key.conversation, turn_key.turn_seq, seq_in_turn) / artifact.ref
        return path.stat().st_size
    except (OSError, ValueError):
        # `ValueError`, not only `OSError`: `Path.stat()` raises it for a path
        # holding a NUL byte, and `ref` comes from a plugin body rather than a
        # literal. The recorder above catches only `sqlite3.Error`, so a
        # `ValueError` escaping here would reach the run being observed —
        # exactly what CLAUDE.md forbids an observability write from doing.
        return None


def _insert_span(
    conn: sqlite3.Connection,
    turn_key: TurnKey,
    seq_in_turn: int,
    node_seq: int,
    node: str,
    kind: str,
    status: str,
    started_at: float,
    ended_at: float,
    port: str | None,
    detail: str | None,
    input_preview: str | None,
    output_preview: str | None,
    error: str | None,
) -> None:
    """One `spans` row, written once a node has already finished (or
    failed) — never updated afterward, unlike `turn_runs`/`plugin_runs`: a
    span's own two calls (`node_started`/`node_finished`) are folded into
    this single insert by `plugin_dispatch.py`'s sink wrapper, which is the
    one thing here that reads the clock (`plugin_manifest.py`'s walker
    itself never does)."""
    span_id = ids.make_id("spn")
    now_wall = time.time()
    with write_txn(conn) as c:
        c.execute(
            "INSERT INTO spans (id, conversation_key, turn_seq, seq_in_turn, node_seq, node, kind, "
            "status, started_at, ended_at, port, detail, input_preview, output_preview, error, "
            "created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                span_id,
                turn_key.conversation,
                turn_key.turn_seq,
                seq_in_turn,
                node_seq,
                node,
                kind,
                status,
                started_at,
                ended_at,
                port,
                detail,
                input_preview,
                output_preview,
                error,
                now_wall,
                now_wall,
            ),
        )
        ledger.record_change(c, noun="spans", id=span_id, kind="created", state=status, version=1, at=now_wall)
