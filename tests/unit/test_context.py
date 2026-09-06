"""Tests for sadana.context: the four CONTEXT lifecycle checkpoints."""

from __future__ import annotations

import asyncio

import pytest

from sadana import model_access, result_spill
from sadana.context import (
    CacheHint,
    ContextState,
    TurnCompleteResult,
    after_response,
    after_tool_result,
    before_send,
    compaction_tail_messages_from_config,
    result_spill_threshold_from_config,
    trailing_marks_from_config,
    turn_complete,
)
from sadana.conversation import Message
from sadana.model_access import Usage

# ── trailing_marks_from_config ───────────────────────────────────────────


@pytest.mark.unit
def test_trailing_marks_from_config_default_is_one() -> None:
    assert trailing_marks_from_config() == 1


@pytest.mark.unit
def test_trailing_marks_from_config_reads_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SADANA_CONTEXT_CACHE_TRAILING_MARKS", "3")
    assert trailing_marks_from_config() == 3


# ── before_send ───────────────────────────────────────────────────────────


@pytest.mark.unit
def test_before_send_returns_cache_hint() -> None:
    history = (Message(role="user", content="hi"),)

    result = before_send(history, stable_prompt_len=12)

    assert result == CacheHint(stable_prefix_len=12, trailing_marks=1)


@pytest.mark.unit
def test_before_send_honors_trailing_marks_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SADANA_CONTEXT_CACHE_TRAILING_MARKS", "2")

    result = before_send((), stable_prompt_len=0)

    assert result.trailing_marks == 2


@pytest.mark.unit
def test_before_send_is_callable_twice_with_no_side_effects() -> None:
    # B2: may be called more than once in one turn (e.g. a
    # needs-context-compression retry), read-only against any state.
    first = before_send((), stable_prompt_len=5)
    second = before_send((), stable_prompt_len=5)
    assert first == second


# ── after_response ────────────────────────────────────────────────────────


@pytest.mark.unit
def test_after_response_accumulates_usage() -> None:
    state = ContextState()

    state = after_response(state, Usage(prompt_tokens=10, completion_tokens=2))
    state = after_response(state, Usage(prompt_tokens=5, completion_tokens=1))

    assert state == ContextState(total_prompt_tokens=15, total_completion_tokens=3)


@pytest.mark.unit
def test_after_response_does_not_mutate_its_input() -> None:
    state = ContextState(total_prompt_tokens=1, total_completion_tokens=1)
    after_response(state, Usage(prompt_tokens=10, completion_tokens=10))
    assert state == ContextState(total_prompt_tokens=1, total_completion_tokens=1)


# ── result_spill_threshold_from_config ────────────────────────────────────


@pytest.mark.unit
def test_result_spill_threshold_from_config_default_matches_truncation_cap() -> None:
    assert result_spill_threshold_from_config() == 100_000


@pytest.mark.unit
def test_result_spill_threshold_from_config_reads_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SADANA_CONTEXT_RESULT_SPILL_CHARS", "500")
    assert result_spill_threshold_from_config() == 500


# ── after_tool_result ─────────────────────────────────────────────────────


@pytest.mark.unit
def test_after_tool_result_is_identity_under_threshold() -> None:
    state = ContextState()
    result = Message(role="tool", tool_call_id="call_1", content="ok")
    assert asyncio.run(after_tool_result(state, result)) is result


