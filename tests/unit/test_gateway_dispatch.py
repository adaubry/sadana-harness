"""Tests for sadana.gateway_dispatch."""

from __future__ import annotations

import asyncio

import pytest

from conftest import plain_response
from sadana import model_access, plugin_dispatch
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
