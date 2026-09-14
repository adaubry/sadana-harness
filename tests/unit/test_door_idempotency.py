"""`door/idempotency.py` — replay, mismatch, pass-through, the hourly sweep."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from sadana.door import idempotency
from sadana.door.request import DoorResponse


def _conn(tmp_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(tmp_path / "i.db", isolation_level=None)
    conn.row_factory = sqlite3.Row
    idempotency.ensure_schema(conn)
    return conn


_RESPONSE = DoorResponse(status=201, headers={"Content-Type": "application/json"}, body=b'{"id": "wgt_1"}')


def _store(conn: sqlite3.Connection, **overrides: object) -> None:
    defaults: dict[str, object] = dict(
        key="k1",
        sub="console:u1",
        method="POST",
        path="/v1/widgets",
        body=b'{"name": "w"}',
        response=_RESPONSE,
        now=1.0,
    )
    defaults.update(overrides)
    idempotency.store(conn, **defaults)  # type: ignore[arg-type]


def _replay(conn: sqlite3.Connection, **overrides: object):
    defaults: dict[str, object] = dict(
        key="k1", sub="console:u1", method="POST", path="/v1/widgets", body=b'{"name": "w"}'
    )
    defaults.update(overrides)
    return idempotency.replay(conn, **defaults)  # type: ignore[arg-type]


@pytest.mark.unit
def test_no_existing_key_is_a_pass_through(tmp_path: Path) -> None:
    assert _replay(_conn(tmp_path)) is None


@pytest.mark.unit
def test_same_key_same_body_replays_the_stored_response_byte_for_byte(tmp_path: Path) -> None:
    conn = _conn(tmp_path)
    _store(conn)

    result = _replay(conn)

    assert result is not None
    assert result.matched is True
    assert result.status == 201
    assert result.body == _RESPONSE.body
    assert result.headers == dict(_RESPONSE.headers)


@pytest.mark.unit
def test_same_key_different_body_is_a_mismatch(tmp_path: Path) -> None:
    conn = _conn(tmp_path)
    _store(conn, body=b'{"name": "a"}')

    result = _replay(conn, body=b'{"name": "b"}')

    assert result is not None
    assert result.matched is False


@pytest.mark.unit
def test_the_same_key_from_a_different_account_is_scoped_separately(tmp_path: Path) -> None:
    conn = _conn(tmp_path)
    _store(conn)

    assert _replay(conn, sub="console:u2") is None


@pytest.mark.unit
def test_the_same_key_reused_against_a_different_path_is_a_mismatch(tmp_path: Path) -> None:
    conn = _conn(tmp_path)
    _store(conn)

    result = _replay(conn, path="/v1/gadgets")

    assert result is not None
    assert result.matched is False


@pytest.mark.unit
def test_a_row_older_than_24h_is_swept_on_the_first_write_of_a_new_hour(tmp_path: Path) -> None:
    conn = _conn(tmp_path)
    _store(conn, key="stale", now=1000.0)

    # A second store(), one hour boundary and more than 24h later.
    _store(conn, key="fresh", now=1000.0 + 25 * 3600)

    row = conn.execute("SELECT key FROM door_idempotency WHERE key = 'stale'").fetchone()
    assert row is None
    row = conn.execute("SELECT key FROM door_idempotency WHERE key = 'fresh'").fetchone()
    assert row is not None
