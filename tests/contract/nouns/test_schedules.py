"""`schedules`' own conformance proof (H27), against `router.handle()`
directly — no socket, matching `tests/contract/nouns/test_approvals.py`'s
own posture and its reasons for being its own file under
`tests/contract/nouns/` rather than an addition to
`test_console_grammar.py`.
"""

from __future__ import annotations

import dataclasses
import json
import time
from pathlib import Path
from types import SimpleNamespace

import jsonschema
import pytest

from sadana.door import auth
from sadana.door import capabilities as capabilities_module
from sadana.door import request as door_request
from sadana.door.nouns import schedules
from sadana.door.router import DoorContext, handle
from sadana.stores import Connections

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
        nouns={"schedules": schedules},
        clock=time.time,
    )
    return SimpleNamespace(ctx=ctx, private_pem=private_pem, kid="test")


def _token(door: SimpleNamespace, **overrides: object) -> str:
    defaults: dict[str, object] = dict(
        sub="u1",
        org=None,
        ws="ws1",
        hrn=_HARNESS_ID,
        scope=["schedules:read", "schedules:write", "schedules:pause", "schedules:resume"],
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


def _create(door: SimpleNamespace, token: str, **overrides: object) -> door_request.DoorResponse:
    body: dict[str, object] = dict(name="digest", cron="0 9 * * *", trigger_text="say good morning")
    body.update(overrides)
    return handle(_req("POST", "/v1/schedules", token=token, body=json.dumps(body).encode()), ctx=door.ctx)


@pytest.mark.contract
def test_schedules_write_is_declared(tmp_path: Path) -> None:
    door = _door(tmp_path)
    assert "schedules.write" in door.ctx.capabilities


@pytest.mark.contract
def test_create_then_get_shows_next_run_at(tmp_path: Path, schema: dict) -> None:
    door = _door(tmp_path)
    token = _token(door)

    created = _json(_create(door, token))
    assert created["state"] == "active"
    assert created["next_run_at"]

    resp = handle(_req("GET", f"/v1/schedules/{created['id']}", token=token), ctx=door.ctx)
    assert resp.status == 200
    body = _json(resp)
    assert body["name"] == "digest"
    assert body["next_run_at"] == created["next_run_at"]


@pytest.mark.contract
def test_create_get_pause_resume_sequence(tmp_path: Path) -> None:
    door = _door(tmp_path)
    token = _token(door)
    created = _json(_create(door, token))
    schedule_id = created["id"]

    paused = handle(
        _req(
            "POST",
            f"/v1/schedules/{schedule_id}/actions/pause",
            token=token,
            body=b"{}",
            headers={"If-Match": '"1"'},
        ),
        ctx=door.ctx,
    )
    assert paused.status == 200
    paused_body = _json(paused)
    assert paused_body["state"] == "paused"

    resumed = handle(
        _req(
            "POST",
            f"/v1/schedules/{schedule_id}/actions/resume",
            token=token,
            body=b"{}",
            headers={"If-Match": '"2"'},
        ),
        ctx=door.ctx,
    )
    assert resumed.status == 200
    resumed_body = _json(resumed)
    assert resumed_body["state"] == "active"


@pytest.mark.contract
def test_create_with_a_bad_cron_and_bad_timezone_returns_both_errors(tmp_path: Path, schema: dict) -> None:
    door = _door(tmp_path)
    token = _token(door)

    resp = _create(door, token, cron="not a cron", timezone="Nowhere/Imaginary")

    assert resp.status == 400
    body = _json(resp)
    _validate(schema, body, "Problem")
    fields = {e["field"] for e in body["errors"]}
    assert fields == {"cron", "timezone"}


@pytest.mark.contract
def test_another_accounts_schedule_is_404_not_403(tmp_path: Path) -> None:
    door = _door(tmp_path)
    owner_token = _token(door, sub="owner")
    created = _json(_create(door, owner_token))
    other_token = _token(door, sub="someone-else")

    resp = handle(_req("GET", f"/v1/schedules/{created['id']}", token=other_token), ctx=door.ctx)

    assert resp.status == 404


@pytest.mark.contract
@pytest.mark.parametrize(
    ("method", "path_suffix", "body"),
    [
        ("PATCH", "", b'{"trigger_text": "hijacked"}'),
        ("DELETE", "", b""),
        ("POST", "/actions/pause", b"{}"),
        ("POST", "/actions/resume", b"{}"),
    ],
)
def test_another_accounts_schedule_is_404_on_every_mutating_verb(
    tmp_path: Path, method: str, path_suffix: str, body: bytes
) -> None:
    """The plan's own proof item names all five verbs — `GET` alone (above)
    isn't enough to discharge it (a deploy-stage cold review caught the
    gap)."""
    door = _door(tmp_path)
    owner_token = _token(door, sub="owner")
    created = _json(_create(door, owner_token))
    other_token = _token(door, sub="someone-else")

    resp = handle(
        _req(
            method,
            f"/v1/schedules/{created['id']}{path_suffix}",
            token=other_token,
            body=body,
            headers={"If-Match": '"1"'},
        ),
        ctx=door.ctx,
    )

    assert resp.status == 404


@pytest.mark.contract
def test_create_without_schedules_write_is_capability_missing(tmp_path: Path) -> None:
    door = _door(tmp_path, capabilities=("grammar.v1", "changes", "inventory"))
    token = _token(door)

    resp = _create(door, token)

    assert resp.status == 501


@pytest.mark.contract
@pytest.mark.parametrize(
    ("method", "path_suffix", "body"),
    [
        ("PATCH", "", b'{"trigger_text": "x"}'),
        ("POST", "/actions/pause", b"{}"),
    ],
)
def test_update_and_pause_without_schedules_write_are_capability_missing(
    tmp_path: Path, method: str, path_suffix: str, body: bytes
) -> None:
    """`create` alone (above) doesn't discharge the plan's own proof item,
    which names `create`/`update`/`pause`/`resume` (a deploy-stage cold
    review caught the gap). A schedule is created through a full-capability
    door, then acted on through a second context sharing the same store but
    missing `schedules.write`. `router.py` checks state before capability
    (requirement 25), so this only proves the gate for an action whose
    state precondition the fresh, `active` row already satisfies — `pause`
    does; `resume` (state `paused`) needs its own setup below."""
    door = _door(tmp_path)
    token = _token(door)
    created = _json(_create(door, token))
    restricted_ctx = dataclasses.replace(door.ctx, capabilities=("grammar.v1", "changes", "inventory"))

    resp = handle(
        _req(
            method,
            f"/v1/schedules/{created['id']}{path_suffix}",
            token=token,
            body=body,
            headers={"If-Match": '"1"'},
        ),
        ctx=restricted_ctx,
    )

    assert resp.status == 501


@pytest.mark.contract
def test_resume_without_schedules_write_is_capability_missing(tmp_path: Path) -> None:
    door = _door(tmp_path)
    token = _token(door)
    created = _json(_create(door, token))
    handle(
        _req(
            "POST",
            f"/v1/schedules/{created['id']}/actions/pause",
            token=token,
            body=b"{}",
            headers={"If-Match": '"1"'},
        ),
        ctx=door.ctx,
    )
    restricted_ctx = dataclasses.replace(door.ctx, capabilities=("grammar.v1", "changes", "inventory"))

    resp = handle(
        _req(
            "POST",
            f"/v1/schedules/{created['id']}/actions/resume",
            token=token,
            body=b"{}",
            headers={"If-Match": '"2"'},
        ),
        ctx=restricted_ctx,
    )

    assert resp.status == 501


@pytest.mark.contract
def test_update_with_a_name_collision_is_409_not_500(tmp_path: Path) -> None:
    """`create` already reports a duplicate `name` as `409`; `update`'s own
    `UPDATE ... SET name = ?` hits the identical constraint and, before this
    fix, escaped as an uncaught `500` (a deploy-stage cold review caught
    this by reproducing it)."""
    door = _door(tmp_path)
    token = _token(door)
    _json(_create(door, token, name="taken"))
    other = _json(_create(door, token, name="renaming-me"))

    resp = handle(
        _req(
            "PATCH",
            f"/v1/schedules/{other['id']}",
            token=token,
            body=b'{"name": "taken"}',
            headers={"If-Match": '"1"'},
        ),
        ctx=door.ctx,
    )

    assert resp.status == 409


@pytest.mark.contract
def test_list_filters_by_state(tmp_path: Path) -> None:
    door = _door(tmp_path)
    token = _token(door)
    active = _json(_create(door, token, name="stays-active"))
    to_pause = _json(_create(door, token, name="gets-paused"))
    handle(
        _req(
            "POST",
            f"/v1/schedules/{to_pause['id']}/actions/pause",
            token=token,
            body=b"{}",
            headers={"If-Match": '"1"'},
        ),
        ctx=door.ctx,
    )

    resp = handle(_req("GET", "/v1/schedules", token=token, query='filter=state%20%3D%20"paused"'), ctx=door.ctx)

    assert resp.status == 200
    body = _json(resp)
    assert [r["id"] for r in body["data"]] == [to_pause["id"]]
    assert active["id"] not in [r["id"] for r in body["data"]]
