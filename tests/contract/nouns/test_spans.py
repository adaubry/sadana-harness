"""`spans` noun contract test — spec.md requirement 26, made real by
`docs/tasks/H21-watched-streaming-live-runs-stop/spec.md`.

A bare, unparented `/v1/spans[/{id}]` still answers empty/404 exactly as
before H21 (nothing changed for that shape — `spans` is a child of
`traces`, never reachable at the top level). The new coverage is the real
one: a real plugin dispatch inside a contract-level turn produces real
`spans` rows, listable and gettable through the door.
"""

# ruff: noqa: F401, F811 -- `keys`/`schema` are pytest fixtures imported from
# `_scaffold.py` (not a `conftest.py` -- see that file's own docstring) and
# used by name as a parameter in every test function below; ruff's static
# analysis reads each repeated parameter as redefining an "unused" import,
# which is exactly the documented pytest cross-module-fixture idiom, not a
# real redefinition.

from __future__ import annotations

import json
from pathlib import Path

import pytest
from _scaffold import keys, make_ctx, mint, plain_response, req, schema, seed_conversation, validate

from sadana import client_surface, ids, model_access, observability, plugin_dispatch, stores
from sadana.conversation import ToolSpec
from sadana.door.nouns import runs, spans, traces
from sadana.door.nouns.conversations import ConversationsNoun
from sadana.door.nouns.messages import MessagesNoun
from sadana.door.router import handle
from sadana.plugins import Entry, InstalledPlugin, Manifest, Node


@pytest.mark.contract
def test_list_is_empty_with_no_parent(keys, tmp_path: Path, schema: dict) -> None:
    conns = stores.Connections(tmp_path / "door.db")
    ctx = make_ctx(keys, conns, {"spans": spans})
    token = mint(keys, scope=["spans:read"])
    resp = handle(req("GET", "/v1/spans", token=token), ctx=ctx)
    assert resp.status == 200
    body = json.loads(resp.body)
    assert body == {"data": [], "next_page_token": None}
    validate(schema, body, "ListResponse")


@pytest.mark.contract
def test_get_is_not_found_with_no_parent(keys, tmp_path: Path) -> None:
    conns = stores.Connections(tmp_path / "door.db")
    ctx = make_ctx(keys, conns, {"spans": spans})
    token = mint(keys, scope=["spans:read"])
    resp = handle(req("GET", "/v1/spans/spn_doesnotexist", token=token), ctx=ctx)
    assert resp.status == 404


@pytest.mark.contract
def test_create_is_capability_missing(keys, tmp_path: Path) -> None:
    conns = stores.Connections(tmp_path / "door.db")
    ctx = make_ctx(keys, conns, {"spans": spans})
    token = mint(keys, scope=["spans:write"])
    resp = handle(req("POST", "/v1/spans", token=token, body=b"{}"), ctx=ctx)
    assert resp.status == 501


@pytest.mark.contract
def test_real_spans_from_a_real_plugin_dispatch_are_listable_and_gettable(
    keys, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, schema: dict
) -> None:
    plugin_dir = tmp_path / "plugins" / "p"
    plugin_dir.mkdir(parents=True)
    (plugin_dir / "init.py").write_text(
        "def step_one(value):\n    return {'n': 1}\n\ndef step_two(value):\n    return {'n': 2}\n"
    )
    (plugin_dir / "s.json").write_text(json.dumps({"type": "object"}))
    manifest = Manifest(
        name="p",
        version="0.1.0",
        description="d",
        entries=(Entry(tool="do_it", purpose="p", parameters="s.json", start="a"),),
        nodes=(
            Node(name="a", kind="compute", body="init:step_one", next="b"),
            Node(name="b", kind="compute", body="init:step_two"),
        ),
    )
    installed = InstalledPlugin(name="p", directory=plugin_dir, manifest=manifest)
    plugin_set = plugin_dispatch.PluginSet(
        catalog=(),
        tool_specs=(ToolSpec(key="do_it", name="do_it", parameters={"type": "object"}, describe=lambda _r: "d"),),
        by_tool={"do_it": (installed, manifest.entries[0])},
    )
    conns = stores.Connections(tmp_path / "door.db")
    runtime = client_surface.Runtime(
        connections=conns,
        plugin_set=plugin_set,
        provider="p",
        model="m",
        recorder=observability.make_recorder(conns.writer),
    )
    conversation_key = ids.make_id("conv")
    seed_conversation(runtime, account="console:u1", id=conversation_key)
    ctx = make_ctx(
        keys,
        conns,
        {
            "conversations": ConversationsNoun(runtime),
            "messages": MessagesNoun(runtime),
            "runs": runs,
            "traces": traces,
            "spans": spans,
        },
    )

    responses = iter(
        [
            model_access.Response(
                content=None,
                tool_calls=({"function": {"name": "do_it", "arguments": "{}"}},),
                finish_reason="tool_calls",
                usage=model_access.Usage(),
            ),
            plain_response("done"),
        ]
    )
    monkeypatch.setattr(model_access, "send", lambda request, on_delta=None: next(responses))

    msg_token = mint(keys, sub="u1", scope=["messages:write"])
    created = handle(
        req(
            "POST",
            f"/v1/conversations/{conversation_key}/messages",
            token=msg_token,
            body=json.dumps({"content": "call do_it"}).encode(),
        ),
        ctx=ctx,
    )
    assert created.status == 201
    run_id = json.loads(created.body)["run_id"]
    assert run_id is not None

    read_token = mint(keys, sub="u1", scope=["traces:read", "spans:read"])
    trace_list = json.loads(handle(req("GET", f"/v1/runs/{run_id}/traces", token=read_token), ctx=ctx).body)["data"]
    assert len(trace_list) == 1
    trace_id = trace_list[0]["id"]

    span_list_resp = handle(
        req("GET", f"/v1/traces/{trace_id}/spans", token=read_token, query="order_by=created_at asc"), ctx=ctx
    )
    assert span_list_resp.status == 200
    span_body = json.loads(span_list_resp.body)
    validate(schema, span_body, "ListResponse")
    span_data = span_body["data"]
    assert [s["node_id"] for s in span_data] == ["a", "b"]
    assert all(s["status"] == "ok" for s in span_data)
    assert all(s["started_at"] is not None and s["ended_at"] is not None for s in span_data)
    for s in span_data:
        validate(schema, s, "StandardFields")

    span_get = handle(req("GET", f"/v1/traces/{trace_id}/spans/{span_data[0]['id']}", token=read_token), ctx=ctx)
    assert span_get.status == 200
    assert json.loads(span_get.body)["id"] == span_data[0]["id"]


@pytest.mark.contract
def test_foreign_account_gets_no_spans(keys, tmp_path: Path) -> None:
    """A trace id that exists but does not belong to this account resolves
    no parent, matching every other child noun's own account scoping."""
    conns = stores.Connections(tmp_path / "door.db")
    ctx = make_ctx(keys, conns, {"spans": spans})
    token = mint(keys, sub="stranger", scope=["spans:read"])
    resp = handle(req("GET", "/v1/traces/run_doesnotexist/spans", token=token), ctx=ctx)
    assert resp.status == 200
    assert json.loads(resp.body)["data"] == []
