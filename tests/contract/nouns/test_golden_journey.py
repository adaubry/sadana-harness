"""The cross-noun golden journey — spec.md's Acceptance criteria, worked as
one scenario through `handle()` with a stubbed `model_access.send`.

Builds its own seven-noun `DoorContext` (`harness` plus this artifact's six),
never importing `test_console_grammar.py`'s `harness`+`widgets`-only fixture
(spec.md § Design, "Reusing H19's test scaffolding without editing it").
"""

# ruff: noqa: F401, F811 -- `keys`/`schema` are pytest fixtures imported from
# `_scaffold.py` (not a `conftest.py` -- see that file's own docstring) and
# used by name as a parameter in every test function below; ruff's static
# analysis reads each repeated parameter as redefining an "unused" import,
# which is exactly the documented pytest cross-module-fixture idiom, not a
# real redefinition.

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest
from _scaffold import (
    keys,
    make_ctx,
    make_runtime,
    mint,
    plain_response,
    req,
    schema,
    seed_artifact,
    seed_conversation,
    validate,
)

from sadana import ids, model_access, stores
from sadana.door import router as router_module
from sadana.door.nouns import artifacts as artifacts_module
from sadana.door.nouns import harness, runs, spans, traces
from sadana.door.nouns.conversations import ConversationsNoun
from sadana.door.nouns.messages import MessagesNoun
from sadana.door.router import handle

pytestmark = pytest.mark.contract


def _full_ctx(keys, tmp_path: Path):
    conns = stores.Connections(tmp_path / "door.db")
    runtime = make_runtime(conns)
    nouns = {
        "harness": harness,
        "conversations": ConversationsNoun(runtime),
        "messages": MessagesNoun(runtime),
        "runs": runs,
        "traces": traces,
        "spans": spans,
        "artifacts": artifacts_module,
    }
    return make_ctx(keys, conns, nouns, extra_capabilities=("artifacts.download",)), runtime


