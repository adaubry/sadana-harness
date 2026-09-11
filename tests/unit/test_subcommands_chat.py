"""Tests for sadana.subcommands.chat."""

from __future__ import annotations

import argparse
import shutil
from pathlib import Path

import pytest

from conftest import conversation as _build_conversation
from conftest import plain_response, tool_call_response, tool_then_text
from conftest import wait_then_summarize_installed as _wait_then_summarize_installed
from sadana import memory_store, model_access
from sadana.conversation import Conversation, IterationBudget
from sadana.conversation_store import ConversationNotFound, create, load, open_store, store_path_from_config
from sadana.persona import persona_path_from_config
from sadana.subcommands.chat import build_chat_parser, cmd_chat


def _args(**overrides: object) -> argparse.Namespace:
    defaults: dict[str, object] = {"resume": None, "key": None, "provider": None, "model": None, "account": None}
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
def test_cmd_chat_records_the_turn(monkeypatch: pytest.MonkeyPatch) -> None:
    """OBSERVABILITY-01: a real chat turn leaves a `turn_runs` row behind,
    not just the transcript `test_cmd_chat_creates_and_persists_a_new_conversation`
    already checks."""
    responses = iter([plain_response("hi there")])
    monkeypatch.setattr(model_access, "send", lambda request: next(responses))
    _feed(monkeypatch, "hello")

    assert cmd_chat(_args(key="recorded-convo")) == 0

    conn = open_store(store_path_from_config())
    try:
        row = conn.execute(
            "SELECT * FROM turn_runs WHERE conversation_key = ? AND turn_seq = 0", ("recorded-convo",)
        ).fetchone()
    finally:
        conn.close()
    assert row is not None
    assert row["exit_reason"] == "completed"


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
def test_cmd_chat_declined_approval_stops_the_call_node_safely(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    # A copy, not the repo's own `tests/fixtures/plugins` in place: MEMORY-01's
    # `cmd_chat` now seeds a `memory` plugin directory under whatever
    # `SADANA_PLUGINS_DIR` resolves to, and this is the one test that ever
    # pointed that env var at a real, tracked repository path.
    plugins_root = tmp_path / "plugins"
    shutil.copytree("tests/fixtures/plugins", plugins_root)
    monkeypatch.setenv("SADANA_PLUGINS_DIR", str(plugins_root))
    responses = iter([tool_call_response("plugin_a_entry"), plain_response("ok, noted")])
    monkeypatch.setattr(model_access, "send", lambda request: next(responses))
    _feed(monkeypatch, "please call plugin_a_entry", "n")  # 2nd input() is the approval prompt

    assert cmd_chat(_args(key="approval-test")) == 0

    loaded = _load("approval-test")
    assert loaded.next_turn_seq == 1  # the turn still completed; the call node just didn't run


# ── cmd_chat: what lands on stdout, what lands on stderr, what the exit says ─


@pytest.mark.unit
def test_cmd_chat_prints_a_completed_turns_answer_to_stdout_and_nothing_to_stderr(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(model_access, "send", lambda request: plain_response("the answer"))
    _feed(monkeypatch, "hello")

    assert cmd_chat(_args(key="stdout-test")) == 0

    captured = capsys.readouterr()
    assert "the answer" in captured.out
    assert captured.err == ""


@pytest.mark.unit
def test_cmd_chat_sends_a_budget_exhausted_turns_own_summary_to_stdout_not_stderr(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A turn that did not complete can still carry real words —
    `conversation.py`'s EPILOGUE — and those words are the answer, so they
    belong on stdout with only the exit code reporting that something went
    wrong. Routing them to stderr instead is the regression
    `client_surface.TurnOutcome`'s three fields exist to make impossible, and
    this is the only test that watches the streams themselves."""
    conn = open_store(store_path_from_config())
    create(conn, _build_conversation(key="exhausted", iteration_budget=IterationBudget(max_total=1, used=1)), now=0.0)
    monkeypatch.setattr(model_access, "send", lambda request: plain_response("here is what we did"))
    _feed(monkeypatch, "hello")

    assert cmd_chat(_args(resume="exhausted")) == 1

    captured = capsys.readouterr()
    assert "here is what we did" in captured.out
    assert "here is what we did" not in captured.err


# ── cmd_chat: a paused plugin run survives, and the next line resumes it ──


@pytest.mark.unit
def test_cmd_chat_resumes_a_paused_plugin_run_on_the_next_line(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """The defect `intent.md` exists to fix, stated as code. A `wait` node
    reached from the terminal used to be discarded in silence: this file held
    its own copy of the turn body and the copy never passed `persist_pause`,
    so `build_dispatch` fell back to a no-op and no `plugin_pauses` row was
    ever written — while the identical plugin over the identical store was
    resumable from a webhook. Two lines of input now: the first reaches the
    `wait` node, the second carries the answer through it.

    `len(calls) == 2` is the half that matters most. A resume must not reach
    the model at all, so a third request would mean the pause was not found
    and an ordinary turn ran in its place."""
    plugins_root = tmp_path / "plugins"
    plugins_root.mkdir()
    _wait_then_summarize_installed(plugins_root)
    monkeypatch.setenv("SADANA_PLUGINS_DIR", str(plugins_root))

    calls: list[object] = []
    send = tool_then_text()

    def _counting_send(request: object) -> model_access.Response:
        calls.append(request)
        return send(request)

    monkeypatch.setattr(model_access, "send", _counting_send)
    _feed(monkeypatch, "start it", "42")

    assert cmd_chat(_args(key="terminal-pause")) == 0

    assert len(calls) == 2
    last = _load("terminal-pause").messages[-1]
    assert (last.role, last.content) == ("assistant", "answered: 42")


# ── cmd_chat: MEMORY-01 recall folded into a new conversation's prompt ──


@pytest.mark.unit
def test_cmd_chat_new_conversation_recalls_the_given_accounts_memories(monkeypatch: pytest.MonkeyPatch) -> None:
    conn = open_store(store_path_from_config())
    try:
        memory_store.ensure_schema(conn)
        memory_store.write_entry(conn, "a1", "dog_name", "Their dog is named Buddy.", now=0.0)
    finally:
        conn.close()
    _feed(monkeypatch)  # immediate EOF; only conversation creation matters here

    assert cmd_chat(_args(key="recall-test", account="a1")) == 0

    assert "Their dog is named Buddy." in _load("recall-test").system_prompt


@pytest.mark.unit
def test_cmd_chat_new_conversation_never_recalls_a_different_accounts_memories(monkeypatch: pytest.MonkeyPatch) -> None:
    conn = open_store(store_path_from_config())
    try:
        memory_store.ensure_schema(conn)
        memory_store.write_entry(conn, "a1", "dog_name", "Their dog is named Buddy.", now=0.0)
    finally:
        conn.close()
    _feed(monkeypatch)

    assert cmd_chat(_args(key="other-account-test", account="a2")) == 0

    assert "Their dog is named Buddy." not in _load("other-account-test").system_prompt
