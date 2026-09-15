"""`secret`'s own conformance proof (H14), against `router.handle()`
directly — matching `test_plugin_noun.py`'s own `_door`/`_token` scaffold,
not `tests/contract/nouns/_scaffold.py`, which is scoped to H24's own
tests per its own docstring.
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
        nouns={"secrets": secrets_noun},
        clock=time.time,
    )
    return SimpleNamespace(ctx=ctx, private_pem=private_pem, kid="test")


def _token(door: SimpleNamespace, **overrides: object) -> str:
    defaults: dict[str, object] = dict(
        sub="u1", org=None, ws="ws1", hrn=_HARNESS_ID, scope=["secrets:read", "secrets:write"]
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
def test_create_never_returns_the_value(tmp_path: Path) -> None:
    door = _door(tmp_path)
    token = _token(door)
    body = json.dumps({"name": "cred_x", "value": "sk-super-secret-value", "kind": "api_key"}).encode()
    resp = handle(_req("POST", "/v1/secrets", token=token, body=body), ctx=door.ctx)
    assert resp.status == 201
    rendered = _json(resp)
    assert "value" not in rendered
    assert rendered["name"] == "cred_x"
    assert rendered["kind"] == "api_key"
    assert rendered["fingerprint"].startswith("alue")  # last four chars of "sk-super-secret-value"
    assert rendered["id"].startswith("sec_")


@pytest.mark.contract
def test_get_after_create_never_returns_the_value_either(tmp_path: Path) -> None:
    door = _door(tmp_path)
    token = _token(door)
    created = _json(
        handle(
            _req("POST", "/v1/secrets", token=token, body=json.dumps({"name": "cred_y", "value": "s3cret"}).encode()),
            ctx=door.ctx,
        )
    )
    resp = handle(_req("GET", f"/v1/secrets/{created['id']}", token=token), ctx=door.ctx)
    assert resp.status == 200
    body_text = resp.body.decode()
    assert "value" not in _json(resp)
    assert "s3cret" not in body_text


@pytest.mark.contract
def test_list_never_returns_any_value(tmp_path: Path) -> None:
    door = _door(tmp_path)
    token = _token(door)
    handle(
        _req("POST", "/v1/secrets", token=token, body=json.dumps({"name": "cred_z", "value": "hunter2"}).encode()),
        ctx=door.ctx,
    )
    resp = handle(_req("GET", "/v1/secrets", token=token), ctx=door.ctx)
    assert "hunter2" not in resp.body.decode()


@pytest.mark.contract
def test_a_raw_value_for_an_existing_name_is_rejected(tmp_path: Path) -> None:
    door = _door(tmp_path)
    token = _token(door)
    body = json.dumps({"name": "dup", "value": "one"}).encode()
    handle(_req("POST", "/v1/secrets", token=token, body=body), ctx=door.ctx)
    resp = handle(_req("POST", "/v1/secrets", token=token, body=body), ctx=door.ctx)
    assert resp.status == 400


@pytest.mark.contract
def test_patch_rotates_the_value_and_the_fingerprint_changes(tmp_path: Path) -> None:
    door = _door(tmp_path)
    token = _token(door)
    created = _json(
        handle(
            _req("POST", "/v1/secrets", token=token, body=json.dumps({"name": "rot", "value": "old-value"}).encode()),
            ctx=door.ctx,
        )
    )
    old_fp = created["fingerprint"]
    resp = handle(
        _req(
            "PATCH",
            f"/v1/secrets/{created['id']}",
            token=token,
            body=json.dumps({"value": "new-value-entirely"}).encode(),
            headers={"If-Match": str(created["version"])},
        ),
        ctx=door.ctx,
    )
    assert resp.status == 200
    updated = _json(resp)
    assert updated["fingerprint"] != old_fp
    assert updated["version"] == created["version"] + 1


@pytest.mark.contract
def test_patch_refuses_a_name_change(tmp_path: Path) -> None:
    door = _door(tmp_path)
    token = _token(door)
    created = _json(
        handle(
            _req("POST", "/v1/secrets", token=token, body=json.dumps({"name": "fixed", "value": "v"}).encode()),
            ctx=door.ctx,
        )
    )
    resp = handle(
        _req(
            "PATCH",
            f"/v1/secrets/{created['id']}",
            token=token,
            body=json.dumps({"name": "renamed"}).encode(),
            headers={"If-Match": str(created["version"])},
        ),
        ctx=door.ctx,
    )
    assert resp.status == 400


@pytest.mark.contract
def test_remove_deletes_the_row_and_the_env_line(tmp_path: Path) -> None:
    door = _door(tmp_path)
    token = _token(door)
    created = _json(
        handle(
            _req("POST", "/v1/secrets", token=token, body=json.dumps({"name": "gone", "value": "v"}).encode()),
            ctx=door.ctx,
        )
    )
    resp = handle(
        _req(
            "DELETE",
            f"/v1/secrets/{created['id']}",
            token=token,
            headers={"If-Match": str(created["version"])},
        ),
        ctx=door.ctx,
    )
    assert resp.status == 204
    assert handle(_req("GET", f"/v1/secrets/{created['id']}", token=token), ctx=door.ctx).status == 404
    assert not secrets_noun.exists("gone")


@pytest.mark.contract
def test_create_without_secrets_write_is_capability_missing(tmp_path: Path) -> None:
    """router.py only auto-gates *actions* by capability, never the
    generic verbs — `create`/`update`/`remove` must each check
    `secrets.write` themselves."""
    door = _door(tmp_path, capabilities=tuple(c for c in capabilities_module.declared() if c != "secrets.write"))
    token = _token(door)
    resp = handle(
        _req("POST", "/v1/secrets", token=token, body=json.dumps({"name": "x", "value": "v"}).encode()),
        ctx=door.ctx,
    )
    assert resp.status == 501
    assert "secrets.write" in _json(resp)["detail"]


@pytest.mark.contract
def test_update_and_remove_without_secrets_write_are_capability_missing(tmp_path: Path) -> None:
    door = _door(tmp_path)
    token = _token(door)
    created = _json(
        handle(
            _req("POST", "/v1/secrets", token=token, body=json.dumps({"name": "gated", "value": "v"}).encode()),
            ctx=door.ctx,
        )
    )
    # Same store, only the capability set differs — the row must already
    # exist under the real token before the ungated context tries to touch it.
    ungated_ctx = DoorContext(
        conns=door.ctx.conns,
        runtime=door.ctx.runtime,
        verifier=door.ctx.verifier,
        capabilities=tuple(c for c in capabilities_module.declared() if c != "secrets.write"),
        nouns={"secrets": secrets_noun},
        clock=time.time,
    )
    resp = handle(
        _req(
            "PATCH",
            f"/v1/secrets/{created['id']}",
            token=token,
            body=json.dumps({"kind": "x"}).encode(),
            headers={"If-Match": str(created["version"])},
        ),
        ctx=ungated_ctx,
    )
    assert resp.status == 501
    resp = handle(
        _req(
            "DELETE",
            f"/v1/secrets/{created['id']}",
            token=token,
            headers={"If-Match": str(created["version"])},
        ),
        ctx=ungated_ctx,
    )
    assert resp.status == 501


@pytest.mark.contract
def test_create_refuses_a_name_that_would_inject_a_second_env_line(tmp_path: Path) -> None:
    """A `name` becomes a bare key on its own line of `state_dir/.env` —
    unvalidated, a newline in it would inject an arbitrary second
    assignment nobody named as a secret."""
    door = _door(tmp_path)
    token = _token(door)
    body = json.dumps({"name": "evil\nINJECTED_VAR", "value": "v"}).encode()
    resp = handle(_req("POST", "/v1/secrets", token=token, body=body), ctx=door.ctx)
    assert resp.status == 400
    from sadana import env_file

    assert env_file.read_key("INJECTED_VAR") is None


@pytest.mark.contract
def test_exists_sees_a_real_environment_variable_with_no_row_at_all(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The environment always wins — a credential a person already exported
    is never rejected for not having been registered through this noun."""
    monkeypatch.setenv("ALREADY_EXPORTED", "value")  # pragma: allowlist secret
    assert secrets_noun.exists("ALREADY_EXPORTED") is True
    assert secrets_noun.exists("NEVER_SET_ANYWHERE") is False
