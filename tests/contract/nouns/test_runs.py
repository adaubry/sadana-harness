"""`runs` noun contract test — spec.md requirements 21-23.

Hand-seeded `turn_runs`/`plugin_runs`/`messages` rows (plan.md step 5) — no
live turn needed to prove this noun's own field derivation.
"""

# ruff: noqa: F401, F811 -- `keys`/`schema` are pytest fixtures imported from
# `_scaffold.py` (not a `conftest.py` -- see that file's own docstring) and
# used by name as a parameter in every test function below; ruff's static
# analysis reads each repeated parameter as redefining an "unused" import,
# which is exactly the documented pytest cross-module-fixture idiom, not a
# real redefinition.

from __future__ import annotations

import json
from pathlib import Path

import pytest
from _scaffold import (
    keys,
    make_ctx,
    make_runtime,
    mint,
    req,
    schema,
    seed_conversation,
    seed_plugin_run,
    seed_turn_run,
    validate,
)

from sadana import ids, stores
from sadana.conversation_store import write_txn
from sadana.door.nouns import runs
from sadana.door.router import handle

pytestmark = pytest.mark.contract


def _seed_user_message(conn, *, conversation_key: str, run_id: str, msg_seq: int = 0) -> str:
    msg_id = ids.make_id("msg")
    with write_txn(conn) as c:
        c.execute(
            "INSERT INTO messages (conversation_key, msg_seq, role, content, tool_calls_json, id, created_at, "
            "run_id) VALUES (?, ?, 'user', 'hello', '[]', ?, ?, ?)",
            (conversation_key, msg_seq, msg_id, 999.0, run_id),
        )
    return msg_id


def test_run_renders_conversation_message_and_exit_reason(keys, tmp_path: Path, schema: dict) -> None:
    conns = stores.Connections(tmp_path / "door.db")
    runtime = make_runtime(conns)
    conversation_key = ids.make_id("conv")
    seed_conversation(runtime, account="console:u1", id=conversation_key)
    run_id = seed_turn_run(conns.writer, conversation_key=conversation_key, turn_seq=0, exit_reason="completed")
    message_id = _seed_user_message(conns.writer, conversation_key=conversation_key, run_id=run_id)

    ctx = make_ctx(keys, conns, {"runs": runs})
    token = mint(keys, sub="u1", scope=["runs:read"])
    resp = handle(req("GET", f"/v1/runs/{run_id}", token=token), ctx=ctx)
    assert resp.status == 200
    row = json.loads(resp.body)
    assert row["conversation_id"] is not None
    assert row["message_id"] == message_id
    assert row["exit_reason"] == "completed"
    assert row["plugin_id"] is None
    assert row["iterations"] == 1
    assert row["duration_ms"] == pytest.approx(1500.0)
    validate(schema, row, "StandardFields")


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("budget_exhausted", "budget_exhausted"),
        ("wall_clock_exhausted", "budget_exhausted"),
        ("interrupted", "stopped"),
        ("persistence_failed", "error"),
        ("provider_failed", "error"),
        ("context_overflow_unhandled", "error"),
        ("invalid_tool_calls", "error"),
    ],
)
def test_exit_reason_mapping(keys, tmp_path: Path, raw: str, expected: str) -> None:
    conns = stores.Connections(tmp_path / "door.db")
    runtime = make_runtime(conns)
    conversation_key = ids.make_id("conv")
    seed_conversation(runtime, account="console:u1", id=conversation_key)
    run_id = seed_turn_run(conns.writer, conversation_key=conversation_key, turn_seq=0, exit_reason=raw)

    ctx = make_ctx(keys, conns, {"runs": runs})
    token = mint(keys, sub="u1", scope=["runs:read"])
    resp = handle(req("GET", f"/v1/runs/{run_id}", token=token), ctx=ctx)
    assert json.loads(resp.body)["exit_reason"] == expected


def test_completed_turn_with_a_failed_plugin_run_shows_failed_node(keys, tmp_path: Path) -> None:
    conns = stores.Connections(tmp_path / "door.db")
    runtime = make_runtime(conns)
    conversation_key = ids.make_id("conv")
    seed_conversation(runtime, account="console:u1", id=conversation_key)
    run_id = seed_turn_run(conns.writer, conversation_key=conversation_key, turn_seq=0, exit_reason="completed")
    seed_plugin_run(conns.writer, conversation_key=conversation_key, turn_seq=0, seq_in_turn=0, failed_node="step2")

    ctx = make_ctx(keys, conns, {"runs": runs})
    token = mint(keys, sub="u1", scope=["runs:read"])
    resp = handle(req("GET", f"/v1/runs/{run_id}", token=token), ctx=ctx)
    assert json.loads(resp.body)["exit_reason"] == "failed_node"


def test_stop_is_capability_missing(keys, tmp_path: Path) -> None:
    conns = stores.Connections(tmp_path / "door.db")
    runtime = make_runtime(conns)
    conversation_key = ids.make_id("conv")
    seed_conversation(runtime, account="console:u1", id=conversation_key)
    run_id = seed_turn_run(conns.writer, conversation_key=conversation_key, turn_seq=0, state="running")

    ctx = make_ctx(keys, conns, {"runs": runs})
    token = mint(keys, sub="u1", scope=["runs:stop", "runs:read"])
    resp = handle(
        req(
            "POST",
            f"/v1/runs/{run_id}/actions/stop",
            token=token,
            body=b"{}",
            headers={"If-Match": '"1"'},
        ),
        ctx=ctx,
    )
    assert resp.status == 501
