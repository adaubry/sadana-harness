"""Tests for sadana.gateway_dispatch."""

from __future__ import annotations

import asyncio

import pytest

from conftest import plain_response
from sadana import memory_store, model_access, observability, plugin_dispatch
from sadana.conversation_store import load, open_store, store_path_from_config
from sadana.gateway import MessageEvent
from sadana.gateway_dispatch import handle_inbound

_PLUGIN_SET = plugin_dispatch.PluginSet(catalog=(), tool_specs=(), by_tool={})


@pytest.mark.unit
def test_handle_inbound_continues_the_same_conversation_across_calls(monkeypatch: pytest.MonkeyPatch) -> None:
    responses = iter([plain_response("first reply"), plain_response("second reply")])
    monkeypatch.setattr(model_access, "send", lambda request: next(responses))

    conn = open_store(store_path_from_config())
    memory_store.ensure_schema(conn)  # cmd_gateway_run's own one-time setup, done here for a direct call
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
    memory_store.ensure_schema(conn)
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


# ── MEMORY-01: account scoping is by chat_id, not by the full session key ──


@pytest.mark.unit
def test_handle_inbound_shares_recall_across_threads_of_the_same_account(monkeypatch: pytest.MonkeyPatch) -> None:
    """Two different `thread_id`s under the same `chat_id` are two different
    conversations (`session_key_for` includes the thread id) but the same
    account (`memory.account_key_for` does not) — both must recall the same
    stored entry."""
    monkeypatch.setattr(model_access, "send", lambda request: plain_response("reply"))
    conn = open_store(store_path_from_config())
    memory_store.ensure_schema(conn)
    memory_store.write_entry(conn, "webhook:chat-3", "dog_name", "Their dog is named Buddy.", now=0.0)

    for thread_id in ("thread-a", "thread-b"):
        event = MessageEvent(platform="webhook", chat_id="chat-3", thread_id=thread_id, text="hi")
        asyncio.run(
            handle_inbound(
                conn, event, plugin_set=_PLUGIN_SET, persona="You are a test persona.\n", provider="p", model="m"
            )
        )
        loaded = load(conn, f"webhook:chat-3:{thread_id}", now=0.0)
        assert "Their dog is named Buddy." in loaded.system_prompt


@pytest.mark.unit
def test_handle_inbound_never_recalls_a_different_chat_ids_memories(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(model_access, "send", lambda request: plain_response("reply"))
    conn = open_store(store_path_from_config())
    memory_store.ensure_schema(conn)
    memory_store.write_entry(conn, "webhook:chat-4", "dog_name", "Their dog is named Buddy.", now=0.0)

    event = MessageEvent(platform="webhook", chat_id="chat-5", thread_id=None, text="hi")
    asyncio.run(
        handle_inbound(
            conn, event, plugin_set=_PLUGIN_SET, persona="You are a test persona.\n", provider="p", model="m"
        )
    )

    loaded = load(conn, "webhook:chat-5", now=0.0)
    assert "Their dog is named Buddy." not in loaded.system_prompt
