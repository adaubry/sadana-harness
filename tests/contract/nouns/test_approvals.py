"""`approvals`' own conformance proof (H18), against `router.handle()`
directly — no socket, matching `tests/contract/test_console_grammar.py`'s
own posture.

A new file, not an edit to `test_console_grammar.py`: the parallel-lane
amendment on `docs/tasks/H18-parked-approvals/plan.md` puts one test file
per noun under `tests/contract/nouns/`. `tests/contract/nouns/` has no
`__init__.py` (nothing under `tests/` does), so this file cannot import
`test_console_grammar.py`'s own fixtures by name — its own directory, not
`tests/contract/`, is what pytest puts on `sys.path` for it. Its own
`schema`/`door`/`_token` below are small, local re-derivations of the same
shapes, not a promotion of anything private (the amendment's own fallback
for exactly this case).
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from types import SimpleNamespace

import jsonschema
import pytest

from sadana import conversation_store, plugins, stores
from sadana.door import auth
from sadana.door import capabilities as capabilities_module
from sadana.door import request as door_request
from sadana.door.nouns import approvals
from sadana.door.router import DoorContext, handle

_HARNESS_ID = "hrn_test"
_SCHEMA_PATH = Path(__file__).resolve().parents[3] / "docs" / "console" / "grammar.json"


@pytest.fixture(scope="module")
def schema() -> dict:
    if not _SCHEMA_PATH.exists():
        pytest.fail("docs/console/grammar.json is missing")
    return json.loads(_SCHEMA_PATH.read_text(encoding="utf-8"))


def _validate(schema: dict, instance: object, def_name: str) -> None:
    ref_schema = {"$ref": f"#/$defs/{def_name}", **{k: v for k, v in schema.items() if k != "$comment"}}
    jsonschema.Draft202012Validator(ref_schema).validate(instance)


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
        nouns={"approvals": approvals},
        clock=time.time,
    )
    return SimpleNamespace(ctx=ctx, private_pem=private_pem, kid="test")


def _token(door: SimpleNamespace, **overrides: object) -> str:
    defaults: dict[str, object] = dict(
        sub="u1",
        org=None,
        ws="ws1",
        hrn=_HARNESS_ID,
        scope=["approvals:read", "approvals:approve", "approvals:decline", "approvals:answer"],
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


def _seed_call_pause(door: SimpleNamespace, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, key: str) -> None:
    """A real on-disk single-`call`-node plugin, and a `waiting` `approvals`
    row parked on it — `act`'s own `approve`/`decline` reach
    `plugin_dispatch.resume_paused_run`, which re-resolves the plugin from
    disk by name, so this proof needs a real one, not a hand-built
    `Manifest` (the same reasoning `conftest.wait_then_summarize_installed`
    documents for the unit suite's own resume tests)."""
    plugin_dir = tmp_path / "plugins" / "p"
    plugin_dir.mkdir(parents=True)
    plugin_dir.joinpath("init.py").write_text("def do_call(value):\n    return f'called: {value}'\n")
    plugin_dir.joinpath("s.json").write_text('{"type": "object"}')
    plugin_dir.joinpath("plugin.toml").write_text(
        '[plugin]\nname = "p"\nversion = "0.1.0"\ndescription = "d"\n\n'
        '[[entry]]\ntool = "do_it"\npurpose = "p"\nparameters = "s.json"\nstart = "reach_out"\n\n'
        '[[node]]\nname = "reach_out"\nkind = "call"\nbody = "init:do_call"\n'
    )
    monkeypatch.setattr(plugins, "_plugins_root", lambda: tmp_path / "plugins")
    conversation_store.save_pause(
        door.ctx.conns.writer,
        conversation_key=key,
        plugin="p",
        entry="do_it",
        node="reach_out",
        trace=(),
        artifacts=(),
        kind="call",
        paused_value={},
    )


@pytest.mark.contract
def test_approvals_wait_and_call_are_declared(door: SimpleNamespace) -> None:
    assert "approvals.wait" in door.ctx.capabilities
    assert "approvals.call" in door.ctx.capabilities


@pytest.mark.contract
def test_the_badge_query_returns_the_waiting_count(
    door: SimpleNamespace, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, schema: dict
) -> None:
    _seed_call_pause(door, tmp_path, monkeypatch, key="k1")
    token = _token(door)

    resp = handle(
        _req("GET", "/v1/approvals", token=token, query='filter=state%20%3D%20"waiting"&count=true'), ctx=door.ctx
    )

    assert resp.status == 200
    body = _json(resp)
    _validate(schema, body, "ListResponse")
    assert body["count"] == 1
    assert body["data"][0]["state"] == "waiting"
    assert body["data"][0]["kind"] == "call"


@pytest.mark.contract
def test_get_an_unknown_id_is_404(door: SimpleNamespace) -> None:
    resp = handle(_req("GET", "/v1/approvals/appr_" + "0" * 32, token=_token(door)), ctx=door.ctx)
    assert resp.status == 404


@pytest.mark.contract
def test_approve_runs_the_body_and_returns_the_updated_resource(
    door: SimpleNamespace, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, schema: dict
) -> None:
    _seed_call_pause(door, tmp_path, monkeypatch, key="k1")
    token = _token(door)
    listed = _json(handle(_req("GET", "/v1/approvals", token=token), ctx=door.ctx))
    approval_id = listed["data"][0]["id"]

    resp = handle(
        _req(
            "POST",
            f"/v1/approvals/{approval_id}/actions/approve",
            token=token,
            body=b"{}",
            headers={"If-Match": '"1"'},
        ),
        ctx=door.ctx,
    )

    assert resp.status == 200
    body = _json(resp)
    assert body["state"] == "approved"
    assert body["answered_by"] == "console:u1"
    assert conversation_store.load_pause(door.ctx.conns.reader(), conversation_key="k1") is None


@pytest.mark.contract
def test_decline_on_an_already_decided_row_is_409(
    door: SimpleNamespace, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, schema: dict
) -> None:
    _seed_call_pause(door, tmp_path, monkeypatch, key="k1")
    token = _token(door)
    listed = _json(handle(_req("GET", "/v1/approvals", token=token), ctx=door.ctx))
    approval_id = listed["data"][0]["id"]
    approved = handle(
        _req(
            "POST",
            f"/v1/approvals/{approval_id}/actions/approve",
            token=token,
            body=b"{}",
            headers={"If-Match": '"1"'},
        ),
        ctx=door.ctx,
    )
    assert approved.status == 200

    resp = handle(
        _req(
            "POST",
            f"/v1/approvals/{approval_id}/actions/decline",
            token=token,
            body=b"{}",
            headers={"If-Match": '"2"'},
        ),
        ctx=door.ctx,
    )

    assert resp.status == 409
    body = _json(resp)
    _validate(schema, body, "Problem")


@pytest.mark.contract
def test_approve_on_a_wait_kind_row_is_409_not_a_crash(door: SimpleNamespace, schema: dict) -> None:
    """Regression: `act` used to call `resume_paused_run` regardless of the
    row's own `kind`, and `resume_paused_run`'s internal `assert
    (decision == "answer") == (pause.kind == "wait")` raised an
    `AssertionError` router.py's catch-all turned into an opaque `500` —
    `approve`/`decline` only ever make sense against a `call`-kind row, and
    that must be a clean `409`, checked before `resume_paused_run` is ever
    reached. No real plugin needed: the kind check short-circuits first."""
    conversation_store.save_pause(
        door.ctx.conns.writer,
        conversation_key="k-wait",
        plugin="whatever",
        entry="e",
        node="n",
        trace=(),
        artifacts=(),
        kind="wait",
    )
    token = _token(door)
    listed = _json(handle(_req("GET", "/v1/approvals", token=token), ctx=door.ctx))
    approval_id = next(r["id"] for r in listed["data"] if r["conversation_key"] == "k-wait")

    resp = handle(
        _req(
            "POST",
            f"/v1/approvals/{approval_id}/actions/approve",
            token=token,
            body=b"{}",
            headers={"If-Match": '"1"'},
        ),
        ctx=door.ctx,
    )

    assert resp.status == 409
    body = _json(resp)
    _validate(schema, body, "Problem")
    assert "call" in body["detail"]


@pytest.mark.contract
def test_a_stale_if_match_on_decline_is_412(
    door: SimpleNamespace, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, schema: dict
) -> None:
    _seed_call_pause(door, tmp_path, monkeypatch, key="k1")
    token = _token(door)
    listed = _json(handle(_req("GET", "/v1/approvals", token=token), ctx=door.ctx))
    approval_id = listed["data"][0]["id"]

    resp = handle(
        _req(
            "POST",
            f"/v1/approvals/{approval_id}/actions/decline",
            token=token,
            body=b'{"reason": "not today"}',
            headers={"If-Match": '"99"'},
        ),
        ctx=door.ctx,
    )

    assert resp.status == 412
    _validate(schema, _json(resp), "Problem")


@pytest.mark.contract
def test_decline_stops_the_call_node_without_running_its_body(
    door: SimpleNamespace, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed_call_pause(door, tmp_path, monkeypatch, key="k1")
    token = _token(door)
    listed = _json(handle(_req("GET", "/v1/approvals", token=token), ctx=door.ctx))
    approval_id = listed["data"][0]["id"]

    resp = handle(
        _req(
            "POST",
            f"/v1/approvals/{approval_id}/actions/decline",
            token=token,
            body=b'{"reason": "not today"}',
            headers={"If-Match": '"1"'},
        ),
        ctx=door.ctx,
    )

    assert resp.status == 200
    body = _json(resp)
    assert body["state"] == "declined"
    assert body["answer"] == "not today"
