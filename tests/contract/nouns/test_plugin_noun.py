"""`plugin`'s own conformance proof (H24), against `router.handle()`
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
from sadana import client_surface, editor_server, plugin_install, plugins
from sadana.door import auth
from sadana.door import capabilities as capabilities_module
from sadana.door import request as door_request
from sadana.door.nouns import plugins as plugins_noun
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
        nouns={"plugins": plugins_noun},
        clock=time.time,
    )
    return SimpleNamespace(ctx=ctx, private_pem=private_pem, kid="test")


def _token(door: SimpleNamespace, **overrides: object) -> str:
    defaults: dict[str, object] = dict(
        sub="u1",
        org=None,
        ws="ws1",
        hrn=_HARNESS_ID,
        scope=[
            "plugins:read",
            "plugins:write",
            "plugins:disable",
            "plugins:enable",
            "plugins:install-from-git",
            "plugins:save",
            "plugins:set-settings",
        ],
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


def _write_fixture_plugin(plugins_root: Path, name: str = "fixture-plugin") -> Path:
    """A plugin with a `compute` step still waiting for code, a `call` step
    whose body resolves, and a `stop` step — enough to prove layout,
    `needs_code` and `external_refs` all at once."""
    directory = plugins_root / name
    (directory / "schema").mkdir(parents=True)
    (directory / "schema" / "t.json").write_text('{"type": "object", "properties": {}}\n')
    (directory / "init.py").write_text("def go(value):\n    return value\n")
    (directory / "plugin.toml").write_text(
        f'[plugin]\nname = "{name}"\nversion = "0.1.0"\ndescription = "a fixture plugin"\n\n'
        '[[entry]]\ntool = "t"\npurpose = "p"\nparameters = "schema/t.json"\nstart = "a"\n\n'
        '[[node]]\nname = "a"\nkind = "compute"\nnext = "b"\n\n'
        '[[node]]\nname = "b"\nkind = "call"\nbody = "init:go"\nnext = "c"\n\n'
        '[[node]]\nname = "c"\nkind = "stop"\n'
    )
    return directory


def _write_fixture_plugin_with_settings(plugins_root: Path, name: str = "fixture-plugin-settings") -> Path:
    """A minimal plugin declaring one secret setting and one non-secret
    one — H14's own `set-settings` fixture."""
    directory = plugins_root / name
    (directory / "schema").mkdir(parents=True)
    (directory / "schema" / "t.json").write_text('{"type": "object", "properties": {}}\n')
    (directory / "init.py").write_text("def go(value):\n    return value\n")
    (directory / "plugin.toml").write_text(
        f'[plugin]\nname = "{name}"\nversion = "0.1.0"\ndescription = "a fixture plugin"\n\n'
        '[[entry]]\ntool = "t"\npurpose = "p"\nparameters = "schema/t.json"\nstart = "a"\n\n'
        '[[node]]\nname = "a"\nkind = "stop"\n\n'
        '[[setting]]\nname = "api_key"\npurpose = "an api key"\nsecret = true\n\n'
        '[[setting]]\nname = "units"\npurpose = "metric or imperial"\nsecret = false\n'
    )
    return directory


def _plugin_state_id(ctx: object, name: str) -> str:
    row = ctx.ctx.conns.reader().execute("SELECT id FROM plugin_state WHERE name = ?", (name,)).fetchone()  # type: ignore[attr-defined]
    assert row is not None, f"no plugin_state row for {name!r}"
    return str(row["id"])


@pytest.mark.contract
def test_plugins_install_and_plugins_write_are_declared(tmp_path: Path) -> None:
    door = _door(tmp_path)
    assert "plugins.install" in door.ctx.capabilities
    assert "plugins.write" in door.ctx.capabilities


