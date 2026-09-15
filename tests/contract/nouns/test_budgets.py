"""`budget`'s own conformance proof (H14), against `router.handle()`
directly — matching `test_plugin_noun.py`'s own `_door`/`_token` scaffold.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from sadana.door import auth
from sadana.door import capabilities as capabilities_module
from sadana.door import request as door_request
from sadana.door.nouns import budgets as budgets_noun
from sadana.door.router import DoorContext, handle
from sadana.stores import Connections

_HARNESS_ID = "hrn_test"


def _door(tmp_path: Path, *, capabilities: tuple[str, ...] | None = None) -> SimpleNamespace:
    private_pem, jwk = auth.generate_dev_keypair("test")
    jwks_path = tmp_path / "jwks.json"
    jwks_path.write_text(json.dumps(auth.jwks_document(jwk)))
    verifier = auth.Verifier(auth.JwksSource(path=jwks_path))
    conns = Connections(tmp_path / "door.db")
    ctx = DoorContext(
        conns=conns,
        runtime=auth.BoxIdentity(harness_id=_HARNESS_ID, org=None),
        verifier=verifier,
        capabilities=capabilities if capabilities is not None else capabilities_module.declared(),
        nouns={"budgets": budgets_noun},
        clock=time.time,
    )
    return SimpleNamespace(ctx=ctx, private_pem=private_pem, kid="test")


def _token(door: SimpleNamespace, **overrides: object) -> str:
    defaults: dict[str, object] = dict(
        sub="u1", org=None, ws="ws1", hrn=_HARNESS_ID, scope=["budgets:read", "budgets:write"]
    )
    defaults.update(overrides)
    return auth.mint_token(door.private_pem, door.kid, **defaults)  # type: ignore[arg-type]


def _req(
    method: str, path: str, *, token: str | None, body: bytes = b"", headers: dict | None = None, query: str = ""
) -> door_request.DoorRequest:
    hdrs: dict[str, str] = dict(headers or {})
    if token is not None:
        hdrs["Authorization"] = f"Bearer {token}"
    hdrs.setdefault("X-Sadana-Harness", _HARNESS_ID)
    return door_request.DoorRequest(method=method, path=path, query=query, headers=hdrs, body=body)


def _json(resp: door_request.DoorResponse) -> dict:
    return dict(json.loads(resp.body))


@pytest.mark.contract
def test_list_renders_a_synthetic_default_before_any_write(tmp_path: Path) -> None:
    door = _door(tmp_path)
    resp = handle(_req("GET", "/v1/budgets", token=_token(door)), ctx=door.ctx)
    assert resp.status == 200
    row = _json(resp)["data"][0]
    assert row["id"] == "bdg_default"
    assert row["version"] == 0
    assert row["iterations_max"] == 60
    assert row["runs_per_day"] is None


@pytest.mark.contract
def test_update_mints_a_real_row_and_it_persists(tmp_path: Path) -> None:
    door = _door(tmp_path)
    token = _token(door)
    default_row = _json(handle(_req("GET", "/v1/budgets", token=token), ctx=door.ctx))["data"][0]
    resp = handle(
        _req(
            "PATCH",
            f"/v1/budgets/{default_row['id']}",
            token=token,
            body=json.dumps({"iterations_max": 99}).encode(),
            headers={"If-Match": "0"},
        ),
        ctx=door.ctx,
    )
    assert resp.status == 200
    updated = _json(resp)
    assert updated["id"] != "bdg_default"
    assert updated["id"].startswith("bdg_")
    assert updated["iterations_max"] == 99
    assert updated["version"] == 1

    second = _json(handle(_req("GET", "/v1/budgets", token=token), ctx=door.ctx))["data"][0]
    assert second["id"] == updated["id"]
    assert second["iterations_max"] == 99


@pytest.mark.contract
def test_update_without_settings_write_is_capability_missing(tmp_path: Path) -> None:
    """router.py only auto-gates *actions* by capability, never the
    generic verbs — `update` must check `settings.write` itself."""
    door = _door(tmp_path, capabilities=tuple(c for c in capabilities_module.declared() if c != "settings.write"))
    token = _token(door)
    row = _json(handle(_req("GET", "/v1/budgets", token=token), ctx=door.ctx))["data"][0]
    resp = handle(
        _req(
            "PATCH",
            f"/v1/budgets/{row['id']}",
            token=token,
            body=json.dumps({"iterations_max": 5}).encode(),
            headers={"If-Match": "0"},
        ),
        ctx=door.ctx,
    )
    assert resp.status == 501
    assert "settings.write" in _json(resp)["detail"]


@pytest.mark.contract
def test_a_boolean_is_refused_for_an_integer_field(tmp_path: Path) -> None:
    """`bool` is a subclass of `int` in Python — `isinstance(True, int)` is
    `True` — so a bare `isinstance` check would silently accept a JSON
    boolean as a valid `iterations_max`."""
    door = _door(tmp_path)
    token = _token(door)
    row = _json(handle(_req("GET", "/v1/budgets", token=token), ctx=door.ctx))["data"][0]
    resp = handle(
        _req(
            "PATCH",
            f"/v1/budgets/{row['id']}",
            token=token,
            body=json.dumps({"iterations_max": True}).encode(),
            headers={"If-Match": "0"},
        ),
        ctx=door.ctx,
    )
    assert resp.status == 400


@pytest.mark.contract
def test_runs_per_day_is_read_only(tmp_path: Path) -> None:
    door = _door(tmp_path)
    token = _token(door)
    row = _json(handle(_req("GET", "/v1/budgets", token=token), ctx=door.ctx))["data"][0]
    resp = handle(
        _req(
            "PATCH",
            f"/v1/budgets/{row['id']}",
            token=token,
            body=json.dumps({"runs_per_day": 5}).encode(),
            headers={"If-Match": "0"},
        ),
        ctx=door.ctx,
    )
    assert resp.status == 400


@pytest.mark.contract
def test_update_without_if_match_is_precondition_failed(tmp_path: Path) -> None:
    door = _door(tmp_path)
    token = _token(door)
    row = _json(handle(_req("GET", "/v1/budgets", token=token), ctx=door.ctx))["data"][0]
    resp = handle(
        _req("PATCH", f"/v1/budgets/{row['id']}", token=token, body=json.dumps({"iterations_max": 10}).encode()),
        ctx=door.ctx,
    )
    assert resp.status == 412


@pytest.mark.contract
def test_create_and_remove_are_unavailable(tmp_path: Path) -> None:
    door = _door(tmp_path)
    token = _token(door)
    assert handle(_req("POST", "/v1/budgets", token=token, body=b"{}"), ctx=door.ctx).status == 501
    row = _json(handle(_req("GET", "/v1/budgets", token=token), ctx=door.ctx))["data"][0]
    resp = handle(_req("DELETE", f"/v1/budgets/{row['id']}", token=token, headers={"If-Match": "0"}), ctx=door.ctx)
    assert resp.status == 501
