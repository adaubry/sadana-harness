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
import sqlite3
import time
from collections.abc import Awaitable, Callable, Coroutine
from dataclasses import dataclass
from typing import TypeVar

from sadana import plugins
from sadana.conversation import TurnKey, TurnResult
from sadana.conversation_store import write_txn

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
    PRIMARY KEY (conversation_key, turn_seq, seq_in_turn)
);
"""

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


def make_recorder(conn: sqlite3.Connection) -> Recorder:
    """Ensures both tables exist on ``conn`` (idempotent — safe on a fresh
    store or one that predates this item) and returns a `Recorder` bound to
    it. Call once per open connection, the same "create tables on open"
    posture `conversation_store.open_store()` already takes — a caller that
    only wants to read rows back (`subcommands/runs.py`, for a conversation
    nothing has recorded yet) still calls this and just doesn't use the
    `Recorder` it returns, rather than this module owning two ways to
    ensure the schema exists."""
    conn.executescript(_SCHEMA)

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
    with write_txn(conn) as c:
        c.execute(
            "INSERT INTO turn_runs (conversation_key, turn_seq, duration_s, model_calls, "
            "prompt_tokens, completion_tokens, exit_reason, recorded_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                result.turn_key.conversation,
                result.turn_key.turn_seq,
                duration_s,
                result.model_calls,
                result.usage.prompt_tokens,
                result.usage.completion_tokens,
                result.exit_reason.value,
                recorded_at,
            ),
        )


def _insert_plugin_run(
    conn: sqlite3.Connection,
    turn_key: TurnKey,
    seq_in_turn: int,
    result: plugins.DagResult,
    duration_s: float,
    recorded_at: float,
) -> None:
    with write_txn(conn) as c:
        c.execute(
            "INSERT INTO plugin_runs (conversation_key, turn_seq, seq_in_turn, plugin, entry, "
            "duration_s, node_count, failed_node, recorded_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
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
            ),
        )