def test_golden_journey(keys, tmp_path: Path, monkeypatch, schema: dict) -> None:
    def _slow_reply(request: object) -> model_access.Response:
        time.sleep(0.05)
        return plain_response("hi there")

    monkeypatch.setattr(model_access, "send", _slow_reply)

    ctx, _runtime = _full_ctx(keys, tmp_path)
    token = mint(
        keys,
        sub="u1",
        scope=[
            "conversations:read",
            "conversations:write",
            "conversations:archive",
            "conversations:rename",
            "messages:read",
            "messages:write",
            "runs:read",
        ],
    )

    # list (empty)
    empty = handle(req("GET", "/v1/conversations", token=token), ctx=ctx)
    assert json.loads(empty.body)["data"] == []

    # create
    created = handle(req("POST", "/v1/conversations", token=token, body=b"{}"), ctx=ctx)
    assert created.status == 201
    convo = json.loads(created.body)
    cid = convo["id"]

    # list (one, message_count 0)
    listed = handle(req("GET", "/v1/conversations", token=token), ctx=ctx)
    data = json.loads(listed.body)["data"]
    assert len(data) == 1
    assert data[0]["message_count"] == 0

    # create message -> operation running. Timeout lowered only for this one
    # call (conversation creation above does real disk I/O too and would
    # otherwise also cross a globally-lowered bound) -- proves the promoted
    # (202) path, not just the fast one.
    monkeypatch.setattr(router_module, "_OPERATION_TIMEOUT_SECONDS", 0.01)
    create_msg = handle(
        req(
            "POST",
            f"/v1/conversations/{cid}/messages",
            token=token,
            body=json.dumps({"content": "hello"}).encode(),
        ),
        ctx=ctx,
    )
    monkeypatch.setattr(router_module, "_OPERATION_TIMEOUT_SECONDS", 2.0)
    assert create_msg.status == 202
    op = json.loads(create_msg.body)["operation"]
    assert op["state"] == "running"
    op_id = op["id"]

    # wait -> operation succeeded, resource asserted None explicitly
    deadline = time.monotonic() + 5.0
    while True:
        polled = handle(req("GET", f"/v1/operations/{op_id}", token=token), ctx=ctx)
        op = json.loads(polled.body)["operation"]
        if op["state"] != "running":
            break
        assert time.monotonic() < deadline, "operation never settled"
        time.sleep(0.01)
    assert op["state"] == "succeeded"
    assert op["resource"] is None
    validate(schema, op, "Operation")

    # messages list shows user and assistant in order, both run_id-tagged
    # (the framework's own default order is `created_at desc` — message.md's
    # own documented deviation, spec.md requirement 20 — so a chronological
    # transcript asks for `asc` explicitly, same as a real console client
    # would).
    messages = json.loads(
        handle(
            req("GET", f"/v1/conversations/{cid}/messages", token=token, query="order_by=created_at asc"), ctx=ctx
        ).body
    )["data"]
    assert [m["role"] for m in messages] == ["user", "assistant"]
    assert messages[0]["run_id"] is not None
    assert messages[0]["run_id"] == messages[1]["run_id"]

    # run done with message_id = the user message
    run_list = json.loads(handle(req("GET", "/v1/runs", token=token), ctx=ctx).body)["data"]
    assert len(run_list) == 1
    assert run_list[0]["state"] == "done"
    assert run_list[0]["message_id"] == messages[0]["id"]

    # rename: stale If-Match -> 412, correct -> 200
    stale = handle(
        req(
            "POST",
            f"/v1/conversations/{cid}/actions/rename",
            token=token,
            body=json.dumps({"name": "trip"}).encode(),
            headers={"If-Match": '"999"'},
        ),
        ctx=ctx,
    )
    assert stale.status == 412
    current = json.loads(handle(req("GET", f"/v1/conversations/{cid}", token=token), ctx=ctx).body)
    renamed = handle(
        req(
            "POST",
            f"/v1/conversations/{cid}/actions/rename",
            token=token,
            body=json.dumps({"name": "trip"}).encode(),
            headers={"If-Match": f'"{current["version"]}"'},
        ),
        ctx=ctx,
    )
    assert renamed.status == 200
    assert json.loads(renamed.body)["name"] == "trip"
    version = json.loads(renamed.body)["version"]

    # archive -> second archive is 409
    archived = handle(
        req(
            "POST",
            f"/v1/conversations/{cid}/actions/archive",
            token=token,
            body=b"{}",
            headers={"If-Match": f'"{version}"'},
        ),
        ctx=ctx,
    )
    assert archived.status == 200
    version = json.loads(archived.body)["version"]
    second_archive = handle(
        req(
            "POST",
            f"/v1/conversations/{cid}/actions/archive",
            token=token,
            body=b"{}",
            headers={"If-Match": f'"{version}"'},
        ),
        ctx=ctx,
    )
    assert second_archive.status == 409

    # another account's token -> 404
    stranger = mint(keys, sub="stranger", scope=["conversations:read"])
    assert handle(req("GET", f"/v1/conversations/{cid}", token=stranger), ctx=ctx).status == 404

    # a message on an archived conversation -> 409
    on_archived = handle(
        req(
            "POST",
            f"/v1/conversations/{cid}/messages",
            token=token,
            body=json.dumps({"content": "hi"}).encode(),
        ),
        ctx=ctx,
    )
    assert on_archived.status == 409


def test_golden_journey_artifact_guard_refusals(keys, tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("SADANA_STATE_DIR", str(tmp_path / "state"))
    ctx, runtime = _full_ctx(keys, tmp_path)
    conversation_key = ids.make_id("conv")
    seed_conversation(runtime, account="console:u1", id=conversation_key)
    token = mint(keys, sub="u1", scope=["artifacts:download"])

    for bad_ref in ("../escape.txt", "/etc/passwd", "sub/../../escape.txt"):
        artifact_id = seed_artifact(
            runtime.connections.writer,
            conversation_key=conversation_key,
            turn_seq=0,
            seq_in_turn=0,
            name="output.txt",
            ref=bad_ref,
        )
        resp = handle(
            req(
                "POST",
                f"/v1/artifacts/{artifact_id}/actions/download",
                token=token,
                body=b"{}",
                headers={"If-Match": '"1"'},
            ),
            ctx=ctx,
        )
        assert resp.status == 400
        assert json.loads(resp.body)["detail"] == "artifact path escapes its run directory"


def test_golden_journey_failed_turn_carries_the_diagnostic(keys, tmp_path: Path, monkeypatch) -> None:
    ctx, runtime = _full_ctx(keys, tmp_path)
    conversation_key = ids.make_id("conv")
    seed_conversation(runtime, account="console:u1", id=conversation_key)
    token = mint(keys, sub="u1", scope=["messages:write", "messages:read"])

    monkeypatch.setattr(model_access, "send", lambda request: model_access.Abort("bad request"))
    resp = handle(
        req(
            "POST",
            f"/v1/conversations/{conversation_key}/messages",
            token=token,
            body=json.dumps({"content": "hello"}).encode(),
        ),
        ctx=ctx,
    )
    assert resp.status == 500
    body = json.loads(resp.body)
    assert body["code"] == "INTERNAL"
    assert "provider_failed" in body["detail"]
