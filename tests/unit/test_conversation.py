"""Tests for sadana.conversation: pending_tool_call_ids(), append(), repair()."""

from __future__ import annotations

import pytest

from sadana.conversation import (
    DuplicateToolError,
    Message,
    MessageKey,
    ToolSpec,
    TranscriptInvariantError,
    append,
    build_surface,
    filter_surface,
    pending_tool_call_ids,
    repair,
    surface_hash,
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


# ── tool surface ─────────────────────────────────────────────────────────


def _spec(key: str, name: str, describe=None) -> ToolSpec:
    return ToolSpec(
        key=key,
        name=name,
        parameters={"type": "object", "properties": {}},
        describe=describe or (lambda _resolved: f"{name} does things."),
    )


@pytest.mark.unit
def test_build_surface_raises_for_duplicate_name() -> None:
    with pytest.raises(DuplicateToolError):
        build_surface([_spec("a", "export"), _spec("b", "export")])


@pytest.mark.unit
def test_build_surface_raises_for_duplicate_key() -> None:
    with pytest.raises(DuplicateToolError):
        build_surface([_spec("export", "export_csv"), _spec("export", "export_json")])


@pytest.mark.unit
def test_build_surface_resolves_a_forward_reference() -> None:
    early = _spec("early", "early_tool", describe=lambda r: f"pairs with {r['late']}")
    late = _spec("late", "late_tool")
    surface = build_surface([early, late])
    assert surface[0]["function"]["description"] == "pairs with late_tool"


@pytest.mark.unit
def test_build_surface_rename_changes_a_referencing_description() -> None:
    describe_importer = lambda r: f"use with {r['export']}"  # noqa: E731
    importer = _spec("importer", "import_csv", describe=describe_importer)

    surface_before = build_surface([_spec("export", "export_csv"), importer])
    surface_after = build_surface([_spec("export", "export_data"), importer])

    desc_before = next(d for d in surface_before if d["function"]["name"] == "import_csv")
    desc_after = next(d for d in surface_after if d["function"]["name"] == "import_csv")
    assert desc_before["function"]["description"] == "use with export_csv"
    assert desc_after["function"]["description"] == "use with export_data"


@pytest.mark.unit
def test_filter_surface_keeps_only_matching_names_in_order() -> None:
    surface = build_surface([_spec("a", "alpha"), _spec("b", "beta"), _spec("c", "gamma")])
    filtered = filter_surface(surface, frozenset({"gamma", "alpha"}))
    assert [d["function"]["name"] for d in filtered] == ["alpha", "gamma"]


@pytest.mark.unit
def test_filter_surface_never_reinvokes_describe() -> None:
    calls = []
    spec = _spec("a", "alpha", describe=lambda r: (calls.append(1), "alpha desc")[1])
    surface = build_surface([spec])
    assert len(calls) == 1

    filter_surface(surface, frozenset({"alpha"}))
    filter_surface(surface, frozenset())
    filter_surface(surface, frozenset({"alpha", "nonexistent"}))
    assert len(calls) == 1


@pytest.mark.unit
def test_filter_surface_with_no_match_returns_empty_tuple() -> None:
    surface = build_surface([_spec("a", "alpha")])
    assert filter_surface(surface, frozenset({"nonexistent"})) == ()


@pytest.mark.unit
def test_surface_hash_ignores_dict_key_order() -> None:
    surface_a = ({"type": "function", "function": {"name": "a", "description": "d", "parameters": {}}},)
    surface_b = ({"function": {"parameters": {}, "description": "d", "name": "a"}, "type": "function"},)
    assert surface_hash(surface_a) == surface_hash(surface_b)


@pytest.mark.unit
def test_surface_hash_changes_when_a_field_differs() -> None:
    surface = build_surface([_spec("a", "alpha")])
    other = build_surface([_spec("a", "alpha", describe=lambda r: "a different description")])
    assert surface_hash(surface) != surface_hash(other)
