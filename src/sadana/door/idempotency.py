"""Idempotency replay for `POST`: same key and body replays the stored
response byte-for-byte; same key, different body, is a mismatch.

`docs/tasks/H19-door-framework-token-conformance/spec.md` requirements 16-20.
I/O — its own file per `CLAUDE.md`'s rule that a module touching real I/O is
separate from a block's pure-function modules.

Adopted from `../hermes-agent/gateway/platforms/api_server_run_idempotency.py`
(spec.md § Design): the composite `(key, sub)` primary key, scoped per
principal so one account's key can never replay another's request, and
`hmac.compare_digest` for the fingerprint comparison — the same constant-time
discipline that file's own `reserve()` already uses. Declined from the same
file: its terminal-aware pruning. `door_idempotency` stores only an HTTP
replay record, never a live job's state (that is `operations.py`'s table),
so the console's own contract — a flat 24-hour expiry — is followed exactly,
not improved on speculatively.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import sqlite3
import threading
import time
from dataclasses import dataclass

from sadana.door.request import DoorResponse

_SCHEMA = """
CREATE TABLE IF NOT EXISTS door_idempotency (
    key TEXT NOT NULL,
    sub TEXT NOT NULL,
    method TEXT NOT NULL,
    path TEXT NOT NULL,
    request_sha256 TEXT NOT NULL,
    status INTEGER NOT NULL,
    headers_json TEXT NOT NULL,
    body BLOB NOT NULL,
    created_at REAL NOT NULL,
    PRIMARY KEY (key, sub)
);
"""

_RETENTION_SECONDS = 24 * 60 * 60

# The hour a sweep last ran, process-wide — a `DELETE` this cheap does not
# need its own background thread, only a guard against running it on every
# single write. Guarded by `_sweep_lock`, never read or written bare.
_sweep_lock = threading.Lock()
_last_swept_hour: int | None = None


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(_SCHEMA)


@dataclass(frozen=True)
class Replay:
    """`matched=True`: `status`/`headers`/`body` are the stored response to
    replay verbatim. `matched=False`: the key exists but the fingerprint
    doesn't match — `422 IDEMPOTENCY_MISMATCH` is the caller's job."""

    matched: bool
    status: int | None = None
    headers: dict[str, str] | None = None
    body: bytes | None = None


def _fingerprint(method: str, path: str, body: bytes) -> str:
    """Hashes method and path along with the body, not the body alone — the
    same `Idempotency-Key` reused against a different endpoint is exactly the
    kind of mismatch this exists to catch, not to silently replay past."""
    return hashlib.sha256(f"{method} {path}\n".encode() + body).hexdigest()


def replay(conn: sqlite3.Connection, *, key: str, sub: str, method: str, path: str, body: bytes) -> Replay | None:
    """`None`: no record for this `(key, sub)` — the caller processes the
    request fresh and should call `store()` afterward."""
    row = conn.execute(
        "SELECT request_sha256, status, headers_json, body FROM door_idempotency WHERE key = ? AND sub = ?",
        (key, sub),
    ).fetchone()
    if row is None:
        return None
    fingerprint = _fingerprint(method, path, body)
    if hmac.compare_digest(row["request_sha256"], fingerprint):
        return Replay(matched=True, status=row["status"], headers=json.loads(row["headers_json"]), body=row["body"])
    return Replay(matched=False)


def store(
    conn: sqlite3.Connection,
    *,
    key: str,
    sub: str,
    method: str,
    path: str,
    body: bytes,
    response: DoorResponse,
    now: float | None = None,
) -> None:
    """Writes one row. Called by `router.handle` inside the same `write_txn`
    as the request's own effect, per spec.md requirement 18 — so a crash
    between the two cannot leave one without the other."""
    now = time.time() if now is None else now
    _sweep_if_new_hour(conn, now=now)
    fingerprint = _fingerprint(method, path, body)
    conn.execute(
        "INSERT INTO door_idempotency "
        "(key, sub, method, path, request_sha256, status, headers_json, body, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (key, sub, method, path, fingerprint, response.status, json.dumps(dict(response.headers)), response.body, now),
    )


def _sweep_if_new_hour(conn: sqlite3.Connection, *, now: float) -> None:
    global _last_swept_hour
    hour = int(now // 3600)
    with _sweep_lock:
        if _last_swept_hour == hour:
            return
        _last_swept_hour = hour
    conn.execute("DELETE FROM door_idempotency WHERE created_at < ?", (now - _RETENTION_SECONDS,))
