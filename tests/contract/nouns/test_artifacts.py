"""`artifacts` noun contract test — spec.md requirements 27-31.

Also covers `router.py`'s one new `DoorResponse` pass-through in isolation,
with a throwaway fixture noun, before trusting `artifacts.py` itself to
exercise it (plan.md step 3).
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
    seed_artifact,
    seed_conversation,
    seed_plugin_run,
    validate,
)

from sadana import ids, stores
from sadana.door.nouns import ActionSpec, NounSpec, SearchDoc, artifacts
from sadana.door.request import DoorResponse
from sadana.door.router import handle

pytestmark = pytest.mark.contract


class _EchoDoorResponseNoun:
    """A minimal noun whose only action returns a raw `DoorResponse` — proves
    `router.py`'s pass-through works before `artifacts.py` exists to prove it
    for real (plan.md step 3)."""

    spec = NounSpec(
        plural="echoes",
        prefix="ech",
        filterable=frozenset(),
        orderable=frozenset({"created_at"}),
        states=frozenset({"ready"}),
        actions={"echo": ActionSpec(from_states=("ready",), to_state="ready", capability=None, scope_verb="echo")},
    )

    def list(self, ctx, principal, params, parent_id=None):
        raise AssertionError("not exercised")

    def get(self, ctx, principal, id, parent_id=None):
        return {"id": id, "state": "ready", "version": 1}

    def create(self, ctx, principal, body, parent_id=None):
        raise AssertionError("not exercised")

    def update(self, ctx, principal, id, body, if_match):
        raise AssertionError("not exercised")

    def remove(self, ctx, principal, id, if_match):
        raise AssertionError("not exercised")

    def act(self, ctx, principal, id, name, body, if_match):
        return DoorResponse(status=200, headers={"Content-Type": "text/plain"}, body=b"echoed")

    def search_doc(self, row):
        return SearchDoc(title="echo")


def test_router_passes_a_door_response_through_unmodified(keys, tmp_path: Path) -> None:
    conns = stores.Connections(tmp_path / "door.db")
    ctx = make_ctx(keys, conns, {"echoes": _EchoDoorResponseNoun()})
    token = mint(keys, scope=["echoes:echo"])
    resp = handle(
        req("POST", "/v1/echoes/ech_1/actions/echo", token=token, body=b"{}", headers={"If-Match": '"1"'}), ctx=ctx
    )
    assert resp.status == 200
    assert resp.headers["Content-Type"] == "text/plain"
    assert resp.body == b"echoed"


def _seed(keys, tmp_path: Path, monkeypatch, *, kind: str = "file", ref: str = "output.txt", content: bytes = b"hi"):
    monkeypatch.setenv("SADANA_STATE_DIR", str(tmp_path / "state"))
    conns = stores.Connections(tmp_path / "door.db")
    runtime = make_runtime(conns)
    conversation_key = ids.make_id("conv")
    # `door.auth.account_key_for` renders a token's `sub` as `"console:" +
    # sub` — the seeded row must be owned by that same namespaced value, not
    # the bare `sub` a token carries.
    seed_conversation(runtime, account="console:acct-1", id=conversation_key)
    plugin_run_id = seed_plugin_run(conns.writer, conversation_key=conversation_key, turn_seq=0, seq_in_turn=0)
    artifact_id = seed_artifact(
        conns.writer,
        conversation_key=conversation_key,
        turn_seq=0,
        seq_in_turn=0,
        name="output.txt",
        kind=kind,
        ref=ref,
    )
    if kind == "file":
        from sadana import artifact_store

        run_dir = artifact_store.for_run(conversation_key, 0, 0)
        run_dir.mkdir(parents=True, exist_ok=True)
        (run_dir / "output.txt").write_bytes(content)
    ctx = make_ctx(keys, conns, {"artifacts": artifacts}, extra_capabilities=("artifacts.download",))
    return ctx, artifact_id, plugin_run_id


def test_list_scopes_to_the_owning_account(keys, tmp_path: Path, monkeypatch, schema: dict) -> None:
    ctx, artifact_id, plugin_run_id = _seed(keys, tmp_path, monkeypatch)
    token = mint(keys, sub="acct-1", scope=["artifacts:read"])
    resp = handle(req("GET", "/v1/artifacts", token=token), ctx=ctx)
    assert resp.status == 200
    body = json.loads(resp.body)
    validate(schema, body, "ListResponse")
    assert len(body["data"]) == 1
    row = body["data"][0]
    assert row["id"] == artifact_id
    assert row["run_id"] == plugin_run_id
    assert row["filename"] == "output.txt"
    assert row["kind"] == "file"
    assert row["url"] is None

    other_token = mint(keys, sub="acct-2", scope=["artifacts:read"])
    other_resp = handle(req("GET", "/v1/artifacts", token=other_token), ctx=ctx)
    assert json.loads(other_resp.body)["data"] == []


def test_get_foreign_account_is_not_found(keys, tmp_path: Path, monkeypatch) -> None:
    ctx, artifact_id, _ = _seed(keys, tmp_path, monkeypatch)
    other_token = mint(keys, sub="acct-2", scope=["artifacts:read"])
    resp = handle(req("GET", f"/v1/artifacts/{artifact_id}", token=other_token), ctx=ctx)
    assert resp.status == 404


def test_create_is_capability_missing(keys, tmp_path: Path, monkeypatch) -> None:
    ctx, _, _ = _seed(keys, tmp_path, monkeypatch)
    token = mint(keys, sub="acct-1", scope=["artifacts:write"])
    resp = handle(req("POST", "/v1/artifacts", token=token, body=b"{}"), ctx=ctx)
    assert resp.status == 501


def test_download_streams_the_real_file(keys, tmp_path: Path, monkeypatch) -> None:
    ctx, artifact_id, _ = _seed(keys, tmp_path, monkeypatch, content=b"hello world")
    token = mint(keys, sub="acct-1", scope=["artifacts:download"])
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
    assert resp.status == 200
    assert resp.body == b"hello world"
    assert resp.headers["Content-Type"] == "text/plain"
    assert resp.headers["Content-Length"] == "11"


@pytest.mark.parametrize(
    "bad_ref", ["../escape.txt", "/etc/passwd", "sub/../../escape.txt"], ids=["dotdot", "absolute", "resolved-out"]
)
def test_download_refuses_a_path_escaping_the_run_directory(keys, tmp_path: Path, monkeypatch, bad_ref: str) -> None:
    ctx, artifact_id, _ = _seed(keys, tmp_path, monkeypatch, ref=bad_ref)
    token = mint(keys, sub="acct-1", scope=["artifacts:download"])
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
    body = json.loads(resp.body)
    assert body["detail"] == "artifact path escapes its run directory"
