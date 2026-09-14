"""`agent_templates`' own conformance proof (H26), against `router.handle()`
directly — matching `tests/contract/test_console_grammar.py`'s own posture
and the per-noun-file precedent `test_approvals.py` set.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from sadana import stores
from sadana.door import auth
from sadana.door import capabilities as capabilities_module
from sadana.door import request as door_request
from sadana.door.nouns import agent_templates, harness
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
        nouns={"harness": harness, "agent_templates": agent_templates},
        clock=time.time,
    )
    return SimpleNamespace(ctx=ctx, private_pem=private_pem, kid="test")


def _token(door: SimpleNamespace, **overrides: object) -> str:
    defaults: dict[str, object] = dict(sub="u1", org=None, ws="ws1", hrn=_HARNESS_ID, scope=["agent_templates:read"])
    defaults.update(overrides)
    return auth.mint_token(door.private_pem, door.kid, **defaults)  # type: ignore[arg-type]


def _req(method: str, path: str, *, token: str | None, query: str = "") -> door_request.DoorRequest:
    headers: dict[str, str] = {"X-Sadana-Harness": _HARNESS_ID}
    if token is not None:
        headers["Authorization"] = f"Bearer {token}"
    return door_request.DoorRequest(method=method, path=path, query=query, headers=headers, body=b"")


def _json(resp: door_request.DoorResponse) -> dict:
    return dict(json.loads(resp.body))


@pytest.mark.contract
def test_list_returns_the_one_builtin_template(door: SimpleNamespace) -> None:
    resp = handle(_req("GET", "/v1/agent_templates", token=_token(door)), ctx=door.ctx)
    assert resp.status == 200
    data = _json(resp)["data"]
    assert [row["name"] for row in data] == ["default"]
    assert data[0]["system_prompt"]
    assert data[0]["description"]


@pytest.mark.contract
def test_get_by_id_matches_the_list_entry(door: SimpleNamespace) -> None:
    token = _token(door)
    listed = _json(handle(_req("GET", "/v1/agent_templates", token=token), ctx=door.ctx))["data"][0]

    fetched = handle(_req("GET", f"/v1/agent_templates/{listed['id']}", token=token), ctx=door.ctx)

    assert fetched.status == 200
    assert _json(fetched) == listed


@pytest.mark.contract
def test_the_id_is_stable_across_requests(door: SimpleNamespace) -> None:
    token = _token(door)
    first = _json(handle(_req("GET", "/v1/agent_templates", token=token), ctx=door.ctx))["data"][0]["id"]
    second = _json(handle(_req("GET", "/v1/agent_templates", token=token), ctx=door.ctx))["data"][0]["id"]
    assert first == second
    assert first.startswith("tmpl_")


@pytest.mark.contract
def test_unknown_id_is_404(door: SimpleNamespace) -> None:
    resp = handle(_req("GET", "/v1/agent_templates/tmpl_" + "0" * 32, token=_token(door)), ctx=door.ctx)
    assert resp.status == 404


@pytest.mark.contract
def test_create_is_capability_missing(door: SimpleNamespace) -> None:
    token = _token(door, scope=["agent_templates:read", "agent_templates:write"])
    resp = handle(
        door_request.DoorRequest(
            method="POST",
            path="/v1/agent_templates",
            query="",
            headers={"Authorization": f"Bearer {token}", "X-Sadana-Harness": _HARNESS_ID},
            body=b"{}",
        ),
        ctx=door.ctx,
    )
    assert resp.status == 501
