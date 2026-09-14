"""`traces` noun contract test — spec.md requirements 24-25.

Also the first proof that `router.py`'s new child-nesting routes actually
work end to end (plan.md's router.py change, approved during implementation).
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
from sadana.door.nouns import runs, traces
from sadana.door.router import handle

pytestmark = pytest.mark.contract


def _seed(keys, tmp_path: Path):
    conns = stores.Connections(tmp_path / "door.db")
    runtime = make_runtime(conns)
    conversation_key = ids.make_id("conv")
    seed_conversation(runtime, account="console:u1", id=conversation_key)
    run_id = seed_turn_run(conns.writer, conversation_key=conversation_key, turn_seq=0)
    trace_id = seed_plugin_run(
        conns.writer,
        conversation_key=conversation_key,
        turn_seq=0,
        seq_in_turn=0,
        plugin="greeter",
        entry="start",
        node_count=3,
    )
    ctx = make_ctx(keys, conns, {"runs": runs, "traces": traces})
    return ctx, run_id, trace_id


def test_list_under_the_parent_run(keys, tmp_path: Path, schema: dict) -> None:
    ctx, run_id, trace_id = _seed(keys, tmp_path)
    token = mint(keys, sub="u1", scope=["traces:read"])
    resp = handle(req("GET", f"/v1/runs/{run_id}/traces", token=token), ctx=ctx)
    assert resp.status == 200
    body = json.loads(resp.body)
    validate(schema, body, "ListResponse")
    data = body["data"]
    assert len(data) == 1
    assert data[0]["id"] == trace_id
    assert data[0]["root_node"] == "start"
    assert data[0]["entry"] == "start"
    assert data[0]["node_count"] == 3
    assert data[0]["state"] == "closed"


def test_get_under_the_parent_run(keys, tmp_path: Path) -> None:
    ctx, run_id, trace_id = _seed(keys, tmp_path)
    token = mint(keys, sub="u1", scope=["traces:read"])
    resp = handle(req("GET", f"/v1/runs/{run_id}/traces/{trace_id}", token=token), ctx=ctx)
    assert resp.status == 200
    assert json.loads(resp.body)["id"] == trace_id


def test_a_failed_node_renders_failed_state(keys, tmp_path: Path) -> None:
    conns = stores.Connections(tmp_path / "door.db")
    runtime = make_runtime(conns)
    conversation_key = ids.make_id("conv")
    seed_conversation(runtime, account="console:u1", id=conversation_key)
    run_id = seed_turn_run(conns.writer, conversation_key=conversation_key, turn_seq=0)
    seed_plugin_run(conns.writer, conversation_key=conversation_key, turn_seq=0, seq_in_turn=0, failed_node="step2")
    ctx = make_ctx(keys, conns, {"runs": runs, "traces": traces})

    token = mint(keys, sub="u1", scope=["traces:read"])
    resp = handle(req("GET", f"/v1/runs/{run_id}/traces", token=token), ctx=ctx)
    row = json.loads(resp.body)["data"][0]
    assert row["state"] == "failed"
    assert row["failed_node"] == "step2"


def test_foreign_account_sees_no_traces(keys, tmp_path: Path) -> None:
    ctx, run_id, _ = _seed(keys, tmp_path)
    token = mint(keys, sub="stranger", scope=["traces:read"])
    resp = handle(req("GET", f"/v1/runs/{run_id}/traces", token=token), ctx=ctx)
    assert json.loads(resp.body)["data"] == []


def test_bare_traces_path_is_empty(keys, tmp_path: Path) -> None:
    ctx, _run_id, _trace_id = _seed(keys, tmp_path)
    token = mint(keys, sub="u1", scope=["traces:read"])
    resp = handle(req("GET", "/v1/traces", token=token), ctx=ctx)
    assert resp.status == 200
    assert json.loads(resp.body)["data"] == []
