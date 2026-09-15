"""`workflow`'s own conformance proof (H24), against `router.handle()`
directly.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from sadana import plugins
from sadana.door import auth
from sadana.door import capabilities as capabilities_module
from sadana.door import request as door_request
from sadana.door.nouns import plugins as plugins_noun
from sadana.door.nouns import workflows
from sadana.door.router import DoorContext, handle
from sadana.stores import Connections

_HARNESS_ID = "hrn_test"


def _write_fixture_plugin(plugins_root: Path, name: str = "fixture-plugin") -> Path:
    directory = plugins_root / name
    (directory / "schema").mkdir(parents=True)
    (directory / "schema" / "t.json").write_text('{"type": "object", "properties": {}}\n')
    (directory / "plugin.toml").write_text(
        f'[plugin]\nname = "{name}"\nversion = "0.1.0"\ndescription = "d"\n\n'
        '[[entry]]\ntool = "t"\npurpose = "p"\nparameters = "schema/t.json"\nstart = "a"\n\n'
        '[[node]]\nname = "a"\nkind = "compute"\nnext = "b"\n\n'
        '[[node]]\nname = "b"\nkind = "stop"\n'
    )
    return directory


def _door(tmp_path: Path) -> SimpleNamespace:
    private_pem, jwk = auth.generate_dev_keypair("test")
    jwks_path = tmp_path / "jwks.json"
    jwks_path.write_text(json.dumps(auth.jwks_document(jwk)))
    verifier = auth.Verifier(auth.JwksSource(path=jwks_path))
    conns = Connections(tmp_path / "door.db")
    ctx = DoorContext(
        conns=conns,
        runtime=auth.BoxIdentity(harness_id=_HARNESS_ID, org=None),
        verifier=verifier,
        capabilities=capabilities_module.declared(),
        nouns={"plugins": plugins_noun, "workflows": workflows},
        clock=time.time,
    )
    return SimpleNamespace(ctx=ctx, private_pem=private_pem, kid="test")


def _token(door: SimpleNamespace) -> str:
    return auth.mint_token(
        door.private_pem,
        door.kid,
        sub="u1",
        org=None,
        ws="ws1",
        hrn=_HARNESS_ID,
        scope=["workflows:read", "workflows:write"],
    )  # type: ignore[arg-type]


def _req(method: str, path: str, *, token: str) -> door_request.DoorRequest:
    return door_request.DoorRequest(
        method=method,
        path=path,
        query="",
        headers={"Authorization": f"Bearer {token}", "X-Sadana-Harness": _HARNESS_ID},
        body=b"",
    )


def _json(resp: door_request.DoorResponse) -> dict:
    return dict(json.loads(resp.body))


@pytest.mark.contract
def test_list_finds_one_workflow_per_entry(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    plugins_root = tmp_path / "plugins"
    _write_fixture_plugin(plugins_root)
    monkeypatch.setattr(plugins, "_plugins_root", lambda: plugins_root)
    door = _door(tmp_path)
    token = _token(door)

    resp = handle(_req("GET", "/v1/workflows", token=token), ctx=door.ctx)

    assert resp.status == 200
    data = _json(resp)["data"]
    assert [w["name"] for w in data] == ["t"]
    assert data[0]["node_count"] == 2


@pytest.mark.contract
def test_get_by_id_matches_the_list_entry(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    plugins_root = tmp_path / "plugins"
    _write_fixture_plugin(plugins_root)
    monkeypatch.setattr(plugins, "_plugins_root", lambda: plugins_root)
    door = _door(tmp_path)
    token = _token(door)
    listed = _json(handle(_req("GET", "/v1/workflows", token=token), ctx=door.ctx))["data"][0]

    resp = handle(_req("GET", f"/v1/workflows/{listed['id']}", token=token), ctx=door.ctx)

    assert resp.status == 200
    assert _json(resp)["name"] == "t"


@pytest.mark.contract
def test_nested_under_a_plugin_only_shows_that_plugins_workflows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    plugins_root = tmp_path / "plugins"
    _write_fixture_plugin(plugins_root, "one")
    _write_fixture_plugin(plugins_root, "two")
    monkeypatch.setattr(plugins, "_plugins_root", lambda: plugins_root)
    door = _door(tmp_path)
    token = _token(door)
    from sadana import plugin_install

    plugin_install.reconcile_plugin_state(door.ctx.conns.writer, plugins_root)
    plg_id = door.ctx.conns.reader().execute("SELECT id FROM plugin_state WHERE name = 'one'").fetchone()["id"]

    resp = handle(_req("GET", f"/v1/plugins/{plg_id}/workflows", token=token), ctx=door.ctx)

    assert resp.status == 200
    assert len(_json(resp)["data"]) == 1


@pytest.mark.contract
def test_create_is_capability_missing(tmp_path: Path) -> None:
    door = _door(tmp_path)
    token = _token(door)

    resp = handle(
        door_request.DoorRequest(
            method="POST",
            path="/v1/workflows",
            query="",
            headers={"Authorization": f"Bearer {token}", "X-Sadana-Harness": _HARNESS_ID},
            body=b"{}",
        ),
        ctx=door.ctx,
    )

    assert resp.status == 501
