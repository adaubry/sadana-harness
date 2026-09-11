"""Tests for sadana.gateway_dispatch."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from conftest import make_runtime, plain_response
from conftest import wait_then_summarize_installed as _wait_then_summarize_installed
from sadana import channel_webhook, memory_store, model_access, plugin_dispatch, plugins
from sadana.conversation import ConversationTemplate, IterationBudget, TemplateRecipe, create_conversation
from sadana.conversation_store import (
    accounts_with_conversations,
    create,
    load,
    load_pause,
    open_store,
    save_pause,
    store_path_from_config,
)
from sadana.gateway import MessageEvent
from sadana.gateway_dispatch import handle_inbound

_SECRET = "s3cr3t"  # pragma: allowlist secret - a fixed test fixture value, not a real credential


@pytest.mark.unit
def test_handle_inbound_continues_the_same_conversation_across_calls(monkeypatch: pytest.MonkeyPatch) -> None:
    responses = iter([plain_response("first reply"), plain_response("second reply")])
    monkeypatch.setattr(model_access, "send", lambda request: next(responses))

    conn = open_store(store_path_from_config())
    event = MessageEvent(platform="webhook", chat_id="chat-1", thread_id=None, text="hello")

    ok1, text1 = asyncio.run(handle_inbound(make_runtime(conn), event))
    assert ok1 is True
    assert text1 == "first reply"

    ok2, text2 = asyncio.run(handle_inbound(make_runtime(conn), event))
    assert ok2 is True
    assert text2 == "second reply"

    loaded = load(conn, "webhook:chat-1", now=0.0)
    assert loaded.next_turn_seq == 2


@pytest.mark.unit
def test_handle_inbound_records_the_turn(monkeypatch: pytest.MonkeyPatch) -> None:
    """OBSERVABILITY-01: one `Recorder` per process reaches every turn. It
    used to be two callables `cmd_gateway_run` threaded into each
    `handle_inbound` call; since CLIENT-SURFACE-01 the recorder is a field on
    the `Runtime` that `client_surface.open_runtime()` assembles once, so
    recording is no longer something a caller can forget to pass. What this
    proves is unchanged — that the injection actually reaches the turn, the
    same contract `build_dispatch`'s own recording tests check at the
    `plugin_dispatch` layer."""
    monkeypatch.setattr(model_access, "send", lambda request: plain_response("reply"))

    conn = open_store(store_path_from_config())
    event = MessageEvent(platform="webhook", chat_id="chat-2", thread_id=None, text="hello")

    asyncio.run(handle_inbound(make_runtime(conn), event))

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
        asyncio.run(handle_inbound(make_runtime(conn), event))
        loaded = load(conn, f"webhook:chat-3:{thread_id}", now=0.0)
        assert "Their dog is named Buddy." in loaded.system_prompt


@pytest.mark.unit
def test_handle_inbound_never_recalls_a_different_chat_ids_memories(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(model_access, "send", lambda request: plain_response("reply"))
    conn = open_store(store_path_from_config())
    memory_store.ensure_schema(conn)
    memory_store.write_entry(conn, "webhook:chat-4", "dog_name", "Their dog is named Buddy.", now=0.0)

    event = MessageEvent(platform="webhook", chat_id="chat-5", thread_id=None, text="hi")
    asyncio.run(handle_inbound(make_runtime(conn), event))

    loaded = load(conn, "webhook:chat-5", now=0.0)
    assert "Their dog is named Buddy." not in loaded.system_prompt


# ── the pause-check branch (GATEWAY-DAEMON-02) ──────────────────────────


@pytest.mark.unit
def test_handle_inbound_with_no_pause_row_is_unaffected_by_the_check(monkeypatch: pytest.MonkeyPatch) -> None:
    """Explicit regression check for the branch this work item added at the
    top of `handle_inbound`: a conversation with no `plugin_pauses` row
    takes today's exact existing path, unchanged."""
    monkeypatch.setattr(model_access, "send", lambda request: plain_response("reply"))
    conn = open_store(store_path_from_config())
    event = MessageEvent(platform="webhook", chat_id="chat-nopause", thread_id=None, text="hello")

    ok, text = asyncio.run(handle_inbound(make_runtime(conn), event))

    assert ok is True
    assert text == "reply"
    assert load_pause(conn, conversation_key="webhook:chat-nopause") is None


