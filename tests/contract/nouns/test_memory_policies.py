"""`memory_policies`' own conformance proof (H26), against `router.handle()`
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
from sadana.door.nouns import harness, memory_policies
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
        nouns={"harness": harness, "memory_policies": memory_policies},
        clock=time.time,
    )
    return SimpleNamespace(ctx=ctx, private_pem=private_pem, kid="test")


def _token(door: SimpleNamespace, **overrides: object) -> str:
    defaults: dict[str, object] = dict(
        sub="u1", org=None, ws="ws1", hrn=_HARNESS_ID, scope=["memory_policies:read", "memory_policies:write"]
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


@pytest.mark.contract
def test_default_row_with_no_override(door: SimpleNamespace) -> None:
    resp = handle(_req("GET", "/v1/memory_policies/rub_default", token=_token(door)), ctx=door.ctx)
    assert resp.status == 200
    body = _json(resp)
    assert (body["id"], body["updated_by"], body["rubric"]) == ("rub_default", "system", "")


@pytest.mark.contract
def test_list_returns_the_one_account_row(door: SimpleNamespace) -> None:
    resp = handle(_req("GET", "/v1/memory_policies", token=_token(door)), ctx=door.ctx)
    data = _json(resp)["data"]
    assert [row["id"] for row in data] == ["rub_default"]


@pytest.mark.contract
def test_update_sets_a_real_override_and_a_second_update_bumps_it(door: SimpleNamespace) -> None:
    token = _token(door)
    default = _json(handle(_req("GET", "/v1/memory_policies/rub_default", token=token), ctx=door.ctx))

    first = handle(
        _req(
            "PATCH",
            "/v1/memory_policies/rub_default",
            token=token,
            body=json.dumps({"rubric": "also remember their timezone"}).encode(),
            headers={"If-Match": f'"{default["version"]}"'},
        ),
        ctx=door.ctx,
    )
    assert first.status == 200
    body = _json(first)
    assert body["rubric"] == "also remember their timezone"
    assert body["id"] != "rub_default"
    assert body["id"].startswith("rub_")

    second = handle(
        _req(
            "PATCH",
            f"/v1/memory_policies/{body['id']}",
            token=token,
            body=json.dumps({"rubric": "and their favorite color"}).encode(),
            headers={"If-Match": f'"{body["version"]}"'},
        ),
        ctx=door.ctx,
    )
    assert second.status == 200
    assert _json(second)["version"] == body["version"] + 1


@pytest.mark.contract
def test_stale_id_after_an_override_exists_is_404(door: SimpleNamespace) -> None:
    token = _token(door)
    default = _json(handle(_req("GET", "/v1/memory_policies/rub_default", token=token), ctx=door.ctx))
    handle(
        _req(
            "PATCH",
            "/v1/memory_policies/rub_default",
            token=token,
            body=json.dumps({"rubric": "x"}).encode(),
            headers={"If-Match": f'"{default["version"]}"'},
        ),
        ctx=door.ctx,
    )

    resp = handle(_req("GET", "/v1/memory_policies/rub_default", token=token), ctx=door.ctx)
    assert resp.status == 404


@pytest.mark.contract
def test_another_accounts_policy_is_independent(door: SimpleNamespace) -> None:
    token_a = _token(door, sub="a")
    token_b = _token(door, sub="b")
    default_a = _json(handle(_req("GET", "/v1/memory_policies/rub_default", token=token_a), ctx=door.ctx))
    handle(
        _req(
            "PATCH",
            "/v1/memory_policies/rub_default",
            token=token_a,
            body=json.dumps({"rubric": "account a's own rubric"}).encode(),
            headers={"If-Match": f'"{default_a["version"]}"'},
        ),
        ctx=door.ctx,
    )

    still_default = _json(handle(_req("GET", "/v1/memory_policies/rub_default", token=token_b), ctx=door.ctx))
    assert still_default["rubric"] == ""


@pytest.mark.contract
def test_create_and_remove_are_capability_missing(door: SimpleNamespace) -> None:
    token = _token(door)
    create_resp = handle(_req("POST", "/v1/memory_policies", token=token, body=b"{}"), ctx=door.ctx)
    assert create_resp.status == 501

    remove_resp = handle(
        _req("DELETE", "/v1/memory_policies/rub_default", token=token, headers={"If-Match": '"0"'}), ctx=door.ctx
    )
    assert remove_resp.status == 501
