"""Tests for sadana.observability: make_recorder(), timed(), best-effort
write semantics."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from conftest import dag_result, turn_result
from sadana import observability
from sadana.conversation import TurnKey
from sadana.conversation_store import open_store

# ── timed ─────────────────────────────────────────────────────────────────


@pytest.mark.unit
def test_timed_returns_result_and_a_non_negative_duration() -> None:
    async def _go() -> str:
        return "done"

    result, duration_s = asyncio.run(observability.timed(_go()))
    assert result == "done"
    assert duration_s >= 0.0


# ── make_recorder / schema ───────────────────────────────────────────────


@pytest.mark.unit
def test_make_recorder_creates_both_tables_on_a_fresh_store(tmp_path: Path) -> None:
    conn = open_store(tmp_path / "c.db")
    try:
        observability.make_recorder(conn)
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        assert {"turn_runs", "plugin_runs"} <= tables
    finally:
        conn.close()


@pytest.mark.unit
def test_make_recorder_is_idempotent_on_a_store_that_already_has_the_tables(tmp_path: Path) -> None:
    path = tmp_path / "c.db"
    conn = open_store(path)
    observability.make_recorder(conn)
    conn.close()

    conn = open_store(path)
    try:
        observability.make_recorder(conn)  # must not raise
    finally:
        conn.close()


# ── record_turn / record_plugin_run ─────────────────────────────────────


@pytest.mark.unit
def test_record_turn_writes_a_row_matching_theturn_result(tmp_path: Path) -> None:
    conn = open_store(tmp_path / "c.db")
    try:
        recorder = observability.make_recorder(conn)
        asyncio.run(recorder.record_turn(turn_result(conversation="c1", turn_seq=3), 1.5))

        row = conn.execute("SELECT * FROM turn_runs WHERE conversation_key = ? AND turn_seq = ?", ("c1", 3)).fetchone()
        assert row is not None
        assert row["duration_s"] == 1.5
        assert row["model_calls"] == 2
        assert row["prompt_tokens"] == 10
        assert row["completion_tokens"] == 5
        assert row["exit_reason"] == "completed"
    finally:
        conn.close()


@pytest.mark.unit
def test_record_plugin_run_writes_a_row_matching_thedag_result(tmp_path: Path) -> None:
    conn = open_store(tmp_path / "c.db")
    try:
        recorder = observability.make_recorder(conn)
        turn_key = TurnKey(conversation="c1", turn_seq=0)
        asyncio.run(recorder.record_plugin_run(turn_key, 2, dag_result(failed_node="a"), 0.25))

        row = conn.execute(
            "SELECT * FROM plugin_runs WHERE conversation_key = ? AND turn_seq = ? AND seq_in_turn = ?", ("c1", 0, 2)
        ).fetchone()
        assert row is not None
        assert row["plugin"] == "p1"
        assert row["entry"] == "do_it"
        assert row["duration_s"] == 0.25
        assert row["node_count"] == 2
        assert row["failed_node"] == "a"
    finally:
        conn.close()


@pytest.mark.unit
def test_record_turn_swallows_a_write_failure_instead_of_raising(tmp_path: Path) -> None:
    conn = open_store(tmp_path / "c.db")
    recorder = observability.make_recorder(conn)
    conn.close()  # forces the threaded write to fail

    asyncio.run(recorder.record_turn(turn_result(), 1.0))  # must not raise


@pytest.mark.unit
def test_record_plugin_run_swallows_a_write_failure_instead_of_raising(tmp_path: Path) -> None:
    conn = open_store(tmp_path / "c.db")
    recorder = observability.make_recorder(conn)
    conn.close()  # forces the threaded write to fail

    turn_key = TurnKey(conversation="c1", turn_seq=0)
    asyncio.run(recorder.record_plugin_run(turn_key, 0, dag_result(), 1.0))  # must not raise
