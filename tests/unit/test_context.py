"""Tests for sadana.context: the four CONTEXT lifecycle checkpoints."""

from __future__ import annotations

import pytest

from sadana.context import (
    CacheHint,
    ContextState,
    after_response,
    after_tool_result,
    before_send,
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


# ── after_tool_result ─────────────────────────────────────────────────────


@pytest.mark.unit
def test_after_tool_result_is_identity() -> None:
    state = ContextState()
    result = Message(role="tool", tool_call_id="call_1", content="ok")
    assert after_tool_result(state, result) is result


# ── turn_complete ─────────────────────────────────────────────────────────


@pytest.mark.unit
def test_turn_complete_is_a_real_no_op() -> None:
    state = ContextState(total_prompt_tokens=3, total_completion_tokens=1)
    history = (Message(role="user", content="hi"),)

    new_state, new_prompt = turn_complete(state, history, "system prompt")

    assert new_state == state
    assert new_prompt is None
