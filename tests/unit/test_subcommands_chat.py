"""Tests for sadana.subcommands.chat."""

from __future__ import annotations

import argparse

import pytest

from conftest import conversation as _build_conversation
from conftest import plain_response, tool_call_response
from sadana import model_access
from sadana.conversation import Conversation
from sadana.conversation_store import ConversationNotFound, create, load, open_store, store_path_from_config
from sadana.persona import persona_path_from_config
from sadana.subcommands.chat import build_chat_parser, cmd_chat


def _args(**overrides: object) -> argparse.Namespace:
    defaults: dict[str, object] = {"resume": None, "key": None, "provider": None, "model": None}
    defaults.update(overrides)
    return argparse.Namespace(**defaults)


def _feed(monkeypatch: pytest.MonkeyPatch, *inputs: str) -> None:
    """`input()` returns each of `inputs` in order, then raises EOFError."""
    remaining = iter(inputs)

    def fake_input(_prompt: str = "") -> str:
        try:
            return next(remaining)
        except StopIteration:
            raise EOFError from None

    monkeypatch.setattr("builtins.input", fake_input)


def _load(key: str) -> Conversation:
    return load(open_store(store_path_from_config()), key, now=0.0)


# ── build_chat_parser ────────────────────────────────────────────────────


@pytest.mark.unit
def test_build_chat_parser_defaults_and_wiring() -> None:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    build_chat_parser(subparsers)

    args = parser.parse_args(["chat"])
    assert (args.resume, args.key, args.provider, args.model) == (None, None, None, None)
    assert args.func is cmd_chat


@pytest.mark.unit
def test_build_chat_parser_rejects_resume_and_key_together() -> None:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    build_chat_parser(subparsers)

    with pytest.raises(SystemExit) as exc:
        parser.parse_args(["chat", "--resume", "x", "--key", "y"])
    assert exc.value.code == 2


# ── cmd_chat: an unwired provider fails via the normal turn loop ────────


@pytest.mark.unit
def test_cmd_chat_unknown_provider_fails_via_the_normal_turn_loop(monkeypatch: pytest.MonkeyPatch) -> None:
    _feed(monkeypatch, "hello")

    assert cmd_chat(_args(key="bad-provider-test", provider="not-a-real-provider")) == 1

    # unlike before MODEL-ACCESS-01, the conversation row already exists —
    # the failure is discovered on the first turn, not before the store is
    # touched. _load would raise ConversationNotFound if it weren't.
    _load("bad-provider-test")


# ── cmd_chat: a real turn, creation, and the post-turn save ─────────────


@pytest.mark.unit
def test_cmd_chat_creates_and_persists_a_new_conversation(monkeypatch: pytest.MonkeyPatch) -> None:
    responses = iter([plain_response("hi there")])
    monkeypatch.setattr(model_access, "send", lambda request: next(responses))
    _feed(monkeypatch, "hello")

    assert cmd_chat(_args(key="test-convo")) == 0

    loaded = _load("test-convo")
    assert loaded.next_turn_seq == 1
    assert loaded.iteration_budget.used > 0
    assert any(m.role == "user" and m.content == "hello" for m in loaded.messages)


@pytest.mark.unit
def test_cmd_chat_immediate_eof_creates_conversation_with_no_turns(monkeypatch: pytest.MonkeyPatch) -> None:
    _feed(monkeypatch)  # EOF on the very first prompt

    assert cmd_chat(_args(key="empty-convo")) == 0

    loaded = _load("empty-convo")
    assert loaded.next_turn_seq == 0
    assert loaded.messages == ()


# ── cmd_chat: resume ─────────────────────────────────────────────────────


@pytest.mark.unit
def test_cmd_chat_resume_continues_existing_conversation(monkeypatch: pytest.MonkeyPatch) -> None:
    conn = open_store(store_path_from_config())
    try:
        create(conn, _build_conversation(key="resume-me"), now=0.0)
    finally:
        conn.close()

    responses = iter([plain_response("continuing")])
    monkeypatch.setattr(model_access, "send", lambda request: next(responses))
    _feed(monkeypatch, "still there?")

    assert cmd_chat(_args(resume="resume-me")) == 0

    loaded = _load("resume-me")
    assert loaded.next_turn_seq == 1


@pytest.mark.unit
def test_cmd_chat_resume_unknown_key_raises_conversation_not_found() -> None:
    with pytest.raises(ConversationNotFound):
        cmd_chat(_args(resume="never-saved"))


@pytest.mark.unit
def test_cmd_chat_resume_keeps_original_persona_after_a_later_edit(monkeypatch: pytest.MonkeyPatch) -> None:
    responses = iter([plain_response("first reply")])
    monkeypatch.setattr(model_access, "send", lambda request: next(responses))
    _feed(monkeypatch, "hello")
    assert cmd_chat(_args(key="persona-test")) == 0
    original_prompt = _load("persona-test").system_prompt

    persona_path_from_config().write_text("You are now a pirate.\n", encoding="utf-8")

    responses = iter([plain_response("second reply")])
    monkeypatch.setattr(model_access, "send", lambda request: next(responses))
    _feed(monkeypatch, "still there?")
    assert cmd_chat(_args(resume="persona-test")) == 0

    assert _load("persona-test").system_prompt == original_prompt


# ── cmd_chat: approval is asked, using the real prompt, never overridden ─


@pytest.mark.unit
def test_cmd_chat_declined_approval_stops_the_call_node_safely(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SADANA_PLUGINS_DIR", "tests/fixtures/plugins")
    responses = iter([tool_call_response("plugin_a_entry"), plain_response("ok, noted")])
    monkeypatch.setattr(model_access, "send", lambda request: next(responses))
    _feed(monkeypatch, "please call plugin_a_entry", "n")  # 2nd input() is the approval prompt

    assert cmd_chat(_args(key="approval-test")) == 0

    loaded = _load("approval-test")
    assert loaded.next_turn_seq == 1  # the turn still completed; the call node just didn't run
