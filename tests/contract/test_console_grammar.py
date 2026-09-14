"""The door's own conformance proof: every behaviour spec.md's Acceptance
criteria names, proven against `router.handle()` directly -- no socket,
which is what makes this a statement about the contract rather than about a
transport (spec.md requirement 52).

Marked `contract` (`pyproject.toml`'s marker, declared and unused until this
work item). Exercises the `harness` noun and a `widgets` noun the test
registers itself (`fixture_noun.WidgetsNoun`), so the framework is proven
independently of any real noun besides `harness` -- H20 and later add their
own nouns to this same file.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from types import SimpleNamespace

import jsonschema
import pytest
from fixture_noun import WidgetsNoun

from sadana import stores
from sadana.door import auth
from sadana.door import capabilities as capabilities_module
from sadana.door import request as door_request
from sadana.door.nouns import harness
from sadana.door.router import DoorContext, handle

_HARNESS_ID = "hrn_test"
_SCHEMA_PATH = Path(__file__).resolve().parents[2] / "docs" / "console" / "grammar.json"


@pytest.fixture(scope="module")
def schema() -> dict:
    """Every test that validates a response depends on this. A missing
    schema file must fail loudly, never quietly skip the conformance proof
    it exists to give (spec.md requirement 52)."""
    if not _SCHEMA_PATH.exists():
        pytest.fail("docs/console/grammar.json is missing")
    return json.loads(_SCHEMA_PATH.read_text(encoding="utf-8"))


def _validate(schema: dict, instance: object, def_name: str) -> None:
    ref_schema = {"$ref": f"#/$defs/{def_name}", **{k: v for k, v in schema.items() if k != "$comment"}}
    jsonschema.Draft202012Validator(ref_schema).validate(instance)


@pytest.mark.contract
def test_the_schema_file_exists_and_is_never_skipped(schema: dict) -> None:
    assert "$defs" in schema


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
        nouns={"harness": harness, "widgets": WidgetsNoun(harness_id=_HARNESS_ID)},
        clock=time.time,
    )
    return SimpleNamespace(ctx=ctx, private_pem=private_pem, kid="test")


def _token(door: SimpleNamespace, **overrides: object) -> str:
    defaults: dict[str, object] = dict(
        sub="u1", org=None, ws="ws1", hrn=_HARNESS_ID, scope=["widgets:read", "widgets:write", "widgets:archive"]
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


# ── fixed paths ──────────────────────────────────────────────────────────
@pytest.mark.contract
def test_get_harness(door: SimpleNamespace) -> None:
    resp = handle(_req("GET", "/v1/harness", token=_token(door)), ctx=door.ctx)
    assert resp.status == 200
    body = _json(resp)
    assert body["id"] == _HARNESS_ID
    assert body["capabilities"] == ["grammar.v1", "changes", "inventory"]
    assert body["leaves_the_box"] == ["harness", "widgets"]


@pytest.mark.contract
def test_get_changes_and_inventory(door: SimpleNamespace, schema: dict) -> None:
    resp = handle(_req("GET", "/v1/changes", token=_token(door), query="since=0&limit=10"), ctx=door.ctx)
    assert resp.status == 200
    assert _json(resp)["data"] == []

    resp = handle(_req("GET", "/v1/inventory", token=_token(door)), ctx=door.ctx)
    assert resp.status == 200
    assert "widgets" not in _json(resp)  # not a ledgered noun in H19


@pytest.mark.contract
def test_changes_events_validate_against_resource_changed_event(door: SimpleNamespace, schema: dict) -> None:
    from sadana import ledger
    from sadana.conversation_store import write_txn

    with write_txn(door.ctx.conns.writer) as c:
        ledger.record_change(
            c, noun="operations", id="op_" + "0" * 32, kind="created", state="running", version=1, at=1.0
        )
        ledger.record_change(
            c, noun="operations", id="op_" + "1" * 32, kind="deleted", state=None, version=None, at=2.0
        )

    resp = handle(_req("GET", "/v1/changes", token=_token(door), query="since=0&limit=10"), ctx=door.ctx)
    events = _json(resp)["data"]
    assert len(events) == 2
    for event in events:
        _validate(schema, event, "ResourceChangedEvent")
    assert events[0]["kind"] == "changed"  # created folded to changed
    assert events[1]["kind"] == "deleted"
    assert "search_doc" not in events[1]


@pytest.mark.contract
def test_unknown_plural_is_404(door: SimpleNamespace) -> None:
    resp = handle(_req("GET", "/v1/gadgets", token=_token(door)), ctx=door.ctx)
    assert resp.status == 404


@pytest.mark.contract
def test_a_fifth_list_param_is_400_naming_it(door: SimpleNamespace, schema: dict) -> None:
    resp = handle(_req("GET", "/v1/widgets", token=_token(door), query="bogus=1"), ctx=door.ctx)
    assert resp.status == 400
    body = _json(resp)
    _validate(schema, body, "Problem")
    assert "bogus" in body["detail"]


@pytest.mark.contract
def test_an_undeclared_action_is_501_naming_it(door: SimpleNamespace, schema: dict) -> None:
    token = _token(door)
    widget = _json(handle(_req("POST", "/v1/widgets", token=token, body=b"{}"), ctx=door.ctx))

    resp = handle(
        _req("POST", f"/v1/widgets/{widget['id']}/actions/teleport", token=token, headers={"If-Match": '"1"'}),
        ctx=door.ctx,
    )
    assert resp.status == 501
    body = _json(resp)
    _validate(schema, body, "Problem")
    assert "teleport" in body["detail"]


# ── widgets: the full CRUD + action lifecycle ───────────────────────────
@pytest.mark.contract
def test_create_get_update_archive_remove(door: SimpleNamespace, schema: dict) -> None:
    token = _token(door)

    created = handle(_req("POST", "/v1/widgets", token=token, body=b'{"name": "w1"}'), ctx=door.ctx)
    assert created.status == 201
    widget = _json(created)
    _validate(schema, widget, "StandardFields")
    assert widget["name"] == "w1"
    assert widget["state"] == "draft"
    assert created.headers["ETag"] == '"1"'

    fetched = handle(_req("GET", f"/v1/widgets/{widget['id']}", token=token), ctx=door.ctx)
    assert fetched.status == 200
    assert fetched.headers["ETag"] == '"1"'

    updated = handle(
        _req(
            "PATCH",
            f"/v1/widgets/{widget['id']}",
            token=token,
            body=b'{"name": "w1-renamed"}',
            headers={"If-Match": '"1"'},
        ),
        ctx=door.ctx,
    )
    assert updated.status == 200
    assert _json(updated)["name"] == "w1-renamed"
    assert updated.headers["ETag"] == '"2"'

    # Archiving from "draft" (not "active") is a state-gate conflict.
    conflict = handle(
        _req("POST", f"/v1/widgets/{widget['id']}/actions/archive", token=token, headers={"If-Match": '"2"'}),
        ctx=door.ctx,
    )
    assert conflict.status == 409
    _validate(schema, _json(conflict), "Problem")
    assert "requires state" in _json(conflict)["detail"]

    removed = handle(
        _req("DELETE", f"/v1/widgets/{widget['id']}", token=token, headers={"If-Match": '"2"'}), ctx=door.ctx
    )
    assert removed.status == 204

    gone = handle(_req("GET", f"/v1/widgets/{widget['id']}", token=token), ctx=door.ctx)
    assert gone.status == 404


@pytest.mark.contract
def test_stale_if_match_is_412_naming_both_versions(door: SimpleNamespace, schema: dict) -> None:
    token = _token(door)
    widget = _json(handle(_req("POST", "/v1/widgets", token=token, body=b"{}"), ctx=door.ctx))

    resp = handle(
        _req("PATCH", f"/v1/widgets/{widget['id']}", token=token, body=b"{}", headers={"If-Match": '"99"'}),
        ctx=door.ctx,
    )
    assert resp.status == 412
    body = _json(resp)
    _validate(schema, body, "Problem")
    detail = body["detail"]
    assert "1" in detail and "99" in detail


@pytest.mark.contract
def test_missing_if_match_is_412(door: SimpleNamespace) -> None:
    token = _token(door)
    widget = _json(handle(_req("POST", "/v1/widgets", token=token, body=b"{}"), ctx=door.ctx))

    resp = handle(_req("PATCH", f"/v1/widgets/{widget['id']}", token=token, body=b"{}"), ctx=door.ctx)
    assert resp.status == 412


@pytest.mark.contract
def test_missing_scope_is_403_naming_it(door: SimpleNamespace, schema: dict) -> None:
    token = _token(door, scope=["widgets:read"])
    resp = handle(_req("POST", "/v1/widgets", token=token, body=b"{}"), ctx=door.ctx)
    assert resp.status == 403
    body = _json(resp)
    _validate(schema, body, "Problem")
    assert "widgets:write" in body["detail"]


@pytest.mark.contract
def test_unknown_id_and_another_accounts_id_are_both_404(door: SimpleNamespace) -> None:
    token_a = _token(door, sub="account_a")
    token_b = _token(door, sub="account_b")
    widget = _json(handle(_req("POST", "/v1/widgets", token=token_a, body=b"{}"), ctx=door.ctx))

    unknown = handle(_req("GET", "/v1/widgets/wgt_00000000000000000000000000000000", token=token_a), ctx=door.ctx)
    assert unknown.status == 404

    someone_elses = handle(_req("GET", f"/v1/widgets/{widget['id']}", token=token_b), ctx=door.ctx)
    assert someone_elses.status == 404


# ── idempotency ──────────────────────────────────────────────────────────
@pytest.mark.contract
def test_replayed_post_returns_the_identical_response_and_creates_nothing(door: SimpleNamespace, schema: dict) -> None:
    token = _token(door)
    body = b'{"name": "once"}'
    headers = {"Idempotency-Key": "idem-1"}

    first = handle(_req("POST", "/v1/widgets", token=token, body=body, headers=headers), ctx=door.ctx)
    second = handle(_req("POST", "/v1/widgets", token=token, body=body, headers=headers), ctx=door.ctx)

    assert second.status == first.status
    assert second.body == first.body

    listing = _json(handle(_req("GET", "/v1/widgets", token=token), ctx=door.ctx))
    _validate(schema, listing, "ListResponse")
    assert len(listing["data"]) == 1


@pytest.mark.contract
def test_replayed_post_with_a_different_body_is_422(door: SimpleNamespace, schema: dict) -> None:
    token = _token(door)
    headers = {"Idempotency-Key": "idem-2"}
    handle(_req("POST", "/v1/widgets", token=token, body=b'{"name": "a"}', headers=headers), ctx=door.ctx)

    resp = handle(_req("POST", "/v1/widgets", token=token, body=b'{"name": "b"}', headers=headers), ctx=door.ctx)
    assert resp.status == 422
    _validate(schema, _json(resp), "Problem")


# ── auth refusals, through the full pipeline ────────────────────────────
@pytest.mark.contract
def test_no_token_is_401(door: SimpleNamespace, schema: dict) -> None:
    resp = handle(_req("GET", "/v1/harness", token=None), ctx=door.ctx)
    assert resp.status == 401
    _validate(schema, _json(resp), "Problem")


@pytest.mark.contract
def test_expired_token_is_401(door: SimpleNamespace) -> None:
    token = _token(door, ttl=10, now=1_000_000.0)
    resp = handle(_req("GET", "/v1/harness", token=token), ctx=door.ctx)
    assert resp.status == 401


@pytest.mark.contract
def test_wrong_hrn_is_403(door: SimpleNamespace, schema: dict) -> None:
    token = _token(door, hrn="hrn_other")
    resp = handle(_req("GET", "/v1/harness", token=token, headers={"X-Sadana-Harness": "hrn_other"}), ctx=door.ctx)
    assert resp.status == 403
    _validate(schema, _json(resp), "Problem")


# ── operations: the 202 promotion path ──────────────────────────────────
@pytest.mark.contract
def test_a_slow_create_promotes_to_an_operation_then_completes(
    door: SimpleNamespace, schema: dict, monkeypatch
) -> None:
    import sadana.door.router as router_module

    monkeypatch.setattr(router_module, "_OPERATION_TIMEOUT_SECONDS", 0.02)
    slow_ctx = DoorContext(
        conns=door.ctx.conns,
        runtime=door.ctx.runtime,
        verifier=door.ctx.verifier,
        capabilities=door.ctx.capabilities,
        nouns={"harness": harness, "widgets": WidgetsNoun(harness_id=_HARNESS_ID, delay_seconds=0.1)},
        clock=time.time,
    )
    token = _token(door)

    resp = handle(_req("POST", "/v1/widgets", token=token, body=b"{}"), ctx=slow_ctx)
    assert resp.status == 202
    op = _json(resp)["operation"]
    _validate(schema, op, "Operation")
    assert op["state"] == "running"

    deadline = time.monotonic() + 2.0
    while time.monotonic() < deadline:
        poll = handle(_req("GET", f"/v1/operations/{op['id']}", token=token), ctx=slow_ctx)
        if _json(poll)["operation"]["state"] != "running":
            break
        time.sleep(0.02)
    else:
        pytest.fail("operation never left the running state")

    assert _json(poll)["operation"]["state"] == "succeeded"
