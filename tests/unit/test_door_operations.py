"""`door/operations.py` — the highest-risk file in this work item.

Covers, directly and separately from the router: the fast path writes
nothing (spec.md requirement 22's whole point), the promotion race between
the timeout firing and `fn` actually finishing, and the first real exercise
of `ledger.record_change` for the "operations"/"harness" nouns H16 reserved
and never wrote to.
"""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path

import pytest

from sadana import ids, ledger
from sadana.conversation_store import write_txn
from sadana.door import operations, problems


class _FakeConns:
    """`run_bounded`/`_promote`/`_transition` only ever touch `.writer` —
    duck typing stands in for `stores.Connections` without importing it."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self.writer = conn


def _conns(tmp_path: Path) -> _FakeConns:
    # check_same_thread=False: the real `stores.Connections.writer` is built
    # the same way (`stores.py:153`) -- a `run_bounded` completion callback
    # runs on a pool thread and must reach the same writer connection.
    conn = sqlite3.connect(tmp_path / "o.db", isolation_level=None, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    ledger.ensure_schema(conn)
    operations.ensure_schema(conn)
    return _FakeConns(conn)


def _wait_until(predicate, *, timeout: float = 2.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    pytest.fail("condition was never true within the wait window")


@pytest.mark.unit
def test_a_fast_call_writes_zero_operations_rows(tmp_path: Path) -> None:
    conns = _conns(tmp_path)

    result = operations.run_bounded(conns, lambda: {"id": "wgt_1"}, timeout=1.0)

    assert result.done is True
    assert result.value == {"id": "wgt_1"}
    assert conns.writer.execute("SELECT COUNT(*) FROM operations").fetchone()[0] == 0
    assert conns.writer.execute("SELECT COUNT(*) FROM changes WHERE noun = 'operations'").fetchone()[0] == 0


@pytest.mark.unit
def test_a_slow_call_promotes_then_completes_exactly_once(tmp_path: Path) -> None:
    conns = _conns(tmp_path)

    def _slow() -> dict:
        time.sleep(0.15)
        return {"id": "wgt_2"}

    result = operations.run_bounded(conns, _slow, timeout=0.02, resource=("widgets", "wgt_2"))

    assert result.done is False
    assert result.operation is not None
    assert result.operation.state == "running"
    assert result.operation.resource_noun == "widgets"
    assert result.operation.resource_id == "wgt_2"

    op_id = result.operation.id
    _wait_until(lambda: operations.get(conns.writer, op_id).state != "running")

    final = operations.get(conns.writer, op_id)
    assert final is not None
    assert final.state == "succeeded"
    assert final.version == 2

    # Exactly one created row and one transition row -- never zero, never two.
    rows = conns.writer.execute(
        "SELECT kind, state FROM changes WHERE noun = 'operations' AND id = ? ORDER BY cursor", (op_id,)
    ).fetchall()
    assert [(r["kind"], r["state"]) for r in rows] == [("created", "running"), ("changed", "succeeded")]


@pytest.mark.unit
def test_a_slow_call_that_raises_transitions_to_failed(tmp_path: Path) -> None:
    conns = _conns(tmp_path)

    def _slow_and_broken() -> None:
        time.sleep(0.15)
        raise RuntimeError("boom")

    result = operations.run_bounded(conns, _slow_and_broken, timeout=0.02)
    op_id = result.operation.id  # type: ignore[union-attr]

    _wait_until(lambda: operations.get(conns.writer, op_id).state != "running")

    final = operations.get(conns.writer, op_id)
    assert final is not None
    assert final.state == "failed"
    assert final.error is not None
    assert final.error["code"] == "INTERNAL"


@pytest.mark.unit
def test_a_slow_call_that_returns_a_problem_transitions_to_failed(tmp_path: Path) -> None:
    conns = _conns(tmp_path)

    def _slow_and_denied():
        time.sleep(0.15)
        return problems.make("FORBIDDEN", "nope")

    result = operations.run_bounded(conns, _slow_and_denied, timeout=0.02)
    op_id = result.operation.id  # type: ignore[union-attr]

    _wait_until(lambda: operations.get(conns.writer, op_id).state != "running")

    final = operations.get(conns.writer, op_id)
    assert final.error["code"] == "FORBIDDEN"  # type: ignore[index]


@pytest.mark.unit
def test_a_call_finishing_in_the_promotion_gap_still_resolves_once(tmp_path: Path) -> None:
    """`fn` finishes just around the timeout boundary -- either the fast or
    the promoted path is taken, but never both, and never neither."""
    conns = _conns(tmp_path)

    def _borderline() -> str:
        time.sleep(0.03)
        return "ok"

    result = operations.run_bounded(conns, _borderline, timeout=0.03)

    if result.done:
        assert result.value == "ok"
    else:
        op_id = result.operation.id  # type: ignore[union-attr]
        _wait_until(lambda: operations.get(conns.writer, op_id).state != "running")
        assert operations.get(conns.writer, op_id).state == "succeeded"  # type: ignore[union-attr]


@pytest.mark.unit
def test_resume_on_start_fails_every_row_still_running(tmp_path: Path) -> None:
    conns = _conns(tmp_path)

    def _never_finishes() -> None:
        time.sleep(0.5)

    result = operations.run_bounded(conns, _never_finishes, timeout=0.02)
    op_id = result.operation.id  # type: ignore[union-attr]

    operations.resume_on_start(conns)

    final = operations.get(conns.writer, op_id)
    assert final is not None
    assert final.state == "failed"
    assert final.detail == "the process restarted"


@pytest.mark.unit
def test_ensure_schema_adding_resume_target_version_is_idempotent(tmp_path: Path) -> None:
    """CLAUDE.md's own rule for a new column on an existing table: a
    guarded `ALTER TABLE`, safe to run again on a connection that already
    has it — never a backfill migration that only runs once."""
    conn = sqlite3.connect(tmp_path / "idempotent.db", isolation_level=None)
    conn.row_factory = sqlite3.Row
    ledger.ensure_schema(conn)
    operations.ensure_schema(conn)
    operations.ensure_schema(conn)  # must not raise "duplicate column"

    columns = {row["name"] for row in conn.execute("PRAGMA table_info(operations)")}
    assert "resume_target_version" in columns


@pytest.mark.unit
def test_resume_on_start_succeeds_an_upgrade_whose_target_now_matches(tmp_path: Path) -> None:
    from sadana import __version__

    conns = _conns(tmp_path)
    op = operations.start_operation(conns, resume_target_version=__version__)

    operations.resume_on_start(conns)

    final = operations.get(conns.writer, op.id)
    assert final is not None
    assert final.state == "succeeded"
    assert final.detail is None


@pytest.mark.unit
def test_resume_on_start_fails_an_upgrade_naming_the_actual_version(tmp_path: Path) -> None:
    from sadana import __version__

    conns = _conns(tmp_path)
    op = operations.start_operation(conns, resume_target_version="not-" + __version__)

    operations.resume_on_start(conns)

    final = operations.get(conns.writer, op.id)
    assert final is not None
    assert final.state == "failed"
    assert final.detail == f"restarted on {__version__}"


@pytest.mark.unit
def test_resume_on_start_upgrade_branch_does_not_change_a_plain_rows_own_behaviour(tmp_path: Path) -> None:
    """The additive branch is keyed on `resume_target_version` alone — a
    `running` row with none set (every caller before H30) keeps today's
    unconditional "the process restarted" failure."""
    conns = _conns(tmp_path)
    op = operations.start_operation(conns)  # no `resume_target_version`

    operations.resume_on_start(conns)

    final = operations.get(conns.writer, op.id)
    assert final is not None
    assert final.state == "failed"
    assert final.detail == "the process restarted"


@pytest.mark.unit
def test_ledger_record_change_guards_the_operations_prefix(tmp_path: Path) -> None:
    """The direct exercise the plan calls for: H16 reserved `operations`
    with prefix `op` and never wrote to it. A mismatched id must still raise
    here, not only once `door/router.py` exists to call through it."""
    conns = _conns(tmp_path)
    good_id = ids.make_id("op")
    with write_txn(conns.writer) as c:
        ledger.record_change(c, noun="operations", id=good_id, kind="created", state="running", version=1, at=1.0)

    with pytest.raises(ValueError), write_txn(conns.writer) as c:
        ledger.record_change(
            c, noun="operations", id="wrong_prefix_id", kind="created", state="running", version=1, at=1.0
        )


@pytest.mark.unit
def test_ledger_record_change_guards_the_harness_prefix(tmp_path: Path) -> None:
    conns = _conns(tmp_path)
    good_id = ids.make_id("hrn")
    with write_txn(conns.writer) as c:
        ledger.record_change(c, noun="harness", id=good_id, kind="changed", state=None, version=1, at=1.0)

    with pytest.raises(ValueError), write_txn(conns.writer) as c:
        ledger.record_change(c, noun="harness", id="op_" + "0" * 32, kind="changed", state=None, version=1, at=1.0)


@pytest.mark.unit
def test_to_wire_shape() -> None:
    op = operations.Operation(
        id="op_x",
        state="running",
        resource_noun="widgets",
        resource_id="wgt_1",
        error=None,
        detail=None,
        created_at=0.0,
        updated_at=0.0,
        version=1,
    )
    wire = op.to_wire()
    assert wire["resource"] == {"noun": "widgets", "id": "wgt_1"}
    assert wire["error"] is None
    assert wire["created_at"] == "1970-01-01T00:00:00.000Z"


@pytest.mark.unit
def test_to_wire_folds_a_restart_detail_into_the_error_slot() -> None:
    op = operations.Operation(
        id="op_x",
        state="failed",
        resource_noun=None,
        resource_id=None,
        error=None,
        detail="the process restarted",
        created_at=0.0,
        updated_at=0.0,
        version=2,
    )
    assert op.to_wire()["error"] == {"detail": "the process restarted"}