@pytest.mark.unit
def test_after_tool_result_spills_content_over_threshold(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SADANA_CONTEXT_RESULT_SPILL_CHARS", "10")
    monkeypatch.setattr(result_spill, "write_and_reference", lambda tool_call_id, content: f"spilled:{tool_call_id}")

    state = ContextState()
    result = Message(role="tool", tool_call_id="call_1", content="x" * 20)

    updated = asyncio.run(after_tool_result(state, result))

    assert updated.content == "spilled:call_1"
    assert updated.role == "tool" and updated.tool_call_id == "call_1"


@pytest.mark.unit
def test_after_tool_result_uses_a_fallback_id_when_none_given(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SADANA_CONTEXT_RESULT_SPILL_CHARS", "1")
    captured: list[str] = []
    monkeypatch.setattr(
        result_spill, "write_and_reference", lambda tool_call_id, content: captured.append(tool_call_id) or "ref"
    )

    result = Message(role="tool", tool_call_id=None, content="xx")
    asyncio.run(after_tool_result(ContextState(), result))

    assert captured == ["result"]


# ── compaction_tail_messages_from_config ──────────────────────────────────


@pytest.mark.unit
def test_compaction_tail_messages_from_config_default_is_ten() -> None:
    assert compaction_tail_messages_from_config() == 10


@pytest.mark.unit
def test_compaction_tail_messages_from_config_reads_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SADANA_CONTEXT_COMPACTION_TAIL_MESSAGES", "4")
    assert compaction_tail_messages_from_config() == 4


# ── turn_complete ─────────────────────────────────────────────────────────


@pytest.mark.unit
def test_turn_complete_returns_a_fresh_summary(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        model_access,
        "send",
        lambda request: model_access.Response(
            content="a fresh summary", tool_calls=(), finish_reason="stop", usage=model_access.Usage()
        ),
    )
    state = ContextState(total_prompt_tokens=3, total_completion_tokens=1)
    history = (Message(role="user", content="old stuff"), Message(role="user", content="recent"))

    result = asyncio.run(turn_complete(state, history, "sys", tail_start=1, provider="p", model="m"))

    assert isinstance(result, TurnCompleteResult)
    assert result.context_state == state
    assert result.new_summary_text == "a fresh summary"
    assert result.new_system_prompt is None


@pytest.mark.unit
def test_turn_complete_is_a_no_op_when_nothing_needs_summarizing() -> None:
    state = ContextState()
    history = (Message(role="user", content="hi"),)

    result = asyncio.run(turn_complete(state, history, "sys", tail_start=0, provider="p", model="m"))

    assert result == TurnCompleteResult(context_state=state)


@pytest.mark.unit
@pytest.mark.parametrize(
    "outcome",
    [
        model_access.Response(content=None, tool_calls=(), finish_reason="stop", usage=model_access.Usage()),
        model_access.Response(content="", tool_calls=(), finish_reason="stop", usage=model_access.Usage()),
        model_access.Abort("gave up"),
        model_access.Degenerate("empty"),
    ],
)
def test_turn_complete_returns_no_summary_on_failure(
    monkeypatch: pytest.MonkeyPatch, outcome: model_access.Outcome
) -> None:
    monkeypatch.setattr(model_access, "send", lambda request: outcome)
    state = ContextState()
    history = (Message(role="user", content="old"), Message(role="user", content="new"))

    result = asyncio.run(turn_complete(state, history, "sys", tail_start=1, provider="p", model="m"))

    assert result == TurnCompleteResult(context_state=state)


@pytest.mark.unit
def test_turn_complete_asks_to_refine_an_existing_summary(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: list[model_access.Request] = []

    def fake_send(request: model_access.Request) -> model_access.Outcome:
        captured.append(request)
        return model_access.Response(
            content="updated summary", tool_calls=(), finish_reason="stop", usage=model_access.Usage()
        )

    monkeypatch.setattr(model_access, "send", fake_send)
    history = (
        Message(role="user", content="a prior summary", is_summary=True),
        Message(role="user", content="a new turn"),
    )

    asyncio.run(turn_complete(ContextState(), history, "sys", tail_start=1, provider="p", model="m"))

    prompt = captured[0].messages[0]["content"]
    assert "updated summary" in prompt or "own summary" in prompt
    assert "a prior summary" in prompt


@pytest.mark.unit
def test_turn_complete_uses_a_fresh_framing_without_an_existing_summary(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: list[model_access.Request] = []

    def fake_send(request: model_access.Request) -> model_access.Outcome:
        captured.append(request)
        return model_access.Response(
            content="a summary", tool_calls=(), finish_reason="stop", usage=model_access.Usage()
        )

    monkeypatch.setattr(model_access, "send", fake_send)
    history = (Message(role="user", content="raw turn one"), Message(role="user", content="raw turn two"))

    asyncio.run(turn_complete(ContextState(), history, "sys", tail_start=2, provider="p", model="m"))

    prompt = captured[0].messages[0]["content"]
    assert "own summary" not in prompt
    assert "raw turn one" in prompt
