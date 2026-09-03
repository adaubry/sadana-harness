"""Tests for sadana.conversation: pending_tool_call_ids(), append(), repair()."""

from __future__ import annotations

import pytest

from sadana.conversation import (
    Message,
    MessageKey,
    TranscriptInvariantError,
    append,
    pending_tool_call_ids,
    repair,
)


def _assistant_call(*ids: str) -> Message:
    return Message(
        role="assistant",
        tool_calls=tuple({"id": call_id, "name": "noop", "arguments": "{}"} for call_id in ids),
    )


def _tool_result(call_id: str, content: str = "ok") -> Message:
    return Message(role="tool", content=content, tool_call_id=call_id)


# ── append() — rejects a malformed append ───────────────────────────────


@pytest.mark.unit
def test_append_raises_for_tool_message_with_nothing_pending() -> None:
    with pytest.raises(TranscriptInvariantError):
        append("c1", (), _tool_result("call_1"))


@pytest.mark.unit
def test_append_raises_for_tool_message_with_wrong_id() -> None:
    messages = (_assistant_call("call_1"),)
    with pytest.raises(TranscriptInvariantError):
        append("c1", messages, _tool_result("call_2"))


@pytest.mark.unit
def test_append_raises_for_user_message_while_pending() -> None:
    messages = (_assistant_call("call_1"),)
    with pytest.raises(TranscriptInvariantError):
        append("c1", messages, Message(role="user", content="hi"))


@pytest.mark.unit
def test_append_raises_for_assistant_message_while_pending() -> None:
    messages = (_assistant_call("call_1"),)
    with pytest.raises(TranscriptInvariantError):
        append("c1", messages, Message(role="assistant", content="hang on"))


# ── append() — accepts what's valid ─────────────────────────────────────


@pytest.mark.unit
def test_append_accepts_text_only_assistant_message_when_nothing_pending() -> None:
    new_messages, key = append("c1", (), Message(role="assistant", content="hi"))
    assert new_messages[-1].content == "hi"
    assert key == MessageKey(conversation="c1", msg_seq=0)


@pytest.mark.unit
def test_append_accepts_matching_tool_result_and_clears_pending() -> None:
    messages = (_assistant_call("call_1"),)
    new_messages, key = append("c1", messages, _tool_result("call_1"))
    assert key == MessageKey(conversation="c1", msg_seq=1)
    assert pending_tool_call_ids(new_messages) == frozenset()


@pytest.mark.unit
def test_two_sequential_tool_rounds_reusing_id_are_paired_independently() -> None:
    """A provider can reuse a short id string (e.g. "call_1") across
    separate tool rounds. pending_tool_call_ids must judge the second round
    on its own tail, not treat the first round's already-answered id as
    covering the second round's identically-named call."""
    messages: tuple[Message, ...] = ()
    messages, _ = append("c1", messages, _assistant_call("call_1"))
    messages, _ = append("c1", messages, _tool_result("call_1", content="first round"))
    messages, _ = append("c1", messages, _assistant_call("call_1"))  # reused id

    assert pending_tool_call_ids(messages) == frozenset({"call_1"})

    messages, _ = append("c1", messages, _tool_result("call_1", content="second round"))
    assert pending_tool_call_ids(messages) == frozenset()
    assert messages[1].content == "first round"
    assert messages[3].content == "second round"


# ── repair() ─────────────────────────────────────────────────────────────


@pytest.mark.unit
def test_repair_is_a_no_op_when_nothing_pending() -> None:
    messages = (Message(role="user", content="hi"),)
    assert repair(messages) is messages


@pytest.mark.unit
def test_repair_appends_one_tool_message_per_pending_id_in_order() -> None:
    messages = (_assistant_call("call_1", "call_2"),)
    repaired = repair(messages)

    assert pending_tool_call_ids(repaired) == frozenset()
    assert [m.tool_call_id for m in repaired[1:]] == ["call_1", "call_2"]
    assert all(m.role == "tool" for m in repaired[1:])


# ── neither function mutates its input ──────────────────────────────────


@pytest.mark.unit
def test_append_does_not_mutate_input_on_success_or_raise() -> None:
    messages = (_assistant_call("call_1"),)

    with pytest.raises(TranscriptInvariantError):
        append("c1", messages, Message(role="user", content="hi"))
    assert messages == (_assistant_call("call_1"),)

    new_messages, _ = append("c1", messages, _tool_result("call_1"))
    assert messages == (_assistant_call("call_1"),)
    assert new_messages is not messages


@pytest.mark.unit
def test_repair_does_not_mutate_input() -> None:
    messages = (_assistant_call("call_1"),)
    original = messages
    repaired = repair(messages)
    assert messages == original
    assert repaired is not messages
