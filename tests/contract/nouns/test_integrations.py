"""`integration`'s own conformance proof (H14), against `router.handle()`
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
from sadana.door.nouns import integrations as integrations_noun
from sadana.door.nouns import secrets as secrets_noun
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
        nouns={"integrations": integrations_noun, "secrets": secrets_noun},
        clock=time.time,
    )
    return SimpleNamespace(ctx=ctx, private_pem=private_pem, kid="test")


def _token(door: SimpleNamespace, **overrides: object) -> str:
    defaults: dict[str, object] = dict(
        sub="u1",
        org=None,
        ws="ws1",
        hrn=_HARNESS_ID,
        scope=["integrations:read", "integrations:write", "secrets:write"],
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
def test_list_renders_a_synthetic_default_with_computed_fields(tmp_path: Path) -> None:
    door = _door(tmp_path)
    resp = handle(_req("GET", "/v1/integrations", token=_token(door)), ctx=door.ctx)
    assert resp.status == 200
    row = _json(resp)["data"][0]
    assert row["id"] == "intg_default"
    assert row["webhook_url"] == "http://127.0.0.1:8765/webhook"
    assert row["gateway_state"] == "stopped"


@pytest.mark.contract
def test_webhook_secret_ref_naming_no_stored_secret_is_rejected(tmp_path: Path) -> None:
    door = _door(tmp_path)
    token = _token(door)
    row = _json(handle(_req("GET", "/v1/integrations", token=token), ctx=door.ctx))["data"][0]
    resp = handle(
        _req(
            "PATCH",
            f"/v1/integrations/{row['id']}",
            token=token,
            body=json.dumps({"webhook_secret_ref": "missing"}).encode(),
            headers={"If-Match": "0"},
        ),
        ctx=door.ctx,
    )
    assert resp.status == 400


@pytest.mark.contract
def test_update_with_a_valid_secret_ref_persists_and_mints_a_real_row(tmp_path: Path) -> None:
    door = _door(tmp_path)
    token = _token(door)
    handle(
        _req("POST", "/v1/secrets", token=token, body=json.dumps({"name": "whsec", "value": "s3cr3t"}).encode()),
        ctx=door.ctx,
    )
    row = _json(handle(_req("GET", "/v1/integrations", token=token), ctx=door.ctx))["data"][0]
    resp = handle(
        _req(
            "PATCH",
            f"/v1/integrations/{row['id']}",
            token=token,
            body=json.dumps({"webhook_secret_ref": "whsec", "webhook_port": 9999}).encode(),
            headers={"If-Match": "0"},
        ),
        ctx=door.ctx,
    )
    assert resp.status == 200
    updated = _json(resp)
    assert updated["webhook_secret_ref"] == "whsec"  # pragma: allowlist secret
    assert updated["webhook_url"] == "http://127.0.0.1:9999/webhook"
    assert updated["id"].startswith("intg_")


@pytest.mark.contract
def test_update_without_settings_write_is_capability_missing(tmp_path: Path) -> None:
    """router.py only auto-gates *actions* by capability, never the
    generic verbs — `update` must check `settings.write` itself."""
    door = _door(tmp_path, capabilities=tuple(c for c in capabilities_module.declared() if c != "settings.write"))
    token = _token(door)
    row = _json(handle(_req("GET", "/v1/integrations", token=token), ctx=door.ctx))["data"][0]
    resp = handle(
        _req(
            "PATCH",
            f"/v1/integrations/{row['id']}",
            token=token,
            body=json.dumps({"webhook_bind": "0.0.0.0"}).encode(),
            headers={"If-Match": "0"},
        ),
        ctx=door.ctx,
    )
    assert resp.status == 501
    assert "settings.write" in _json(resp)["detail"]


@pytest.mark.contract
def test_a_boolean_is_refused_for_webhook_port(tmp_path: Path) -> None:
    """`bool` is a subclass of `int` in Python — a bare `isinstance` check
    would silently accept a JSON boolean as a valid port number."""
    door = _door(tmp_path)
    token = _token(door)
    row = _json(handle(_req("GET", "/v1/integrations", token=token), ctx=door.ctx))["data"][0]
    resp = handle(
        _req(
            "PATCH",
            f"/v1/integrations/{row['id']}",
            token=token,
            body=json.dumps({"webhook_port": True}).encode(),
            headers={"If-Match": "0"},
        ),
        ctx=door.ctx,
    )
    assert resp.status == 400


@pytest.mark.contract
def test_webhook_url_and_gateway_state_are_read_only(tmp_path: Path) -> None:
    door = _door(tmp_path)
    token = _token(door)
    row = _json(handle(_req("GET", "/v1/integrations", token=token), ctx=door.ctx))["data"][0]
    resp = handle(
        _req(
            "PATCH",
            f"/v1/integrations/{row['id']}",
            token=token,
            body=json.dumps({"webhook_url": "http://evil"}).encode(),
            headers={"If-Match": "0"},
        ),
        ctx=door.ctx,
    )
    assert resp.status == 400


@pytest.mark.contract
def test_create_and_remove_are_unavailable(tmp_path: Path) -> None:
    door = _door(tmp_path)
    token = _token(door)
    assert handle(_req("POST", "/v1/integrations", token=token, body=b"{}"), ctx=door.ctx).status == 501
    row = _json(handle(_req("GET", "/v1/integrations", token=token), ctx=door.ctx))["data"][0]
    resp = handle(_req("DELETE", f"/v1/integrations/{row['id']}", token=token, headers={"If-Match": "0"}), ctx=door.ctx)
    assert resp.status == 501
