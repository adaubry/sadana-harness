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

#: Every word each of this module's three `state` columns may hold. `turn_runs`
#: and `plugin_runs` are written once, after the fact, so they are only ever
#: terminal; H21 makes them live and will add a running word to each.
TURN_RUN_STATES = frozenset({"done", "failed"})
PLUGIN_RUN_STATES = frozenset({"closed", "failed"})
ARTIFACT_STATES = frozenset({"ready"})

RecordTurnFn = Callable[[TurnResult, float], Awaitable[None]]
RecordPluginRunFn = Callable[[TurnKey, int, "plugins.DagResult", float], Awaitable[None]]


@dataclass(frozen=True)
class Recorder:
    """The two callables `plugin_dispatch.py`'s `build_dispatch()` and
    `take_turn_and_reconcile()` accept as their `record_turn`/
    `record_plugin_run` parameters."""

    record_turn: RecordTurnFn
    record_plugin_run: RecordPluginRunFn


async def noop_record(*_args: object) -> None:
    """The default for `record_turn`/`record_plugin_run` everywhere
    (`plugin_dispatch.build_dispatch()`/`take_turn_and_reconcile()`,
    `gateway_dispatch.handle_inbound()`): a caller that hasn't opted into
    OBSERVABILITY-01 keeps behaving exactly as before — same posture
    `conversation._noop_persist` already takes for `persist`. One generic
    no-op, not one per signature: both `RecordTurnFn` and
    `RecordPluginRunFn` just need "accepts anything, does nothing"."""
    return None


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
    """Ensures both tables exist on ``conn`` (idempotent — safe on a fresh
    store or one that predates this item) and returns a `Recorder` bound to
    it. Call once per open connection, the same "create tables on open"
    posture `conversation_store.open_store()` already takes — a caller that
    only wants to read rows back (`subcommands/runs.py`, for a conversation
    nothing has recorded yet) still calls this and just doesn't use the
    `Recorder` it returns, rather than this module owning two ways to
    ensure the schema exists."""
    ensure_schema(conn)

    async def record_turn(result: TurnResult, duration_s: float) -> None:
        recorded_at = time.time()
        try:
            await asyncio.to_thread(_insert_turn_run, conn, result, duration_s, recorded_at)
        except sqlite3.Error:
            logger.warning("failed to record turn run for %r", result.turn_key, exc_info=True)

    async def record_plugin_run(
        turn_key: TurnKey, seq_in_turn: int, result: plugins.DagResult, duration_s: float
    ) -> None:
        recorded_at = time.time()
        try:
            await asyncio.to_thread(_insert_plugin_run, conn, turn_key, seq_in_turn, result, duration_s, recorded_at)
        except sqlite3.Error:
            logger.warning("failed to record plugin run for %r/%d", turn_key, seq_in_turn, exc_info=True)

    return Recorder(record_turn=record_turn, record_plugin_run=record_plugin_run)


def _insert_turn_run(conn: sqlite3.Connection, result: TurnResult, duration_s: float, recorded_at: float) -> None:
    run_id = ids.make_id("run")
    state = "done" if result.exit_reason == ExitReason.COMPLETED else "failed"
    with write_txn(conn) as c:
        c.execute(
            "INSERT INTO turn_runs (conversation_key, turn_seq, duration_s, model_calls, "
            "prompt_tokens, completion_tokens, exit_reason, recorded_at, id, state, started_at, ended_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
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
                # Reconstructed, not measured — this row is written once the
                # turn is already over. H21 measures them.
                recorded_at - duration_s,
                recorded_at,
            ),
        )
        ledger.record_change(c, noun="runs", id=run_id, kind="created", state=state, version=1, at=recorded_at)


def _insert_plugin_run(
    conn: sqlite3.Connection,
    turn_key: TurnKey,
    seq_in_turn: int,
    result: plugins.DagResult,
    duration_s: float,
    recorded_at: float,
) -> None:
    run_id = ids.make_id("run")
    state = "failed" if result.failed_node is not None else "closed"
    with write_txn(conn) as c:
        c.execute(
            "INSERT INTO plugin_runs (conversation_key, turn_seq, seq_in_turn, plugin, entry, "
            "duration_s, node_count, failed_node, recorded_at, id, state, started_at, ended_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
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
        ledger.record_change(c, noun="runs", id=run_id, kind="created", state=state, version=1, at=recorded_at)
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
