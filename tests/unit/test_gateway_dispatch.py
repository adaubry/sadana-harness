"""Tests for sadana.gateway_dispatch."""

from __future__ import annotations

import asyncio

import pytest

from conftest import plain_response
from sadana import model_access, observability, plugin_dispatch
from sadana.conversation_store import load, open_store, store_path_from_config
from sadana.gateway import MessageEvent
from sadana.gateway_dispatch import handle_inbound

_PLUGIN_SET = plugin_dispatch.PluginSet(catalog=(), tool_specs=(), by_tool={})


@pytest.mark.unit
def test_handle_inbound_continues_the_same_conversation_across_calls(monkeypatch: pytest.MonkeyPatch) -> None:
    responses = iter([plain_response("first reply"), plain_response("second reply")])
    monkeypatch.setattr(model_access, "send", lambda request: next(responses))

    conn = open_store(store_path_from_config())
    event = MessageEvent(platform="webhook", chat_id="chat-1", thread_id=None, text="hello")

    ok1, text1 = asyncio.run(
        handle_inbound(
            conn, event, plugin_set=_PLUGIN_SET, persona="You are a test persona.\n", provider="p", model="m"
        )
    )
    assert ok1 is True
    assert text1 == "first reply"

    ok2, text2 = asyncio.run(
        handle_inbound(
            conn, event, plugin_set=_PLUGIN_SET, persona="You are a test persona.\n", provider="p", model="m"
        )
    )
    assert ok2 is True
    assert text2 == "second reply"

    loaded = load(conn, "webhook:chat-1", now=0.0)
    assert loaded.next_turn_seq == 2


@pytest.mark.unit
def test_handle_inbound_records_the_turn_when_given_a_recorder(monkeypatch: pytest.MonkeyPatch) -> None:
    """OBSERVABILITY-01: `cmd_gateway_run` builds one `Recorder` per
    process and passes its two callables into every `handle_inbound` call
    (see `subcommands/gateway.py`) — this proves that injection actually
    reaches the turn, the same contract `build_dispatch`'s own recording
    tests check at the `plugin_dispatch` layer."""
    monkeypatch.setattr(model_access, "send", lambda request: plain_response("reply"))

    conn = open_store(store_path_from_config())
    recorder = observability.make_recorder(conn)
    event = MessageEvent(platform="webhook", chat_id="chat-2", thread_id=None, text="hello")

    asyncio.run(
        handle_inbound(
            conn,
            event,
            plugin_set=_PLUGIN_SET,
            persona="You are a test persona.\n",
            provider="p",
            model="m",
            record_turn=recorder.record_turn,
            record_plugin_run=recorder.record_plugin_run,
        )
    )

    row = conn.execute(
        "SELECT * FROM turn_runs WHERE conversation_key = ? AND turn_seq = 0", ("webhook:chat-2",)
    ).fetchone()
    assert row is not None
    assert row["exit_reason"] == "completed"
