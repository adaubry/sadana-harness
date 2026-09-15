"""`provider`'s own conformance proof (H14), against `router.handle()`
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
from sadana.door.nouns import providers as providers_noun
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
        nouns={"providers": providers_noun, "secrets": secrets_noun},
        clock=time.time,
    )
    return SimpleNamespace(ctx=ctx, private_pem=private_pem, kid="test")


def _token(door: SimpleNamespace, **overrides: object) -> str:
    defaults: dict[str, object] = dict(
        sub="u1", org=None, ws="ws1", hrn=_HARNESS_ID, scope=["providers:read", "providers:write", "secrets:write"]
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
def test_list_shows_openrouter_as_a_wired_provider(tmp_path: Path) -> None:
    door = _door(tmp_path)
    resp = handle(_req("GET", "/v1/providers", token=_token(door)), ctx=door.ctx)
    assert resp.status == 200
    items = _json(resp)["data"]
    names = {item["name"] for item in items}
    assert "openrouter" in names
    assert all(item["id"].startswith("prv_") for item in items)


@pytest.mark.contract
def test_get_materializes_a_real_id_stable_across_calls(tmp_path: Path) -> None:
    door = _door(tmp_path)
    token = _token(door)
    first = _json(handle(_req("GET", "/v1/providers", token=token), ctx=door.ctx))["data"][0]
    second = _json(handle(_req("GET", f"/v1/providers/{first['id']}", token=token), ctx=door.ctx))
    assert second["id"] == first["id"]
    assert second["name"] == first["name"]


@pytest.mark.contract
def test_update_credential_ref_naming_no_stored_secret_is_rejected(tmp_path: Path) -> None:
    door = _door(tmp_path)
    token = _token(door)
    row = _json(handle(_req("GET", "/v1/providers", token=token), ctx=door.ctx))["data"][0]
    resp = handle(
        _req(
            "PATCH",
            f"/v1/providers/{row['id']}",
            token=token,
            body=json.dumps({"credential_ref": "no_such_secret"}).encode(),
            headers={"If-Match": str(row["version"])},
        ),
        ctx=door.ctx,
    )
    assert resp.status == 400
    assert "no_such_secret" in resp.body.decode()


@pytest.mark.contract
def test_update_model_takes_effect_on_config_get_immediately(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """P9: a change through the door is read at the moment of use — the
    same `config.get` call `client_surface.open_runtime` makes for the
    active selection sees a provider-scoped change with no restart."""
    from sadana import config

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfgroot"))
    door = _door(tmp_path)
    token = _token(door)
    row = _json(handle(_req("GET", "/v1/providers", token=token), ctx=door.ctx))["data"][0]
    resp = handle(
        _req(
            "PATCH",
            f"/v1/providers/{row['id']}",
            token=token,
            body=json.dumps({"model": "anthropic/claude-x"}).encode(),
            headers={"If-Match": str(row["version"])},
        ),
        ctx=door.ctx,
    )
    assert resp.status == 200
    assert config.get(f"providers.{row['name']}.model", "unset") == "anthropic/claude-x"


@pytest.mark.contract
def test_update_with_a_valid_credential_ref_succeeds(tmp_path: Path) -> None:
    door = _door(tmp_path)
    token = _token(door)
    handle(
        _req(
            "POST",
            "/v1/secrets",
            token=token,
            body=json.dumps({"name": "cred_ok", "value": "sk-value"}).encode(),
        ),
        ctx=door.ctx,
    )
    row = _json(handle(_req("GET", "/v1/providers", token=token), ctx=door.ctx))["data"][0]
    resp = handle(
        _req(
            "PATCH",
            f"/v1/providers/{row['id']}",
            token=token,
            body=json.dumps({"credential_ref": "cred_ok"}).encode(),
            headers={"If-Match": str(row["version"])},
        ),
        ctx=door.ctx,
    )
    assert resp.status == 200
    assert _json(resp)["credential_ref"] == "cred_ok"


@pytest.mark.contract
def test_update_without_settings_write_is_capability_missing(tmp_path: Path) -> None:
    """router.py only auto-gates *actions* by capability, never the
    generic verbs — `update` must check `settings.write` itself."""
    door = _door(tmp_path, capabilities=tuple(c for c in capabilities_module.declared() if c != "settings.write"))
    token = _token(door)
    row = _json(handle(_req("GET", "/v1/providers", token=token), ctx=door.ctx))["data"][0]
    resp = handle(
        _req(
            "PATCH",
            f"/v1/providers/{row['id']}",
            token=token,
            body=json.dumps({"model": "x"}).encode(),
            headers={"If-Match": str(row["version"])},
        ),
        ctx=door.ctx,
    )
    assert resp.status == 501
    assert "settings.write" in _json(resp)["detail"]


@pytest.mark.contract
def test_create_and_remove_are_unavailable(tmp_path: Path) -> None:
    door = _door(tmp_path)
    token = _token(door)
    assert handle(_req("POST", "/v1/providers", token=token, body=b"{}"), ctx=door.ctx).status == 501
    row = _json(handle(_req("GET", "/v1/providers", token=token), ctx=door.ctx))["data"][0]
    resp = handle(
        _req("DELETE", f"/v1/providers/{row['id']}", token=token, headers={"If-Match": str(row["version"])}),
        ctx=door.ctx,
    )
    assert resp.status == 501
