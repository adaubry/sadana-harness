"""`memory_entries`' own conformance proof (H26), against `router.handle()`
directly — matching `tests/contract/test_console_grammar.py`'s own posture
and the per-noun-file precedent `test_approvals.py` set.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from sadana import memory_store, stores
from sadana.door import auth
from sadana.door import capabilities as capabilities_module
from sadana.door import request as door_request
from sadana.door.nouns import harness, memory_entries
from sadana.door.router import DoorContext, handle

_HARNESS_ID = "hrn_test"


@pytest.fixture
def door(tmp_path: Path) -> SimpleNamespace:
    private_pem, jwk = auth.generate_dev_keypair("test")
    jwks_path = tmp_path / "jwks.json"
    jwks_path.write_text(json.dumps(auth.jwks_document(jwk)))
    verifier = auth.Verifier(auth.JwksSource(path=jwks_path))
    conns = stores.Connections(tmp_path / "door.db")
    ctx = DoorContext(
        conns=conns,
        runtime=auth.BoxIdentity(harness_id=_HARNESS_ID, org=None),
        verifier=verifier,
        capabilities=capabilities_module.declared(),
        nouns={"harness": harness, "memory_entries": memory_entries},
        clock=time.time,
    )
    return SimpleNamespace(ctx=ctx, private_pem=private_pem, kid="test")


def _token(door: SimpleNamespace, **overrides: object) -> str:
    defaults: dict[str, object] = dict(
        sub="u1", org=None, ws="ws1", hrn=_HARNESS_ID, scope=["memory_entries:read", "memory_entries:forget"]
    )
    defaults.update(overrides)
    return auth.mint_token(door.private_pem, door.kid, **defaults)  # type: ignore[arg-type]


def _req(
    method: str, path: str, *, token: str | None, body: bytes = b"", headers: dict | None = None
) -> door_request.DoorRequest:
    hdrs: dict[str, str] = dict(headers or {})
    if token is not None:
        hdrs["Authorization"] = f"Bearer {token}"
    hdrs.setdefault("X-Sadana-Harness", _HARNESS_ID)
    return door_request.DoorRequest(method=method, path=path, query="", headers=hdrs, body=body)


def _json(resp: door_request.DoorResponse) -> dict:
    return dict(json.loads(resp.body))


def _write(door: SimpleNamespace, account: str, entry_key: str, content: str, **kwargs: object) -> str:
    memory_store.write_entry(door.ctx.conns.writer, account, entry_key, content, now=1.0, **kwargs)  # type: ignore[arg-type]
    return memory_store.list_entries_full(door.ctx.conns.reader(), account)[0].id


@pytest.mark.contract
def test_list_is_scoped_to_the_acting_account(door: SimpleNamespace) -> None:
    _write(door, "console:u1", "dog_name", "Buddy")
    _write(door, "console:other", "dog_name", "Rex")

    resp = handle(_req("GET", "/v1/memory_entries", token=_token(door)), ctx=door.ctx)
    data = _json(resp)["data"]

    assert [row["content"] for row in data] == ["Buddy"]


@pytest.mark.contract
def test_legacy_row_renders_default_kind_and_source(door: SimpleNamespace) -> None:
    entry_id = _write(door, "console:u1", "dog_name", "Buddy")  # no kind/source given → NULL columns

    resp = handle(_req("GET", f"/v1/memory_entries/{entry_id}", token=_token(door)), ctx=door.ctx)
    body = _json(resp)

    assert (body["kind"], body["source"]) == ("fact", "conversation")


@pytest.mark.contract
def test_recorded_kind_and_conversation_render_through(door: SimpleNamespace) -> None:
    entry_id = _write(door, "console:u1", "tz", "UTC+2", kind="preference", conversation_key="conv_1")

    body = _json(handle(_req("GET", f"/v1/memory_entries/{entry_id}", token=_token(door)), ctx=door.ctx))

    assert (body["kind"], body["conversation_id"]) == ("preference", "conv_1")


@pytest.mark.contract
def test_forget_moves_state_and_hides_it_from_recall_but_not_from_the_door(door: SimpleNamespace) -> None:
    entry_id = _write(door, "console:u1", "dog_name", "Buddy")

    resp = handle(
        _req(
            "POST",
            f"/v1/memory_entries/{entry_id}/actions/forget",
            token=_token(door),
            headers={"If-Match": '"1"'},
        ),
        ctx=door.ctx,
    )
    assert resp.status == 200
    assert _json(resp)["state"] == "forgotten"

    reader = door.ctx.conns.reader()
    assert memory_store.list_entries(reader, "console:u1") == ()  # what memory.retrieve reads
    assert [r.state for r in memory_store.list_entries_full(reader, "console:u1")] == ["forgotten"]

    listed = _json(handle(_req("GET", "/v1/memory_entries", token=_token(door)), ctx=door.ctx))["data"]
    assert [row["id"] for row in listed] == [entry_id]


@pytest.mark.contract
def test_forget_requires_the_kept_state(door: SimpleNamespace) -> None:
    entry_id = _write(door, "console:u1", "dog_name", "Buddy")
    token = _token(door)
    handle(
        _req("POST", f"/v1/memory_entries/{entry_id}/actions/forget", token=token, headers={"If-Match": '"1"'}),
        ctx=door.ctx,
    )

    conflict = handle(
        _req("POST", f"/v1/memory_entries/{entry_id}/actions/forget", token=token, headers={"If-Match": '"2"'}),
        ctx=door.ctx,
    )
    assert conflict.status == 409


@pytest.mark.contract
def test_remove_purges_the_row(door: SimpleNamespace) -> None:
    entry_id = _write(door, "console:u1", "dog_name", "Buddy")
    token = _token(door, scope=["memory_entries:read", "memory_entries:write"])

    resp = handle(
        _req("DELETE", f"/v1/memory_entries/{entry_id}", token=token, headers={"If-Match": '"1"'}), ctx=door.ctx
    )
    assert resp.status == 204
    assert memory_store.list_entries_full(door.ctx.conns.reader(), "console:u1") == ()


@pytest.mark.contract
def test_another_accounts_entry_is_404_on_get_and_act(door: SimpleNamespace) -> None:
    entry_id = _write(door, "console:other", "dog_name", "Rex")
    token = _token(door)

    get_resp = handle(_req("GET", f"/v1/memory_entries/{entry_id}", token=token), ctx=door.ctx)
    assert get_resp.status == 404

    act_resp = handle(
        _req(
            "POST",
            f"/v1/memory_entries/{entry_id}/actions/forget",
            token=token,
            headers={"If-Match": '"1"'},
        ),
        ctx=door.ctx,
    )
    assert act_resp.status == 404


@pytest.mark.contract
def test_create_is_capability_missing(door: SimpleNamespace) -> None:
    token = _token(door, scope=["memory_entries:read", "memory_entries:write"])
    resp = handle(_req("POST", "/v1/memory_entries", token=token, body=b"{}"), ctx=door.ctx)
    assert resp.status == 501
