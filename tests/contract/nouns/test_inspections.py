"""`inspections`' own conformance proof (H24), against `router.handle()`
directly — no socket, matching `tests/contract/nouns/test_schedules.py`'s
own posture.
"""

from __future__ import annotations

import dataclasses
import json
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from conftest import make_upstream_repo as _make_upstream_repo
from conftest import run_git as _run_git
from sadana.door import auth
from sadana.door import capabilities as capabilities_module
from sadana.door import request as door_request
from sadana.door.nouns import inspections
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
        nouns={"inspections": inspections},
        clock=time.time,
    )
    return SimpleNamespace(ctx=ctx, private_pem=private_pem, kid="test")


def _token(door: SimpleNamespace, **overrides: object) -> str:
    defaults: dict[str, object] = dict(
        sub="u1", org=None, ws="ws1", hrn=_HARNESS_ID, scope=["inspections:read", "inspections:write"]
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


def _create(door: SimpleNamespace, token: str, repo_url: str, tag: str) -> door_request.DoorResponse:
    body = json.dumps({"repo_url": repo_url, "tag": tag}).encode()
    return handle(_req("POST", "/v1/inspections", token=token, body=body), ctx=door.ctx)


@pytest.mark.contract
def test_plugins_inspect_is_declared(tmp_path: Path) -> None:
    door = _door(tmp_path)
    assert "plugins.inspect" in door.ctx.capabilities


@pytest.mark.contract
def test_create_then_get_a_valid_tag_returns_the_declared_shape_and_checksum(tmp_path: Path) -> None:
    repo = _make_upstream_repo(tmp_path, plugin_name="greeter", tag="v1.0.0")
    door = _door(tmp_path)
    token = _token(door)

    created = _json(_create(door, token, str(repo), "v1.0.0"))
    assert created["state"] == "ok"
    assert created["declared_shape"]["name"] == "greeter"
    assert created["checksum"]
    assert created["error"] is None

    resp = handle(_req("GET", f"/v1/inspections/{created['id']}", token=token), ctx=door.ctx)
    assert resp.status == 200
    body = _json(resp)
    assert body["checksum"] == created["checksum"]
    assert body["repo_url"] == str(repo)
    assert body["tag"] == "v1.0.0"


@pytest.mark.contract
def test_create_reports_needs_code_and_external_refs_structurally(tmp_path: Path) -> None:
    repo = tmp_path / "upstream"
    repo.mkdir()
    _run_git(["init", "-q", "-b", "main"], cwd=repo)
    _run_git(["config", "user.email", "test@example.com"], cwd=repo)
    _run_git(["config", "user.name", "Test"], cwd=repo)
    (repo / "schema").mkdir()
    (repo / "schema" / "t.json").write_text('{"type": "object", "properties": {}}\n')
    (repo / "plugin.toml").write_text(
        '[plugin]\nname = "greeter"\nversion = "v1.0.0"\ndescription = "d"\n\n'
        '[[entry]]\ntool = "t"\npurpose = "p"\nparameters = "schema/t.json"\nstart = "a"\n\n'
        '[[node]]\nname = "a"\nkind = "compute"\nnext = "b"\n\n'
        '[[node]]\nname = "b"\nkind = "call"\nbody = "init:go"\nnext = "c"\n\n'
        '[[node]]\nname = "c"\nkind = "stop"\n'
    )
    _run_git(["add", "."], cwd=repo)
    _run_git(["commit", "-q", "-m", "initial"], cwd=repo)
    _run_git(["tag", "v1.0.0"], cwd=repo)
    door = _door(tmp_path)
    token = _token(door)

    created = _json(_create(door, token, str(repo), "v1.0.0"))

    nodes = {n["id"]: n for n in created["declared_shape"]["nodes"]}
    assert nodes["a"]["needs_code"] is True
    assert nodes["a"]["external_refs"] == []
    assert nodes["b"]["needs_code"] is False
    assert nodes["b"]["external_refs"] == ["init:go"]
    assert nodes["c"]["needs_code"] is False


@pytest.mark.contract
def test_create_an_unknown_tag_reports_state_failed_with_no_declared_shape(tmp_path: Path) -> None:
    repo = _make_upstream_repo(tmp_path, plugin_name="greeter", tag="v1.0.0")
    door = _door(tmp_path)
    token = _token(door)

    created = _json(_create(door, token, str(repo), "no-such-tag"))

    assert created["state"] == "failed"
    assert created["declared_shape"] is None
    assert created["checksum"] is None
    assert created["error"]


@pytest.mark.contract
def test_create_without_plugins_inspect_is_capability_missing(tmp_path: Path) -> None:
    door = _door(tmp_path, capabilities=("grammar.v1", "changes", "inventory"))
    token = _token(door)

    resp = _create(door, token, "https://example.invalid/greeter.git", "v1.0.0")

    assert resp.status == 501
    body = _json(resp)
    assert body["code"] == "HARNESS_CAPABILITY_MISSING"
    assert "plugins.inspect" in body["detail"]


@pytest.mark.contract
def test_create_with_a_missing_repo_url_is_validation(tmp_path: Path) -> None:
    door = _door(tmp_path)
    token = _token(door)

    resp = handle(
        _req("POST", "/v1/inspections", token=token, body=json.dumps({"tag": "v1.0.0"}).encode()), ctx=door.ctx
    )

    assert resp.status == 400
    assert _json(resp)["errors"][0]["field"] == "repo_url"


@pytest.mark.contract
def test_a_row_older_than_seven_days_is_swept_on_the_next_write(tmp_path: Path) -> None:
    repo = _make_upstream_repo(tmp_path, plugin_name="greeter", tag="v1.0.0")
    door = _door(tmp_path)
    token = _token(door)
    long_ago = time.time() - 8 * 24 * 60 * 60
    old_ctx = dataclasses.replace(door.ctx, clock=lambda: long_ago)
    handle(
        _req(
            "POST", "/v1/inspections", token=token, body=json.dumps({"repo_url": str(repo), "tag": "v1.0.0"}).encode()
        ),
        ctx=old_ctx,
    )
    assert door.ctx.conns.reader().execute("SELECT COUNT(*) AS n FROM inspections").fetchone()["n"] == 1

    _create(door, token, str(repo), "v1.0.0")

    assert door.ctx.conns.reader().execute("SELECT COUNT(*) AS n FROM inspections").fetchone()["n"] == 1
