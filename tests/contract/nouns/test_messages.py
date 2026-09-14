"""`messages` noun contract test — spec.md requirements 8-20.

A stubbed `model_access.send` drives a real turn end to end (`client_surface.
take_turn`, `conversation_store`, `observability.py`'s own `turn_runs`
write) — only the model call itself is faked, matching this project's own
established turn-test pattern (`tests/unit/test_client_surface.py`).
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
    plain_response,
    req,
    schema,
    seed_conversation,
    validate,
)

from sadana import ids, model_access, stores
from sadana.door.nouns.conversations import ConversationsNoun
from sadana.door.nouns.messages import MessagesNoun
from sadana.door.router import handle

pytestmark = pytest.mark.contract


def _ctx(keys, tmp_path: Path):
    conns = stores.Connections(tmp_path / "door.db")
    runtime = make_runtime(conns)
    noun = MessagesNoun(runtime)
    convo = ConversationsNoun(runtime)
    ctx = make_ctx(keys, conns, {"conversations": convo, "messages": noun})
    return ctx, runtime


def test_create_succeeds_and_returns_the_user_message(keys, tmp_path: Path, monkeypatch, schema: dict) -> None:
    """H21: a streamed turn (`on_delta` genuinely called, not just accepted
    and ignored) still produces exactly one assistant row, whose final
    content equals the turn's own `final_text` — streaming changes only
    what is visible while a turn runs, never what a finished turn's answer
    is (Requirement 5)."""
    seen_deltas: list[str] = []

    def fake_send(request: object, on_delta=None) -> model_access.Response:  # type: ignore[no-untyped-def]
        if on_delta is not None:
            for fragment in ("hi ", "there"):
                seen_deltas.append(fragment)
                on_delta(fragment)
        return plain_response("hi there")

    monkeypatch.setattr(model_access, "send", fake_send)
    ctx, runtime = _ctx(keys, tmp_path)
    conversation_key = ids.make_id("conv")
    seed_conversation(runtime, account="console:u1", id=conversation_key)

    token = mint(keys, sub="u1", scope=["messages:write", "messages:read"])
    created = handle(
        req(
            "POST",
            f"/v1/conversations/{conversation_key}/messages",
            token=token,
            body=json.dumps({"content": "hello there"}).encode(),
        ),
        ctx=ctx,
    )
    assert created.status == 201
    row = json.loads(created.body)
    assert row["role"] == "user"
    assert row["content"] == "hello there"
    assert row["run_id"] is not None
    assert row["turn_seq"] == 0
    validate(schema, row, "StandardFields")

    listed = handle(req("GET", f"/v1/conversations/{conversation_key}/messages", token=token), ctx=ctx)
    data = json.loads(listed.body)["data"]
    assert len(data) == 2
    by_role = {m["role"]: m for m in data}
    assert by_role["user"]["run_id"] == by_role["assistant"]["run_id"]
    assert by_role["assistant"]["content"] == "hi there"
    assert by_role["user"]["state"] == "sent"
    assert by_role["assistant"]["state"] == "sent"
    assert seen_deltas == ["hi ", "there"]  # the fake provider's own on_delta really was called


def test_create_on_an_archived_conversation_conflicts(keys, tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(model_access, "send", lambda request: plain_response("hi"))
    ctx, runtime = _ctx(keys, tmp_path)
    conversation_key = ids.make_id("conv")
    seed_conversation(runtime, account="console:u1", id=conversation_key)

    archive_token = mint(keys, sub="u1", scope=["conversations:write", "conversations:archive", "conversations:read"])
    convo = json.loads(handle(req("GET", f"/v1/conversations/{conversation_key}", token=archive_token), ctx=ctx).body)
    archived = handle(
        req(
            "POST",
            f"/v1/conversations/{conversation_key}/actions/archive",
            token=archive_token,
            body=b"{}",
            headers={"If-Match": f'"{convo["version"]}"'},
        ),
        ctx=ctx,
    )
    assert archived.status == 200

    token = mint(keys, sub="u1", scope=["messages:write"])
    resp = handle(
        req(
            "POST",
            f"/v1/conversations/{conversation_key}/messages",
            token=token,
            body=json.dumps({"content": "hello"}).encode(),
        ),
        ctx=ctx,
    )
    assert resp.status == 409


def test_a_failed_turn_with_no_completion_still_marks_its_assistant_row_failed(
    keys, tmp_path: Path, monkeypatch
) -> None:
    """H21 supersedes the pre-H21 "no assistant row" behavior this test
    used to name and assert: the door's own observer now inserts the
    assistant's row at the very start of every turn, always — including
    one that fails before any completion ever returns — so there is
    always a row to mark `failed`, closing H20's own carried-over
    "untested failed-turn-marks-assistant-row path" finding for real."""
    monkeypatch.setattr(model_access, "send", lambda request, on_delta=None: model_access.Abort("bad request"))
    ctx, runtime = _ctx(keys, tmp_path)
    conversation_key = ids.make_id("conv")
    seed_conversation(runtime, account="console:u1", id=conversation_key)

    token = mint(keys, sub="u1", scope=["messages:write", "messages:read"])
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
    assert json.loads(resp.body)["code"] == "INTERNAL"

    listed = handle(req("GET", f"/v1/conversations/{conversation_key}/messages", token=token), ctx=ctx)
    data = json.loads(listed.body)["data"]
    assert len(data) == 2
    by_role = {m["role"]: m for m in data}
    assert by_role["user"]["state"] == "sent"
    assert by_role["assistant"]["state"] == "failed"
    assert by_role["assistant"]["content"] == ""
    assert by_role["user"]["run_id"] == by_role["assistant"]["run_id"]


def test_foreign_conversation_is_not_found(keys, tmp_path: Path) -> None:
    """H21, re-verifying H20's own carried-over finding: an unknown or
    foreign parent on `messages.list` answers `404`, not an empty `200` —
    the latter reads indistinguishably from "a real, visible conversation
    with zero messages."."""
    ctx, runtime = _ctx(keys, tmp_path)
    conversation_key = ids.make_id("conv")
    seed_conversation(runtime, account="console:owner", id=conversation_key)

    token = mint(keys, sub="stranger", scope=["messages:read"])
    resp = handle(req("GET", f"/v1/conversations/{conversation_key}/messages", token=token), ctx=ctx)
    assert resp.status == 404
    assert json.loads(resp.body)["code"] == "NOT_FOUND"


def test_a_child_route_under_the_wrong_parent_plural_is_not_found(keys, tmp_path: Path) -> None:
    """`messages.spec.parent == "conversations"` — `/v1/harness/{id}/messages`
    must not dispatch to `messages` just because the shape parses the same
    way `/v1/conversations/{id}/messages` does (router.py's `parent_plural`
    check, added during the build-skill self-check)."""
    from sadana.door.nouns import harness

    ctx, runtime = _ctx(keys, tmp_path)
    ctx = make_ctx(keys, ctx.conns, {"harness": harness, "messages": ctx.nouns["messages"]})
    conversation_key = ids.make_id("conv")
    seed_conversation(runtime, account="console:u1", id=conversation_key)

    token = mint(keys, sub="u1", scope=["messages:read"])
    resp = handle(req("GET", f"/v1/harness/{conversation_key}/messages", token=token), ctx=ctx)
    assert resp.status == 404
