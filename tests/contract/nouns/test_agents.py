"""`agents`' own conformance proof (H26), against `router.handle()` directly
— matching `tests/contract/test_console_grammar.py`'s own posture and the
per-noun-file precedent `test_approvals.py` set.
"""

from __future__ import annotations

import dataclasses
import json
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from conftest import conversation as build_conversation
from sadana import conversation_store, persona, persona_store, stores
from sadana.conversation import turn_prompt_hash
from sadana.door import auth
from sadana.door import capabilities as capabilities_module
from sadana.door import request as door_request
from sadana.door.nouns import agent_templates, agents, harness
from sadana.door.router import DoorContext, handle

_HARNESS_ID = "hrn_test"


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
        nouns={"harness": harness, "agents": agents, "agent_templates": agent_templates},
        clock=time.time,
    )
    return SimpleNamespace(ctx=ctx, private_pem=private_pem, kid="test")


def _token(door: SimpleNamespace, **overrides: object) -> str:
    defaults: dict[str, object] = dict(
        sub="u1",
        org=None,
        ws="ws1",
        hrn=_HARNESS_ID,
        scope=["agents:read", "agents:write", "agents:activate", "agents:set-default", "agent_templates:read"],
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
def test_create_starts_as_draft_and_appears_in_list_characters(door: SimpleNamespace) -> None:
    token = _token(door)
    created = _json(
        handle(
            _req(
                "POST",
                "/v1/agents",
                token=token,
                body=json.dumps({"name": "helper", "system_prompt": "---\ndescription: x\n---\nBe helpful."}).encode(),
            ),
            ctx=door.ctx,
        )
    )
    assert created["state"] == "draft"
    assert created["is_default"] is False
    assert [c.name for c in persona_store.list_characters(persona_store.characters_dir_from_config())] == ["helper"]


@pytest.mark.contract
def test_activate_then_set_default_changes_resolve_voice_for_this_account_only(door: SimpleNamespace) -> None:
    token = _token(door)
    created = _json(
        handle(
            _req(
                "POST",
                "/v1/agents",
                token=token,
                body=json.dumps({"name": "helper", "system_prompt": "---\ndescription: x\n---\nBe helpful."}).encode(),
            ),
            ctx=door.ctx,
        )
    )

    activated = _json(
        handle(
            _req(
                "POST",
                f"/v1/agents/{created['id']}/actions/activate",
                token=token,
                headers={"If-Match": f'"{created["version"]}"'},
            ),
            ctx=door.ctx,
        )
    )
    assert activated["state"] == "active"

    defaulted = _json(
        handle(
            _req(
                "POST",
                f"/v1/agents/{created['id']}/actions/set-default",
                token=token,
                headers={"If-Match": f'"{activated["version"]}"'},
            ),
            ctx=door.ctx,
        )
    )
    assert defaulted["is_default"] is True

    characters_dir = persona_store.characters_dir_from_config()
    reader = door.ctx.conns.reader()
    assert persona_store.resolve_voice(reader, "console:u1", characters_dir) == persona_store.resolve_voice(
        reader, "console:u1", characters_dir
    )
    assert "Be helpful." in persona_store.resolve_voice(reader, "console:u1", characters_dir)
    # A different account never sees this account's default.
    assert persona_store.resolve_voice(reader, "console:someone_else", characters_dir) != persona_store.resolve_voice(
        reader, "console:u1", characters_dir
    )


@pytest.mark.contract
def test_activate_requires_draft_state(door: SimpleNamespace) -> None:
    token = _token(door)
    created = _json(
        handle(
            _req("POST", "/v1/agents", token=token, body=json.dumps({"name": "helper", "system_prompt": "x"}).encode()),
            ctx=door.ctx,
        )
    )
    handle(
        _req(
            "POST",
            f"/v1/agents/{created['id']}/actions/activate",
            token=token,
            headers={"If-Match": f'"{created["version"]}"'},
        ),
        ctx=door.ctx,
    )

    conflict = handle(
        _req(
            "POST",
            f"/v1/agents/{created['id']}/actions/activate",
            token=token,
            headers={"If-Match": '"2"'},
        ),
        ctx=door.ctx,
    )
    assert conflict.status == 409


@pytest.mark.contract
def test_remove_is_capability_missing(door: SimpleNamespace) -> None:
    token = _token(door)
    created = _json(
        handle(
            _req("POST", "/v1/agents", token=token, body=json.dumps({"name": "helper", "system_prompt": "x"}).encode()),
            ctx=door.ctx,
        )
    )
    resp = handle(
        _req("DELETE", f"/v1/agents/{created['id']}", token=token, headers={"If-Match": f'"{created["version"]}"'}),
        ctx=door.ctx,
    )
    assert resp.status == 501


@pytest.mark.contract
def test_create_with_a_known_template_id_records_it(door: SimpleNamespace) -> None:
    token = _token(door)
    templates = _json(handle(_req("GET", "/v1/agent_templates", token=token), ctx=door.ctx))["data"]
    default_template_id = templates[0]["id"]

    created = _json(
        handle(
            _req(
                "POST",
                "/v1/agents",
                token=token,
                body=json.dumps({"name": "helper", "system_prompt": "x", "template_id": default_template_id}).encode(),
            ),
            ctx=door.ctx,
        )
    )
    assert created["template_id"] == default_template_id


@pytest.mark.contract
def test_create_with_an_unknown_template_id_is_400(door: SimpleNamespace) -> None:
    token = _token(door)
    resp = handle(
        _req(
            "POST",
            "/v1/agents",
            token=token,
            body=json.dumps({"name": "helper", "system_prompt": "x", "template_id": "tmpl_bogus"}).encode(),
        ),
        ctx=door.ctx,
    )
    assert resp.status == 400


@pytest.mark.contract
def test_editing_system_prompt_leaves_an_already_created_conversation_untouched(door: SimpleNamespace) -> None:
    """The intent's own byte-stability constraint: this module never touches
    `conversation_store`, so a conversation created before an edit keeps its
    prompt — proven directly rather than assumed."""
    characters_dir = persona_store.characters_dir_from_config()
    persona_store.create_character(
        door.ctx.conns.writer, characters_dir, "helper", "---\ndescription: x\n---\nOriginal voice."
    )
    voice = persona_store.resolve_voice(door.ctx.conns.reader(), "console:u1", characters_dir)
    assert voice == persona.NEUTRAL_VOICE  # not selected yet — a plain, known value to snapshot

    convo = build_conversation(key="c1")
    convo = dataclasses.replace(
        convo,
        system_prompt=voice,
        prompt_sha256=turn_prompt_hash(voice, convo.tool_surface),
        stable_prompt_len=len(voice),
    )
    conversation_store.create(
        door.ctx.conns.writer,
        convo,
        now=0.0,
        account_key="console:u1",  # pragma: allowlist secret
    )

    token = _token(door)
    agent_id = persona_store.list_agent_rows(door.ctx.conns.reader())[0].id
    activated = _json(
        handle(
            _req("POST", f"/v1/agents/{agent_id}/actions/activate", token=token, headers={"If-Match": '"1"'}),
            ctx=door.ctx,
        )
    )
    handle(
        _req(
            "PATCH",
            f"/v1/agents/{agent_id}",
            token=token,
            body=json.dumps({"system_prompt": "---\ndescription: x\n---\nA completely different voice."}).encode(),
            headers={"If-Match": f'"{activated["version"]}"'},
        ),
        ctx=door.ctx,
    )

    reloaded = conversation_store.load(door.ctx.conns.reader(), "c1", now=0.0)
    assert reloaded.system_prompt == voice
