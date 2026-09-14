"""`conversations` noun contract test — spec.md requirements 1-7."""

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
from _scaffold import keys, make_ctx, make_runtime, mint, req, schema, validate

from sadana import stores
from sadana.door.nouns.conversations import ConversationsNoun
from sadana.door.router import handle

pytestmark = pytest.mark.contract


def _ctx(keys, tmp_path: Path):
    conns = stores.Connections(tmp_path / "door.db")
    runtime = make_runtime(conns)
    noun = ConversationsNoun(runtime)
    return make_ctx(keys, conns, {"conversations": noun})


def test_create_then_list_shows_one_with_zero_messages(keys, tmp_path: Path, schema: dict) -> None:
    ctx = _ctx(keys, tmp_path)
    token = mint(keys, sub="u1", scope=["conversations:read", "conversations:write"])

    empty = handle(req("GET", "/v1/conversations", token=token), ctx=ctx)
    assert json.loads(empty.body)["data"] == []

    created = handle(
        req("POST", "/v1/conversations", token=token, body=json.dumps({"name": "trip planning"}).encode()), ctx=ctx
    )
    assert created.status == 201
    row = json.loads(created.body)
    assert row["name"] == "trip planning"
    assert row["id"].startswith("conv_")
    assert row["message_count"] == 0
    assert row["state"] == "active"
    validate(schema, row, "StandardFields")

    listed = handle(req("GET", "/v1/conversations", token=token), ctx=ctx)
    data = json.loads(listed.body)["data"]
    assert len(data) == 1
    assert data[0]["id"] == row["id"]


def test_create_defaults_name_to_the_minted_id(keys, tmp_path: Path) -> None:
    ctx = _ctx(keys, tmp_path)
    token = mint(keys, sub="u1", scope=["conversations:write"])
    created = handle(req("POST", "/v1/conversations", token=token, body=b"{}"), ctx=ctx)
    row = json.loads(created.body)
    assert row["name"] == row["id"]


def test_rename_requires_a_fresh_if_match(keys, tmp_path: Path) -> None:
    ctx = _ctx(keys, tmp_path)
    token = mint(keys, sub="u1", scope=["conversations:write", "conversations:rename", "conversations:read"])
    created = json.loads(handle(req("POST", "/v1/conversations", token=token, body=b"{}"), ctx=ctx).body)
    cid = created["id"]

    stale = handle(
        req(
            "POST",
            f"/v1/conversations/{cid}/actions/rename",
            token=token,
            body=json.dumps({"name": "new name"}).encode(),
            headers={"If-Match": '"99"'},
        ),
        ctx=ctx,
    )
    assert stale.status == 412

    fresh = handle(
        req(
            "POST",
            f"/v1/conversations/{cid}/actions/rename",
            token=token,
            body=json.dumps({"name": "new name"}).encode(),
            headers={"If-Match": f'"{created["version"]}"'},
        ),
        ctx=ctx,
    )
    assert fresh.status == 200
    updated = json.loads(fresh.body)
    assert updated["name"] == "new name"
    assert updated["version"] == created["version"] + 1


def test_archive_then_unarchive_then_double_archive_conflicts(keys, tmp_path: Path) -> None:
    ctx = _ctx(keys, tmp_path)
    token = mint(
        keys,
        sub="u1",
        scope=["conversations:write", "conversations:archive", "conversations:unarchive", "conversations:read"],
    )
    created = json.loads(handle(req("POST", "/v1/conversations", token=token, body=b"{}"), ctx=ctx).body)
    cid, version = created["id"], created["version"]

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
    assert json.loads(archived.body)["state"] == "archived"
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

    unarchived = handle(
        req(
            "POST",
            f"/v1/conversations/{cid}/actions/unarchive",
            token=token,
            body=b"{}",
            headers={"If-Match": f'"{version}"'},
        ),
        ctx=ctx,
    )
    assert unarchived.status == 200
    assert json.loads(unarchived.body)["state"] == "active"


def test_another_accounts_conversation_is_not_found(keys, tmp_path: Path) -> None:
    ctx = _ctx(keys, tmp_path)
    owner = mint(keys, sub="owner", scope=["conversations:write"])
    created = json.loads(handle(req("POST", "/v1/conversations", token=owner, body=b"{}"), ctx=ctx).body)

    stranger = mint(keys, sub="stranger", scope=["conversations:read"])
    resp = handle(req("GET", f"/v1/conversations/{created['id']}", token=stranger), ctx=ctx)
    assert resp.status == 404


def test_remove_is_capability_missing(keys, tmp_path: Path) -> None:
    ctx = _ctx(keys, tmp_path)
    token = mint(keys, sub="u1", scope=["conversations:write"])
    created = json.loads(handle(req("POST", "/v1/conversations", token=token, body=b"{}"), ctx=ctx).body)
    resp = handle(req("DELETE", f"/v1/conversations/{created['id']}", token=token), ctx=ctx)
    assert resp.status == 501
