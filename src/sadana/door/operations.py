"""A ticket for anything the door can't answer synchronously.

`docs/tasks/H19-door-framework-token-conformance/spec.md` requirements 21-24,
rewritten during this work item's own design review to close an ambiguity
the first draft left open: does every dispatch pay for an `operations` row,
or only a genuinely slow one? It's the second. `run_bounded()` always
dispatches through a thread (that's the only way to give up waiting without
killing the work), but writes nothing to `operations` unless `timeout`
actually elapses — a plain `list`/`get` costs exactly what it cost before
this module existed.

I/O — its own file per `CLAUDE.md`'s rule. `Connections` is imported only
under `TYPE_CHECKING`: `stores.py` imports this module to wire
`ensure_schema` into `ensure_schemas`, so a real import here would be a
cycle. `run_bounded` only ever calls `.reader()`/`.writer` on what it is
given — duck typing, not a dependency.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeoutError
from dataclasses import dataclass
from typing import TYPE_CHECKING

from sadana import config, ids, ledger
from sadana.conversation_store import write_txn
from sadana.door import grammar, problems

if TYPE_CHECKING:
    from sadana.stores import Connections

_SCHEMA = """
CREATE TABLE IF NOT EXISTS operations (
    id TEXT PRIMARY KEY,
    state TEXT NOT NULL,
    resource_noun TEXT,
    resource_id TEXT,
    error_json TEXT,
    detail_json TEXT,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    version INTEGER NOT NULL DEFAULT 1
);
"""


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(_SCHEMA)


@dataclass(frozen=True)
class Operation:
    id: str
    state: str  # "running" | "succeeded" | "failed"
    resource_noun: str | None
    resource_id: str | None
    error: dict[str, object] | None
    detail: str | None
    created_at: float
    updated_at: float
    version: int

    def to_wire(self) -> dict[str, object]:
        resource = {"noun": self.resource_noun, "id": self.resource_id} if self.resource_noun else None
        error = self.error if self.error is not None else ({"detail": self.detail} if self.detail else None)
        return {
            "id": self.id,
            "state": self.state,
            "resource": resource,
            "error": error,
            "created_at": grammar.render_ts(self.created_at),
            "updated_at": grammar.render_ts(self.updated_at),
        }


def _row_to_operation(row: sqlite3.Row) -> Operation:
    return Operation(
        id=row["id"],
        state=row["state"],
        resource_noun=row["resource_noun"],
        resource_id=row["resource_id"],
        error=json.loads(row["error_json"]) if row["error_json"] else None,
        detail=json.loads(row["detail_json"])["detail"] if row["detail_json"] else None,
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        version=row["version"],
    )


def get(conn: sqlite3.Connection, id: str) -> Operation | None:
    row = conn.execute("SELECT * FROM operations WHERE id = ?", (id,)).fetchone()
    return _row_to_operation(row) if row is not None else None


def _promote(conns: Connections, *, resource: tuple[str, str] | None) -> Operation:
    op_id = ids.make_id("op")
    now = time.time()
    resource_noun, resource_id = resource if resource is not None else (None, None)
    with write_txn(conns.writer) as c:
        c.execute(
            "INSERT INTO operations "
            "(id, state, resource_noun, resource_id, error_json, detail_json, created_at, updated_at, version) "
            "VALUES (?, 'running', ?, ?, NULL, NULL, ?, ?, 1)",
            (op_id, resource_noun, resource_id, now, now),
        )
        ledger.record_change(c, noun="operations", id=op_id, kind="created", state="running", version=1, at=now)
    return Operation(
        id=op_id,
        state="running",
        resource_noun=resource_noun,
        resource_id=resource_id,
        error=None,
        detail=None,
        created_at=now,
        updated_at=now,
        version=1,
    )


def _transition(conns: Connections, op_id: str, *, state: str, error: dict[str, object] | None) -> None:
    now = time.time()
    with write_txn(conns.writer) as c:
        row = c.execute("SELECT version FROM operations WHERE id = ?", (op_id,)).fetchone()
        if row is None:
            return
        new_version = row["version"] + 1
        c.execute(
            "UPDATE operations SET state = ?, error_json = ?, updated_at = ?, version = ? WHERE id = ?",
            (state, json.dumps(error) if error is not None else None, now, new_version, op_id),
        )
        ledger.record_change(c, noun="operations", id=op_id, kind="changed", state=state, version=new_version, at=now)


def resume_on_start(conns: Connections) -> None:
    """Called once, at process start. A row still `running` belonged to a
    process that no longer exists — it cannot finish, so it is marked failed
    with a detail naming why, never left `running` forever."""
    now = time.time()
    with write_txn(conns.writer) as c:
        rows = c.execute("SELECT id, version FROM operations WHERE state = 'running'").fetchall()
        for row in rows:
            c.execute(
                "UPDATE operations SET state = 'failed', detail_json = ?, updated_at = ?, version = ? WHERE id = ?",
                (json.dumps({"detail": "the process restarted"}), now, row["version"] + 1, row["id"]),
            )
            ledger.record_change(
                c, noun="operations", id=row["id"], kind="changed", state="failed", version=row["version"] + 1, at=now
            )


# ── the package-owned pool ──────────────────────────────────────────────
_executor_lock = threading.Lock()
_executor: ThreadPoolExecutor | None = None


def _get_executor() -> ThreadPoolExecutor:
    """Lazily constructed: importing this module must never spin up worker
    threads by itself, or every test process that imports `door.router`
    transitively pays for a thread pool it never uses."""
    global _executor
    with _executor_lock:
        if _executor is None:
            _executor = ThreadPoolExecutor(max_workers=config.get("door.workers", 8), thread_name_prefix="door-op")
        return _executor


@dataclass(frozen=True)
class BoundedResult:
    """`done=True`: `value` is `fn`'s own return value, exactly as if it had
    been called directly. `done=False`: `operation` is the promoted, durable
    ticket; poll `GET /v1/operations/{id}`."""

    done: bool
    value: object = None
    operation: Operation | None = None


def run_bounded(
    conns: Connections,
    fn: Callable[[], object],
    *,
    timeout: float = 2.0,
    resource: tuple[str, str] | None = None,
) -> BoundedResult:
    """Submits `fn` to the pool and waits up to `timeout`.

    The race this exists to get right: `fn` may finish in the gap between
    the wait timing out and this function deciding whether to promote. The
    completion callback is registered *before* waiting, and `promoted_op`
    (guarded by `lock`, local to this call — never a module-global, so
    concurrent calls never contend with each other over it) is the single
    fact both sides check: the callback transitions a row only if one was
    actually created, and the timeout branch creates one only if `fn`
    genuinely has not finished yet by the time it holds `lock`."""
    future: Future[object] = _get_executor().submit(fn)
    lock = threading.Lock()
    promoted_op: Operation | None = None

    def _on_done(f: Future[object]) -> None:
        with lock:
            if promoted_op is None:
                return
            op = promoted_op
        try:
            result = f.result()
        except Exception as exc:  # noqa: BLE001 -- must never raise on the pool thread
            _transition(conns, op.id, state="failed", error=problems.make("INTERNAL", str(exc)).to_json())
            return
        if isinstance(result, problems.Problem):
            _transition(conns, op.id, state="failed", error=result.to_json())
        else:
            _transition(conns, op.id, state="succeeded", error=None)

    future.add_done_callback(_on_done)

    try:
        value = future.result(timeout=timeout)
        return BoundedResult(done=True, value=value)
    except FutureTimeoutError:
        with lock:
            if future.done():
                pass  # finished in the gap -- fall through to the fast path, no promotion.
            else:
                op = _promote(conns, resource=resource)
                promoted_op = op
                return BoundedResult(done=False, operation=op)
        return BoundedResult(done=True, value=future.result())
