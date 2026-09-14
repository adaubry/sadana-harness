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

from sadana import client_surface, ids, model_access, observability, plugin_dispatch, stores
from sadana.conversation import ToolSpec
from sadana.conversation_store import write_txn
from sadana.door.nouns import runs
from sadana.door.nouns.conversations import ConversationsNoun
from sadana.door.nouns.messages import MessagesNoun
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


def test_stop_on_a_run_registered_mid_turn_stops_it_and_settles_to_stopped(
    keys, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """H21. `runs.stop` is exercised for real — not `capabilities.DECLARED`
    yet (step 15), so this test states `runs.stop` as its own
    `extra_capabilities`, the same mechanism every other noun's own
    capability-gated test already uses before its capability is wired in.
    A fake provider stops the run *from inside its own first response* —
    the same shape a genuinely concurrent stop request would have, since
    this test has no second thread to send one from — then returns a tool
    call so the turn's own `should_stop()` check (after the tool round,
    before a second model call) is what actually ends it; the fake
    provider asserts it is never called a second time, proving the stop
    was honored rather than merely recorded."""
    conns = stores.Connections(tmp_path / "door.db")
    # A real (if unregistered) tool, not `make_runtime`'s own
    # `EMPTY_PLUGIN_SET`: an empty tool surface makes any tool call
    # `invalid_tool_calls` before the turn loop ever reaches its
    # `should_stop()` check — this test needs a *valid, unregistered* call
    # (a clean, dispatch-level "no installed plugin" `DagResult`) so the
    # loop reaches the tool round and, after it, `should_stop()`.
    plugin_set = plugin_dispatch.PluginSet(
        catalog=(),
        tool_specs=(ToolSpec(key="nonexistent_tool", name="nonexistent_tool", parameters={}, describe=lambda _r: ""),),
        by_tool={},
    )
    runtime = client_surface.Runtime(
        connections=conns,
        plugin_set=plugin_set,
        provider="p",
        model="m",
        recorder=observability.make_recorder(conns.writer),
    )
    conversation_key = ids.make_id("conv")
    seed_conversation(runtime, account="console:u1", id=conversation_key)
    ctx = make_ctx(
        keys,
        conns,
        {"conversations": ConversationsNoun(runtime), "messages": MessagesNoun(runtime), "runs": runs},
        extra_capabilities=("runs.stop",),
    )
    stop_token = mint(keys, sub="u1", scope=["runs:stop"])
    seen_run_id: dict[str, str] = {}
    calls = {"n": 0}

    def fake_send(request: object, on_delta=None) -> model_access.Response:  # type: ignore[no-untyped-def]
        calls["n"] += 1
        if calls["n"] > 1:
            raise AssertionError("should_stop() should have ended the turn before a second model call")
        row = (
            conns.reader()
            .execute("SELECT id FROM turn_runs WHERE conversation_key = ? AND state = 'running'", (conversation_key,))
            .fetchone()
        )
        assert row is not None
        seen_run_id["id"] = row["id"]
        stop_resp = handle(req("POST", f"/v1/runs/{row['id']}/actions/stop", token=stop_token, body=b"{}"), ctx=ctx)
        assert stop_resp.status == 200
        return model_access.Response(
            content=None,
            tool_calls=({"function": {"name": "nonexistent_tool", "arguments": "{}"}},),
            finish_reason="tool_calls",
            usage=model_access.Usage(),
        )

    monkeypatch.setattr(model_access, "send", fake_send)

    msg_token = mint(keys, sub="u1", scope=["messages:write", "messages:read"])
    created = handle(
        req(
            "POST",
            f"/v1/conversations/{conversation_key}/messages",
            token=msg_token,
            body=json.dumps({"content": "hi"}).encode(),
        ),
        ctx=ctx,
    )
    assert created.status == 500  # INTERRUPTED is not `ok`, same as any other non-completed turn

    read_token = mint(keys, sub="u1", scope=["runs:read"])
    settled = handle(req("GET", f"/v1/runs/{seen_run_id['id']}", token=read_token), ctx=ctx)
    assert json.loads(settled.body)["exit_reason"] == "stopped"


def test_stop_on_a_run_not_held_by_this_process_answers_conflict(keys, tmp_path: Path) -> None:
    """A `turn_runs` row seeded straight into the DB, never registered with
    `run_control` — the "the process restarted since the run started"
    case `door/nouns/runs.py`'s own docstring names."""
    conns = stores.Connections(tmp_path / "door.db")
    runtime = make_runtime(conns)
    conversation_key = ids.make_id("conv")
    seed_conversation(runtime, account="console:u1", id=conversation_key)
    run_id = seed_turn_run(conns.writer, conversation_key=conversation_key, turn_seq=0, state="running")

    ctx = make_ctx(keys, conns, {"runs": runs}, extra_capabilities=("runs.stop",))
    token = mint(keys, sub="u1", scope=["runs:stop"])
    resp = handle(
        req("POST", f"/v1/runs/{run_id}/actions/stop", token=token, body=b"{}"),
        ctx=ctx,
    )
    assert resp.status == 409


def test_stop_on_an_already_finished_run_answers_conflict(keys, tmp_path: Path) -> None:
    conns = stores.Connections(tmp_path / "door.db")
    runtime = make_runtime(conns)
    conversation_key = ids.make_id("conv")
    seed_conversation(runtime, account="console:u1", id=conversation_key)
    run_id = seed_turn_run(conns.writer, conversation_key=conversation_key, turn_seq=0, state="done")

    ctx = make_ctx(keys, conns, {"runs": runs}, extra_capabilities=("runs.stop",))
    token = mint(keys, sub="u1", scope=["runs:stop"])
    resp = handle(
        req("POST", f"/v1/runs/{run_id}/actions/stop", token=token, body=b"{}"),
        ctx=ctx,
    )
    assert resp.status == 409
