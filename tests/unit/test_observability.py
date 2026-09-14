"""Tests for sadana.observability: make_recorder(), timed(), best-effort
write semantics."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from pathlib import Path

import pytest

from conftest import dag_result, turn_result
from conftest import open_conn as _conn
from sadana import artifact_store, ids, ledger, observability, plugins
from sadana.conversation import ExitReason, TurnKey
from sadana.conversation_store import open_store
from sadana.observability import _insert_plugin_run, insert_turn_run, make_recorder

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


# ── H16: run identity, and what a run produced ─────────────────────────────


@pytest.mark.unit
def test_a_turn_run_carries_an_id_and_a_state_derived_from_its_exit(tmp_path: Path) -> None:
    conn = _conn()
    make_recorder(conn)

    insert_turn_run(conn, turn_result(), duration_s=2.0, recorded_at=100.0)

    row = conn.execute("SELECT id, state, started_at, ended_at, version FROM turn_runs").fetchone()
    assert ids.parse_id("run", row["id"]) is not None
    assert row["state"] == "done"
    assert (row["started_at"], row["ended_at"]) == (98.0, 100.0)
    assert [(c.noun, c.kind, c.id) for c in ledger.changes_since(conn, 0, 10)] == [("runs", "created", row["id"])]


@pytest.mark.unit
def test_a_turn_that_did_not_complete_is_recorded_as_failed(tmp_path: Path) -> None:
    conn = _conn()
    make_recorder(conn)
    result = replace(turn_result(), exit_reason=ExitReason.BUDGET_EXHAUSTED)

    insert_turn_run(conn, result, duration_s=1.0, recorded_at=100.0)

    assert conn.execute("SELECT state FROM turn_runs").fetchone()["state"] == "failed"


@pytest.mark.unit
def test_a_plugin_runs_artifacts_are_indexed_in_the_same_transaction(tmp_path: Path) -> None:
    conn = _conn()
    make_recorder(conn)
    run_dir = artifact_store.for_run("c1", 0, 0)
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "report.md").write_text("hello")
    result = replace(
        dag_result(),
        artifacts=(
            plugins.Artifact(kind="file", name="report", ref="report.md"),
            plugins.Artifact(kind="link", name="issue", ref="https://example.test/1"),
        ),
    )

    _insert_plugin_run(conn, TurnKey(conversation="c1", turn_seq=0), 0, result, duration_s=1.0, recorded_at=100.0)

    rows = conn.execute("SELECT name, kind, mime, size_bytes FROM artifacts ORDER BY name").fetchall()
    assert [(r["name"], r["kind"]) for r in rows] == [("issue", "link"), ("report", "file")]
    assert rows[1]["size_bytes"] == 5
    assert rows[1]["mime"] == "text/markdown"
    assert rows[0]["size_bytes"] is None
    assert [c.noun for c in ledger.changes_since(conn, 0, 10)] == ["runs", "artifacts", "artifacts"]


@pytest.mark.unit
def test_an_artifact_whose_file_has_gone_is_still_indexed(tmp_path: Path) -> None:
    """The size is stored rather than derived precisely because the file may
    not be there when somebody asks."""
    conn = _conn()
    make_recorder(conn)
    result = replace(dag_result(), artifacts=(plugins.Artifact(kind="file", name="gone", ref="gone.txt"),))

    _insert_plugin_run(conn, TurnKey(conversation="c1", turn_seq=0), 0, result, duration_s=1.0, recorded_at=100.0)

    row = conn.execute("SELECT name, size_bytes, state FROM artifacts").fetchone()
    assert (row["name"], row["size_bytes"], row["state"]) == ("gone", None, "ready")


# ── H21: live turn/plugin runs and spans ────────────────────────────────


@pytest.mark.unit
def test_record_turn_started_inserts_a_running_row(tmp_path: Path) -> None:
    conn = _conn()
    recorder = make_recorder(conn)
    turn_key = TurnKey(conversation="c1", turn_seq=0)

    run_id = asyncio.run(recorder.record_turn_started(turn_key, 0, 100.0))

    row = conn.execute("SELECT * FROM turn_runs WHERE conversation_key = ? AND turn_seq = ?", ("c1", 0)).fetchone()
    assert row["id"] == run_id
    assert row["state"] == "running"
    assert row["started_at"] == 100.0
    assert row["ended_at"] is None
    assert [(c.noun, c.kind, c.state) for c in ledger.changes_since(conn, 0, 10)] == [("runs", "created", "running")]


@pytest.mark.unit
def test_record_turn_after_started_updates_the_same_row_not_a_second_one(tmp_path: Path) -> None:
    conn = _conn()
    recorder = make_recorder(conn)
    turn_key = TurnKey(conversation="c1", turn_seq=0)
    run_id = asyncio.run(recorder.record_turn_started(turn_key, 0, 100.0))

    asyncio.run(recorder.record_turn(turn_result(conversation="c1", turn_seq=0), 2.5))

    rows = conn.execute("SELECT * FROM turn_runs WHERE conversation_key = ? AND turn_seq = ?", ("c1", 0)).fetchall()
    assert len(rows) == 1
    assert rows[0]["id"] == run_id  # the id minted at start survives the finish
    assert rows[0]["state"] == "done"
    assert rows[0]["started_at"] == 100.0  # the measured value, not reconstructed
    assert [c.kind for c in ledger.changes_since(conn, 0, 10)] == ["created", "changed"]


@pytest.mark.unit
def test_a_stopped_turn_is_recorded_as_stopped(tmp_path: Path) -> None:
    conn = _conn()
    make_recorder(conn)
    result = replace(turn_result(), exit_reason=ExitReason.INTERRUPTED)

    insert_turn_run(conn, result, duration_s=1.0, recorded_at=100.0)

    assert conn.execute("SELECT state FROM turn_runs").fetchone()["state"] == "stopped"


@pytest.mark.unit
def test_record_turn_started_returns_a_usable_id_even_if_the_write_fails(tmp_path: Path) -> None:
    conn = _conn()
    recorder = make_recorder(conn)
    conn.close()  # forces the threaded write to fail

    run_id = asyncio.run(recorder.record_turn_started(TurnKey(conversation="c1", turn_seq=0), 0, 100.0))

    assert ids.parse_id("run", run_id) is not None


@pytest.mark.unit
def test_record_plugin_run_started_then_finished_updates_one_row(tmp_path: Path) -> None:
    conn = _conn()
    recorder = make_recorder(conn)
    turn_key = TurnKey(conversation="c1", turn_seq=0)
    run_id = asyncio.run(recorder.record_plugin_run_started(turn_key, 0, "p1", "do_it", 50.0))

    asyncio.run(recorder.record_plugin_run(turn_key, 0, dag_result(), 1.0))

    rows = conn.execute(
        "SELECT * FROM plugin_runs WHERE conversation_key = ? AND turn_seq = ? AND seq_in_turn = ?", ("c1", 0, 0)
    ).fetchall()
    assert len(rows) == 1
    assert rows[0]["id"] == run_id
    assert rows[0]["state"] == "closed"
    assert rows[0]["started_at"] == 50.0
    assert [c.kind for c in ledger.changes_since(conn, 0, 10)] == ["created", "changed"]


@pytest.mark.unit
def test_record_node_writes_one_span_row(tmp_path: Path) -> None:
    conn = _conn()
    recorder = make_recorder(conn)
    turn_key = TurnKey(conversation="c1", turn_seq=0)

    asyncio.run(
        recorder.record_node(
            turn_key,
            0,
            0,
            "a",
            "compute",
            "ok",
            100.0,
            100.5,
            detail="ok",
        )
    )

    row = conn.execute("SELECT * FROM spans").fetchone()
    assert ids.parse_id("spn", row["id"]) is not None
    assert (row["node"], row["kind"], row["status"]) == ("a", "compute", "ok")
    assert row["started_at"] <= row["ended_at"]
    assert [(c.noun, c.kind, c.state) for c in ledger.changes_since(conn, 0, 10)] == [("spans", "created", "ok")]


@pytest.mark.unit
def test_record_node_carries_a_raising_nodes_error_message(tmp_path: Path) -> None:
    conn = _conn()
    recorder = make_recorder(conn)
    turn_key = TurnKey(conversation="c1", turn_seq=0)

    asyncio.run(recorder.record_node(turn_key, 0, 0, "a", "compute", "error", 100.0, 100.1, error="ValueError: boom"))

    assert conn.execute("SELECT error FROM spans").fetchone()["error"] == "ValueError: boom"


@pytest.mark.unit
def test_record_node_swallows_a_write_failure_instead_of_raising(tmp_path: Path) -> None:
    conn = _conn()
    recorder = make_recorder(conn)
    conn.close()

    asyncio.run(recorder.record_node(TurnKey(conversation="c1", turn_seq=0), 0, 0, "a", "compute", "ok", 1.0, 2.0))
