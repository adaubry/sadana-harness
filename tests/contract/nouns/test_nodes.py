"""`node`'s own conformance proof (H24), against `router.handle()` directly."""

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
from sadana.door.nouns import nodes, workflows
from sadana.door.router import DoorContext, handle
from sadana.stores import Connections

_HARNESS_ID = "hrn_test"


def _write_fixture_plugin(plugins_root: Path, name: str = "fixture-plugin") -> Path:
    directory = plugins_root / name
    (directory / "schema").mkdir(parents=True)
    (directory / "schema" / "t.json").write_text('{"type": "object", "properties": {}}\n')
    (directory / "init.py").write_text("def go(value):\n    return value\n")
    (directory / "plugin.toml").write_text(
        f'[plugin]\nname = "{name}"\nversion = "0.1.0"\ndescription = "d"\n\n'
        '[[entry]]\ntool = "t"\npurpose = "p"\nparameters = "schema/t.json"\nstart = "a"\n\n'
        # `a`'s body names a function `init.py` never defines — the shape
        # `editor_server.waiting()` actually flags as "waiting for code"; a
        # `compute`/`call` node with *no* `body` at all isn't caught by that
        # function (nothing to check on disk), so a bodyless node would not
        # prove this test's point.
        '[[node]]\nname = "a"\nkind = "compute"\nbody = "init:missing"\nnext = "b"\n\n'
        '[[node]]\nname = "b"\nkind = "call"\nbody = "init:go"\nnext = "c"\n\n'
        '[[node]]\nname = "c"\nkind = "stop"\n'
    )
    return directory


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
        nouns={"nodes": nodes, "workflows": workflows},
        clock=time.time,
    )
    return SimpleNamespace(ctx=ctx, private_pem=private_pem, kid="test")


def _token(door: SimpleNamespace) -> str:
    return auth.mint_token(
        door.private_pem, door.kid, sub="u1", org=None, ws="ws1", hrn=_HARNESS_ID, scope=["nodes:read", "nodes:write"]
    )  # type: ignore[arg-type]


def _req(
    method: str, path: str, *, token: str, body: bytes = b"", headers: dict | None = None
) -> door_request.DoorRequest:
    hdrs = {"Authorization": f"Bearer {token}", "X-Sadana-Harness": _HARNESS_ID, **(headers or {})}
    return door_request.DoorRequest(method=method, path=path, query="", headers=hdrs, body=body)


def _json(resp: door_request.DoorResponse) -> dict:
    return dict(json.loads(resp.body))


@pytest.mark.contract
def test_list_finds_every_node_in_the_plugin(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    plugins_root = tmp_path / "plugins"
    _write_fixture_plugin(plugins_root)
    monkeypatch.setattr(plugins, "_plugins_root", lambda: plugins_root)
    door = _door(tmp_path)
    token = _token(door)

    resp = handle(_req("GET", "/v1/nodes", token=token), ctx=door.ctx)

    assert resp.status == 200
    rows = {r["name"]: r for r in _json(resp)["data"]}
    assert set(rows) == {"a", "b", "c"}
    assert rows["a"]["needs_code"] is True
    assert rows["b"]["needs_code"] is False
    assert rows["b"]["external_refs"] == ["init:go"]


@pytest.mark.contract
def test_nested_under_a_workflow_shows_the_owning_plugins_whole_graph(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    plugins_root = tmp_path / "plugins"
    _write_fixture_plugin(plugins_root)
    monkeypatch.setattr(plugins, "_plugins_root", lambda: plugins_root)
    door = _door(tmp_path)
    token = _token(door)
    wfl_id = workflows.workflow_id("fixture-plugin", "t")

    resp = handle(_req("GET", f"/v1/workflows/{wfl_id}/nodes", token=token), ctx=door.ctx)

    assert resp.status == 200
    assert {r["name"] for r in _json(resp)["data"]} == {"a", "b", "c"}


@pytest.mark.contract
def test_update_renames_a_step_and_follows_its_arrows(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    plugins_root = tmp_path / "plugins"
    directory = _write_fixture_plugin(plugins_root)
    monkeypatch.setattr(plugins, "_plugins_root", lambda: plugins_root)
    door = _door(tmp_path)
    token = _token(door)
    node_id = nodes.node_id("fixture-plugin", "a")

    resp = handle(
        _req(
            "PATCH",
            f"/v1/nodes/{node_id}",
            token=token,
            body=json.dumps({"label": "renamed"}).encode(),
            headers={"If-Match": '"1"'},
        ),
        ctx=door.ctx,
    )

    assert resp.status == 200
    body = _json(resp)
    assert body["name"] == "renamed"
    text = (directory / "plugin.toml").read_text()
    assert 'name = "renamed"' in text
    assert 'next = "renamed"' not in text  # nothing pointed at "a" to begin with
    assert 'name = "a"' not in text


@pytest.mark.contract
def test_update_without_plugins_write_is_capability_missing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    plugins_root = tmp_path / "plugins"
    _write_fixture_plugin(plugins_root)
    monkeypatch.setattr(plugins, "_plugins_root", lambda: plugins_root)
    door = _door(tmp_path, capabilities=("grammar.v1", "changes", "inventory"))
    token = _token(door)
    node_id = nodes.node_id("fixture-plugin", "a")

    resp = handle(
        _req(
            "PATCH",
            f"/v1/nodes/{node_id}",
            token=token,
            body=json.dumps({"label": "renamed"}).encode(),
            headers={"If-Match": '"1"'},
        ),
        ctx=door.ctx,
    )

    assert resp.status == 501