@pytest.mark.unit
def test_handle_inbound_with_a_pause_row_resumes_without_calling_the_model(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _boom(_request: object) -> model_access.Response:
        raise AssertionError("the model must not be called on a resume")

    monkeypatch.setattr(model_access, "send", _boom)
    _wait_then_summarize_installed(tmp_path)  # writes tmp_path/p/plugin.toml et al.
    monkeypatch.setattr(plugins, "_plugins_root", lambda: tmp_path)

    conn = open_store(store_path_from_config())
    key = "webhook:chat-resume"
    conversation, _t = create_conversation(
        ConversationTemplate(name="t", recipe=TemplateRecipe(stable_prompt="p", catalog=(), tool_specs=())),
        key,
        system_message="",
        iteration_budget=IterationBudget(max_total=10),
    )
    create(conn, conversation, now=0.0, account_key="webhook:c1")  # pragma: allowlist secret
    save_pause(conn, conversation_key=key, plugin="p", entry="do_it", node="future", trace=(), artifacts=())

    event = MessageEvent(platform="webhook", chat_id="chat-resume", thread_id=None, text="42")
    ok, text = asyncio.run(handle_inbound(make_runtime(conn), event))

    assert ok is True
    assert text == "answered: 42"
    assert load_pause(conn, conversation_key=key) is None
    loaded = load(conn, key, now=0.0)
    assert loaded.messages[-1].role == "assistant"
    assert loaded.messages[-1].content == "answered: 42"


@pytest.mark.unit
def test_handle_inbound_persists_a_pause_row_when_a_turns_dispatch_call_pauses(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Caught by `scripts/prove_gateway_wait_e2e.py`, not by any test
    written alongside the pause-check branch itself: the resume-side test
    above pre-seeds its own pause row by hand and never exercises a real
    turn's dispatch call actually producing one. Without
    `capturing_dispatch` wired into `handle_inbound`'s own normal path, a
    `paused_node`-bearing `DagResult` a turn's dispatch call returns was
    silently discarded — `run_turn` keeps only `.text` — and no
    `plugin_pauses` row was ever written for the *first* pause."""
    installed = _wait_then_summarize_installed(tmp_path)
    plugin_set = plugin_dispatch.build_plugin_set((installed,))

    def _stateful_send(request: object) -> model_access.Response:
        messages = request.messages  # type: ignore[attr-defined]
        if any(m.get("role") == "tool" for m in messages):
            return plain_response("okay, I'll wait for it.")
        return model_access.Response(
            content=None,
            tool_calls=({"function": {"name": "do_it", "arguments": "{}"}},),
            finish_reason="tool_calls",
            usage=model_access.Usage(),
        )

    monkeypatch.setattr(model_access, "send", _stateful_send)

    conn = open_store(store_path_from_config())
    key = "webhook:chat-first-pause"
    event = MessageEvent(platform="webhook", chat_id="chat-first-pause", thread_id=None, text="start it")

    ok, text = asyncio.run(handle_inbound(make_runtime(conn, plugin_set=plugin_set), event))

    assert ok is True
    assert text == "okay, I'll wait for it."
    pause = load_pause(conn, conversation_key=key)
    assert pause is not None
    assert pause.plugin == "p"
    assert pause.node == "future"


# ── PERSONA-02: who the door is told this is ─────────────────────────────


@pytest.mark.unit
def test_a_webhook_message_still_runs_under_its_own_chat_id(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SADANA_MEMORY_ACCOUNT", "adam")
    monkeypatch.setattr(model_access, "send", lambda request: plain_response("reply"))
    conn = open_store(store_path_from_config())
    event = MessageEvent(platform="webhook", chat_id="chat-9", thread_id=None, text="hello")

    asyncio.run(handle_inbound(make_runtime(conn), event))

    assert accounts_with_conversations(conn) == frozenset({"webhook:chat-9"})


@pytest.mark.unit
def test_an_account_in_the_payload_is_not_an_account(monkeypatch: pytest.MonkeyPatch) -> None:
    """CLAUDE.md: an inbound channel envelope never carries the account it
    belongs to. A sender claiming to be the owner is parsed as an unknown
    key and dropped on the floor, so the turn still runs as the sender."""
    monkeypatch.setenv("SADANA_MEMORY_ACCOUNT", "adam")
    monkeypatch.setattr(model_access, "send", lambda request: plain_response("reply"))
    conn = open_store(store_path_from_config())
    body = json.dumps({"chat_id": "chat-9", "text": "hello", "account": "adam"}).encode("utf-8")

    outcome = channel_webhook.parse_webhook_request(
        headers={"X-Sadana-Webhook-Secret": _SECRET}, body=body, secret=_SECRET
    )
    assert isinstance(outcome, MessageEvent)
    asyncio.run(handle_inbound(make_runtime(conn), outcome))

    assert accounts_with_conversations(conn) == frozenset({"webhook:chat-9"})


@pytest.mark.unit
def test_a_trusted_caller_may_state_the_account(monkeypatch: pytest.MonkeyPatch) -> None:
    """The other half of the same rule: `scheduling.tick()` decided who this
    is before calling, and that override reaches the door."""
    monkeypatch.setattr(model_access, "send", lambda request: plain_response("reply"))
    conn = open_store(store_path_from_config())
    event = MessageEvent(platform="schedule", chat_id="daily", thread_id=None, text="go")

    asyncio.run(handle_inbound(make_runtime(conn), event, account="adam"))

    assert accounts_with_conversations(conn) == frozenset({"adam"})