@pytest.mark.contract
def test_get_returns_a_complete_layout_matching_waiting(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    plugins_root = tmp_path / "plugins"
    directory = _write_fixture_plugin(plugins_root)
    monkeypatch.setattr(plugins, "_plugins_root", lambda: plugins_root)
    door = _door(tmp_path)
    token = _token(door)
    plugin_install.reconcile_plugin_state(door.ctx.conns.writer, plugins_root)
    plg_id = _plugin_state_id(door, "fixture-plugin")

    manifest = plugins._parse_manifest((directory / "plugin.toml").read_text())
    expected_waiting = {w["step"] for w in editor_server.waiting(directory, manifest)}

    resp = handle(_req("GET", f"/v1/plugins/{plg_id}", token=token), ctx=door.ctx)
    assert resp.status == 200
    body = _json(resp)

    node_ids = {n["id"] for n in body["layout"]["nodes"]}
    assert node_ids == {"a", "b", "c"}
    for node in body["layout"]["nodes"]:
        assert isinstance(node["x"], int)
        assert isinstance(node["y"], int)
        assert node["needs_code"] == (node["id"] in expected_waiting)
    for edge in body["layout"]["edges"]:
        assert edge["from"] in node_ids
        assert edge["to"] in node_ids
    assert body["needs_code_count"] == len(expected_waiting)
    assert body["steps_count"] == 3


@pytest.mark.contract
def test_create_install_from_git_with_a_valid_tag_succeeds(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = _make_upstream_repo(tmp_path, plugin_name="greeter", tag="v1.0.0")
    plugins_root = tmp_path / "plugins"
    monkeypatch.setattr(plugins, "_plugins_root", lambda: plugins_root)
    door = _door(tmp_path)
    token = _token(door)

    resp = handle(
        _req("POST", "/v1/plugins", token=token, body=json.dumps({"repo_url": str(repo), "tag": "v1.0.0"}).encode()),
        ctx=door.ctx,
    )

    assert resp.status == 201
    body = _json(resp)
    assert body["name"] == "greeter"
    assert body["state"] == "installed"
    assert (plugins_root / "greeter" / "plugin.toml").is_file()


@pytest.mark.contract
def test_create_install_from_git_with_a_bad_tag_is_400(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = _make_upstream_repo(tmp_path, plugin_name="greeter", tag="v1.0.0")
    plugins_root = tmp_path / "plugins"
    monkeypatch.setattr(plugins, "_plugins_root", lambda: plugins_root)
    door = _door(tmp_path)
    token = _token(door)

    resp = handle(
        _req(
            "POST",
            "/v1/plugins",
            token=token,
            body=json.dumps({"repo_url": str(repo), "tag": "no-such-tag"}).encode(),
        ),
        ctx=door.ctx,
    )

    assert resp.status == 400


@pytest.mark.contract
def test_create_without_plugins_install_is_capability_missing(tmp_path: Path) -> None:
    door = _door(tmp_path, capabilities=("grammar.v1", "changes", "inventory"))
    token = _token(door)

    resp = handle(
        _req(
            "POST",
            "/v1/plugins",
            token=token,
            body=json.dumps({"repo_url": "https://example.invalid/g.git", "tag": "v1.0.0"}).encode(),
        ),
        ctx=door.ctx,
    )

    assert resp.status == 501
    assert _json(resp)["code"] == "HARNESS_CAPABILITY_MISSING"


@pytest.mark.contract
def test_disable_then_enable_round_trip_and_excludes_from_a_fresh_plugin_set(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    plugins_root = tmp_path / "plugins"
    _write_fixture_plugin(plugins_root)
    monkeypatch.setattr(plugins, "_plugins_root", lambda: plugins_root)
    door = _door(tmp_path)
    token = _token(door)
    plugin_install.reconcile_plugin_state(door.ctx.conns.writer, plugins_root)
    plg_id = _plugin_state_id(door, "fixture-plugin")

    disabled = handle(
        _req("POST", f"/v1/plugins/{plg_id}/actions/disable", token=token, body=b"{}", headers={"If-Match": '"1"'}),
        ctx=door.ctx,
    )
    assert disabled.status == 200
    assert _json(disabled)["state"] == "disabled"
    plugin_set = client_surface.enabled_plugin_set(door.ctx.conns.writer)
    assert [p.name for p in plugin_set.catalog] == []

    enabled = handle(
        _req("POST", f"/v1/plugins/{plg_id}/actions/enable", token=token, body=b"{}", headers={"If-Match": '"2"'}),
        ctx=door.ctx,
    )
    assert enabled.status == 200
    assert _json(enabled)["state"] == "installed"
    plugin_set = client_surface.enabled_plugin_set(door.ctx.conns.writer)
    assert [p.name for p in plugin_set.catalog] == ["fixture-plugin"]


@pytest.mark.contract
def test_disable_from_error_state_is_409(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    plugins_root = tmp_path / "plugins"
    broken = plugins_root / "broken"
    broken.mkdir(parents=True)
    (broken / "plugin.toml").write_text("this is not toml [[[")
    monkeypatch.setattr(plugins, "_plugins_root", lambda: plugins_root)
    door = _door(tmp_path)
    token = _token(door)
    plugin_install.reconcile_plugin_state(door.ctx.conns.writer, plugins_root)
    plg_id = _plugin_state_id(door, "broken")

    resp = handle(
        _req("POST", f"/v1/plugins/{plg_id}/actions/disable", token=token, body=b"{}", headers={"If-Match": '"1"'}),
        ctx=door.ctx,
    )

    assert resp.status == 409


@pytest.mark.contract
def test_save_with_a_bad_field_returns_a_node_scoped_error_and_writes_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    plugins_root = tmp_path / "plugins"
    directory = _write_fixture_plugin(plugins_root)
    monkeypatch.setattr(plugins, "_plugins_root", lambda: plugins_root)
    door = _door(tmp_path)
    token = _token(door)
    plugin_install.reconcile_plugin_state(door.ctx.conns.writer, plugins_root)
    plg_id = _plugin_state_id(door, "fixture-plugin")
    before = (directory / "plugin.toml").read_text()
    get_body = _json(handle(_req("GET", f"/v1/plugins/{plg_id}", token=token), ctx=door.ctx))
    bad_manifest = {
        "name": "fixture-plugin",
        "version": "0.1.0",
        "description": "d",
        "entries": [{"tool": "t", "purpose": "p", "parameters": "schema/t.json", "start": "a"}],
        "nodes": [
            # `kind` omitted entirely: `manifest_from_dict`'s own error text
            # is `"node 'a': 'kind' must be a string, got NoneType"` — the
            # one shape `_manifest_error_to_problem` recognizes as naming a
            # real field, unlike an invalid-but-present kind string (whose
            # message quotes the bad *value*, not the field name).
            {"name": "a", "body": None, "skill": None, "next": None, "ports": []},
        ],
        "settings": [],
    }
    resp = handle(
        _req(
            "POST",
            f"/v1/plugins/{plg_id}/actions/save",
            token=token,
            body=json.dumps({"manifest": bad_manifest}).encode(),
            headers={"If-Match": f'"{get_body["version"]}"'},
        ),
        ctx=door.ctx,
    )

    assert resp.status == 400
    body = _json(resp)
    assert body["errors"] == [{"field": "nodes.a.kind", "code": "invalid"}]
    assert (directory / "plugin.toml").read_text() == before


@pytest.mark.contract
def test_save_a_clean_edit_updates_the_manifest_on_disk(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    plugins_root = tmp_path / "plugins"
    directory = _write_fixture_plugin(plugins_root)
    monkeypatch.setattr(plugins, "_plugins_root", lambda: plugins_root)
    door = _door(tmp_path)
    token = _token(door)
    plugin_install.reconcile_plugin_state(door.ctx.conns.writer, plugins_root)
    plg_id = _plugin_state_id(door, "fixture-plugin")
    get_body = _json(handle(_req("GET", f"/v1/plugins/{plg_id}", token=token), ctx=door.ctx))

    new_manifest = {
        "name": "fixture-plugin",
        "version": "0.1.0",
        "description": "an updated description",
        "entries": [{"tool": "t", "purpose": "p", "parameters": "schema/t.json", "start": "a"}],
        "nodes": [{"name": "a", "kind": "stop", "body": None, "skill": None, "next": None, "ports": []}],
        "settings": [],
    }
    resp = handle(
        _req(
            "POST",
            f"/v1/plugins/{plg_id}/actions/save",
            token=token,
            body=json.dumps({"manifest": new_manifest}).encode(),
            headers={"If-Match": f'"{get_body["version"]}"'},
        ),
        ctx=door.ctx,
    )

    assert resp.status == 200
    assert "an updated description" in (directory / "plugin.toml").read_text()
    assert _json(resp)["description"] == "an updated description"


@pytest.mark.contract
def test_save_without_plugins_write_is_capability_missing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    plugins_root = tmp_path / "plugins"
    _write_fixture_plugin(plugins_root)
    monkeypatch.setattr(plugins, "_plugins_root", lambda: plugins_root)
    door = _door(tmp_path)
    token = _token(door)
    plugin_install.reconcile_plugin_state(door.ctx.conns.writer, plugins_root)
    plg_id = _plugin_state_id(door, "fixture-plugin")
    restricted_ctx = dataclasses.replace(door.ctx, capabilities=("grammar.v1", "changes", "inventory"))

    resp = handle(
        _req(
            "POST",
            f"/v1/plugins/{plg_id}/actions/save",
            token=token,
            body=json.dumps({"manifest": {}}).encode(),
            headers={"If-Match": '"1"'},
        ),
        ctx=restricted_ctx,
    )

    assert resp.status == 501
    assert _json(resp)["code"] == "HARNESS_CAPABILITY_MISSING"


@pytest.mark.contract
def test_set_settings_without_the_capability_is_capability_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    plugins_root = tmp_path / "plugins"
    _write_fixture_plugin(plugins_root)
    monkeypatch.setattr(plugins, "_plugins_root", lambda: plugins_root)
    door = _door(tmp_path, capabilities=tuple(c for c in capabilities_module.declared() if c != "settings.write"))
    token = _token(door)
    plugin_install.reconcile_plugin_state(door.ctx.conns.writer, plugins_root)
    plg_id = _plugin_state_id(door, "fixture-plugin")

    resp = handle(
        _req(
            "POST",
            f"/v1/plugins/{plg_id}/actions/set-settings",
            token=token,
            body=b"{}",
            headers={"If-Match": '"1"'},
        ),
        ctx=door.ctx,
    )

    assert resp.status == 501
    assert "settings.write" in _json(resp)["detail"]


@pytest.mark.contract
def test_set_settings_refuses_a_raw_value_for_a_secret_key(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    plugins_root = tmp_path / "plugins"
    _write_fixture_plugin_with_settings(plugins_root)
    monkeypatch.setattr(plugins, "_plugins_root", lambda: plugins_root)
    door = _door(tmp_path)
    token = _token(door)
    plugin_install.reconcile_plugin_state(door.ctx.conns.writer, plugins_root)
    plg_id = _plugin_state_id(door, "fixture-plugin-settings")

    resp = handle(
        _req(
            "POST",
            f"/v1/plugins/{plg_id}/actions/set-settings",
            token=token,
            body=json.dumps({"settings": {"api_key": "sk-raw-value-not-a-reference"}}).encode(),
            headers={"If-Match": '"1"'},
        ),
        ctx=door.ctx,
    )
    assert resp.status == 400
    assert "api_key" in _json(resp)["detail"]


@pytest.mark.contract
def test_set_settings_accepts_a_reference_and_it_is_followed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    plugins_root = tmp_path / "plugins"
    _write_fixture_plugin_with_settings(plugins_root)
    monkeypatch.setattr(plugins, "_plugins_root", lambda: plugins_root)
    monkeypatch.setenv("cred_x", "sk-the-real-value")  # pragma: allowlist secret
    door = _door(tmp_path)
    token = _token(door)
    plugin_install.reconcile_plugin_state(door.ctx.conns.writer, plugins_root)
    plg_id = _plugin_state_id(door, "fixture-plugin-settings")

    resp = handle(
        _req(
            "POST",
            f"/v1/plugins/{plg_id}/actions/set-settings",
            token=token,
            body=json.dumps({"settings": {"api_key": {"secret_ref": "cred_x"}, "units": "metric"}}).encode(),
            headers={"If-Match": '"1"'},
        ),
        ctx=door.ctx,
    )
    assert resp.status == 200
    assert plugins.read_setting("fixture-plugin-settings", "api_key", secret=True) == "sk-the-real-value"
    assert plugins.read_setting("fixture-plugin-settings", "units", secret=False) == "metric"


@pytest.mark.contract
def test_set_settings_refuses_a_reference_naming_no_stored_secret(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    plugins_root = tmp_path / "plugins"
    _write_fixture_plugin_with_settings(plugins_root)
    monkeypatch.setattr(plugins, "_plugins_root", lambda: plugins_root)
    door = _door(tmp_path)
    token = _token(door)
    plugin_install.reconcile_plugin_state(door.ctx.conns.writer, plugins_root)
    plg_id = _plugin_state_id(door, "fixture-plugin-settings")

    resp = handle(
        _req(
            "POST",
            f"/v1/plugins/{plg_id}/actions/set-settings",
            token=token,
            body=json.dumps({"settings": {"api_key": {"secret_ref": "no_such_secret"}}}).encode(),
            headers={"If-Match": '"1"'},
        ),
        ctx=door.ctx,
    )
    assert resp.status == 400


@pytest.mark.contract
def test_set_settings_refuses_an_undeclared_key(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    plugins_root = tmp_path / "plugins"
    _write_fixture_plugin_with_settings(plugins_root)
    monkeypatch.setattr(plugins, "_plugins_root", lambda: plugins_root)
    door = _door(tmp_path)
    token = _token(door)
    plugin_install.reconcile_plugin_state(door.ctx.conns.writer, plugins_root)
    plg_id = _plugin_state_id(door, "fixture-plugin-settings")

    resp = handle(
        _req(
            "POST",
            f"/v1/plugins/{plg_id}/actions/set-settings",
            token=token,
            body=json.dumps({"settings": {"not_declared": "x"}}).encode(),
            headers={"If-Match": '"1"'},
        ),
        ctx=door.ctx,
    )
    assert resp.status == 400
    assert "not_declared" in _json(resp)["detail"]
