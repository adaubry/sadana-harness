"""Tests for sadana.conversation: pending_tool_call_ids(), append(), repair()."""

from __future__ import annotations

import asyncio
import dataclasses
import time
from pathlib import Path

import pytest

from sadana import context, model_access
from sadana.context import CacheHint, ContextState
from sadana.conversation import (
    ChildDepthExceeded,
    ChildSpec,
    Completion,
    ContextOverflow,
    ConversationTemplate,
    DuplicateToolError,
    ExitReason,
    IterationBudget,
    Message,
    MessageKey,
    PluginCatalogEntry,
    PromptDriftError,
    ProviderFailure,
    SkillLoadError,
    SkillRef,
    TemplateRecipe,
    ToolSpec,
    ToolSurface,
    TranscriptInvariantError,
    TurnKey,
    WallClockBudget,
    _child_key,  # private, tested directly per spec.md
    _conversation_depth,  # private, tested directly per spec.md
    _deduplicate_tool_calls,  # private, tested directly per spec.md
    append,
    build_surface,
    child_iteration_budget_from_config,
    child_max_depth_from_config,
    coalesce_tool_call_id,
    complete,
    consume_iteration,
    create_conversation,
    defer_invalidation,
    deterministic_call_id,
    filter_surface,
    iteration_budget_from_config,
    load_skill,
    pending_tool_call_ids,
    repair,
    repair_tool_call_arguments,
    run_child,
    run_turn,
    surface_hash,
    take_turn,
    turn_prompt_hash,
    uniquify_tool_call_ids,
    wall_clock_budget_from_config,
    wall_clock_remaining,
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


# ── budgets ──────────────────────────────────────────────────────────────


@pytest.mark.unit
def test_consume_iteration_below_max_returns_incremented_budget() -> None:
    budget = IterationBudget(max_total=3, used=1)
    new_budget = consume_iteration(budget)
    assert new_budget == IterationBudget(max_total=3, used=2)
    assert budget == IterationBudget(max_total=3, used=1)


@pytest.mark.unit
def test_consume_iteration_at_max_returns_none() -> None:
    budget = IterationBudget(max_total=3, used=3)
    assert consume_iteration(budget) is None


@pytest.mark.unit
def test_consume_iteration_zero_max_returns_none_immediately() -> None:
    budget = IterationBudget(max_total=0)
    assert consume_iteration(budget) is None


@pytest.mark.unit
def test_wall_clock_remaining_positive_before_deadline() -> None:
    budget = WallClockBudget(deadline=1000.0)
    assert wall_clock_remaining(budget, now=900.0) == 100.0


@pytest.mark.unit
def test_wall_clock_remaining_non_positive_at_or_after_deadline() -> None:
    budget = WallClockBudget(deadline=1000.0)
    assert wall_clock_remaining(budget, now=1000.0) == 0.0
    assert wall_clock_remaining(budget, now=1100.0) < 0


@pytest.mark.unit
def test_iteration_budget_from_config_defaults_to_60(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SADANA_CONVERSATION_MAX_ITERATIONS", raising=False)
    assert iteration_budget_from_config() == IterationBudget(max_total=60)


@pytest.mark.unit
def test_iteration_budget_from_config_uses_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SADANA_CONVERSATION_MAX_ITERATIONS", "12")
    assert iteration_budget_from_config() == IterationBudget(max_total=12)


@pytest.mark.unit
def test_iteration_budget_from_config_raises_for_negative(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SADANA_CONVERSATION_MAX_ITERATIONS", "-1")
    with pytest.raises(ValueError):
        iteration_budget_from_config()


@pytest.mark.unit
def test_wall_clock_budget_from_config_defaults_to_none(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SADANA_CONVERSATION_RUN_BUDGET_SECONDS", raising=False)
    assert wall_clock_budget_from_config(now=0.0) is None


@pytest.mark.unit
def test_wall_clock_budget_from_config_explicit_zero_is_none(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SADANA_CONVERSATION_RUN_BUDGET_SECONDS", "0")
    assert wall_clock_budget_from_config(now=0.0) is None


@pytest.mark.unit
def test_wall_clock_budget_from_config_positive_value(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SADANA_CONVERSATION_RUN_BUDGET_SECONDS", "30")
    assert wall_clock_budget_from_config(now=100.0) == WallClockBudget(deadline=130.0)


@pytest.mark.unit
def test_wall_clock_budget_from_config_raises_for_negative(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SADANA_CONVERSATION_RUN_BUDGET_SECONDS", "-1")
    with pytest.raises(ValueError):
        wall_clock_budget_from_config(now=0.0)


# ── provider port: repair_tool_call_arguments() ─────────────────────────


@pytest.mark.unit
def test_repair_tool_call_arguments_parses_well_formed_json() -> None:
    assert repair_tool_call_arguments('{"a": 1}') == {"a": 1}


@pytest.mark.unit
def test_repair_tool_call_arguments_strips_trailing_comma() -> None:
    assert repair_tool_call_arguments('{"a": 1,}') == {"a": 1}


@pytest.mark.unit
def test_repair_tool_call_arguments_closes_unclosed_bracket() -> None:
    assert repair_tool_call_arguments('{"a": 1') == {"a": 1}


@pytest.mark.unit
def test_repair_tool_call_arguments_escapes_control_char() -> None:
    # Trailing comma AND a raw control char: pass 0 (strict=False) fails on
    # the comma; pass 1 strips it; the remaining control char still fails
    # strict json.loads until pass 4 escapes it.
    raw = '{"a": "x\x01y",}'
    assert repair_tool_call_arguments(raw) == {"a": "x\x01y"}


@pytest.mark.unit
def test_repair_tool_call_arguments_falls_back_to_empty_dict() -> None:
    assert repair_tool_call_arguments("not json at all {{{") == {}


@pytest.mark.unit
@pytest.mark.parametrize("raw", ["[1, 2, 3]", "5", '"a string"', "null", "true"])
def test_repair_tool_call_arguments_rejects_valid_non_object_json(raw: str) -> None:
    """Syntactically valid JSON that isn't an object must not count as a
    successful repair — the function's contract is always a dict."""
    assert repair_tool_call_arguments(raw) == {}


# ── provider port: id resolution ────────────────────────────────────────


@pytest.mark.unit
def test_deterministic_call_id_is_deterministic() -> None:
    first = deterministic_call_id("foo", '{"a": 1}', 0)
    second = deterministic_call_id("foo", '{"a": 1}', 0)
    assert first == second
    assert first.startswith("call_")


@pytest.mark.unit
def test_deterministic_call_id_differs_by_index() -> None:
    assert deterministic_call_id("foo", "{}", 0) != deterministic_call_id("foo", "{}", 1)


@pytest.mark.unit
def test_coalesce_tool_call_id_prefers_call_id_then_id() -> None:
    assert coalesce_tool_call_id({"call_id": "c1", "id": "i1"}) == "c1"
    assert coalesce_tool_call_id({"id": "i1"}) == "i1"


@pytest.mark.unit
def test_coalesce_tool_call_id_splits_composite() -> None:
    assert coalesce_tool_call_id({"id": "c1|resp1"}) == "c1"


@pytest.mark.unit
def test_coalesce_tool_call_id_returns_empty_when_absent() -> None:
    assert coalesce_tool_call_id({}) == ""


@pytest.mark.unit
def test_uniquify_tool_call_ids_suffixes_duplicates() -> None:
    calls = ({"id": "dup", "name": "a"}, {"id": "dup", "name": "b"})
    result = uniquify_tool_call_ids(calls)
    assert [tc["id"] for tc in result] == ["dup", "dup_d2"]


# ── provider port: complete() ────────────────────────────────────────────


def _raw_tool_call(name: str, arguments: str, *, id_: str | None = None) -> dict:
    tc: dict = {"function": {"name": name, "arguments": arguments}}
    if id_ is not None:
        tc["id"] = id_
    return tc


@pytest.mark.unit
def test_complete_returns_completion_for_response(monkeypatch: pytest.MonkeyPatch) -> None:
    usage = model_access.Usage(prompt_tokens=1, completion_tokens=2)
    response = model_access.Response(content="hi", tool_calls=(), finish_reason="stop", usage=usage)
    monkeypatch.setattr(model_access, "send", lambda request: response)

    result = asyncio.run(complete(system="sys", messages=(), tools=(), provider="p", model="m"))
    assert result == Completion(content="hi", tool_calls=(), finish_reason="stop", usage=usage)


@pytest.mark.unit
def test_complete_applies_cache_hint_to_the_real_request(monkeypatch: pytest.MonkeyPatch) -> None:
    response = model_access.Response(content="hi", tool_calls=(), finish_reason="stop", usage=model_access.Usage())
    captured: list[model_access.Request] = []

    def fake_send(request: model_access.Request) -> model_access.Outcome:
        captured.append(request)
        return response

    monkeypatch.setattr(model_access, "send", fake_send)

    asyncio.run(
        complete(
            system="stable" + "volatile",
            messages=({"role": "user", "content": "hi"},),
            tools=(),
            provider="p",
            model="m",
            cache_hint=CacheHint(stable_prefix_len=len("stable"), trailing_marks=1),
        )
    )

    request = captured[0]
    assert request.messages[0]["content"] == [
        {"type": "text", "text": "stable", "cache_control": {"type": "ephemeral"}},
        {"type": "text", "text": "volatile"},
    ]
    assert request.messages[1]["content"] == [{"type": "text", "text": "hi", "cache_control": {"type": "ephemeral"}}]


@pytest.mark.unit
def test_complete_without_cache_hint_sends_messages_unmarked(monkeypatch: pytest.MonkeyPatch) -> None:
    response = model_access.Response(content="hi", tool_calls=(), finish_reason="stop", usage=model_access.Usage())
    captured: list[model_access.Request] = []

    def fake_send(request: model_access.Request) -> model_access.Outcome:
        captured.append(request)
        return response

    monkeypatch.setattr(model_access, "send", fake_send)

    asyncio.run(
        complete(system="sys", messages=({"role": "user", "content": "hi"},), tools=(), provider="p", model="m")
    )

    assert captured[0].messages == ({"role": "system", "content": "sys"}, {"role": "user", "content": "hi"})


@pytest.mark.unit
def test_complete_retries_transparently(monkeypatch: pytest.MonkeyPatch) -> None:
    seen_attempts = []
    usage = model_access.Usage()
    response = model_access.Response(content="ok", tool_calls=(), finish_reason="stop", usage=usage)

    def fake_send(request: model_access.Request) -> model_access.Outcome:
        seen_attempts.append(request.attempt)
        if len(seen_attempts) == 1:
            return model_access.Retry(next_attempt=1)
        return response

    monkeypatch.setattr(model_access, "send", fake_send)

    result = asyncio.run(complete(system="sys", messages=(), tools=(), provider="p", model="m"))
    assert result.content == "ok"
    assert seen_attempts == [0, 1]


@pytest.mark.unit
def test_complete_raises_context_overflow(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(model_access, "send", lambda request: model_access.NeedsContextCompression("too big"))
    with pytest.raises(ContextOverflow):
        asyncio.run(complete(system="sys", messages=(), tools=(), provider="p", model="m"))


@pytest.mark.unit
def test_complete_raises_provider_failure_for_needs_credential(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(model_access, "send", lambda request: model_access.NeedsCredentialOrProviderChange("no key"))
    with pytest.raises(ProviderFailure):
        asyncio.run(complete(system="sys", messages=(), tools=(), provider="p", model="m"))


@pytest.mark.unit
def test_complete_raises_provider_failure_for_abort(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(model_access, "send", lambda request: model_access.Abort("unclassifiable"))
    with pytest.raises(ProviderFailure):
        asyncio.run(complete(system="sys", messages=(), tools=(), provider="p", model="m"))


@pytest.mark.unit
def test_complete_raises_provider_failure_for_degenerate(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(model_access, "send", lambda request: model_access.Degenerate("empty"))
    with pytest.raises(ProviderFailure):
        asyncio.run(complete(system="sys", messages=(), tools=(), provider="p", model="m"))


@pytest.mark.unit
def test_complete_resolves_missing_ids_with_different_indices(monkeypatch: pytest.MonkeyPatch) -> None:
    raw_calls = (_raw_tool_call("same", "{}"), _raw_tool_call("same", "{}"))
    response = model_access.Response(
        content=None, tool_calls=raw_calls, finish_reason="tool_calls", usage=model_access.Usage()
    )
    monkeypatch.setattr(model_access, "send", lambda request: response)

    result = asyncio.run(complete(system="sys", messages=(), tools=(), provider="p", model="m"))
    ids = [tc["id"] for tc in result.tool_calls]
    assert len(ids) == 2
    assert ids[0] != ids[1]
    assert all(i.startswith("call_") for i in ids)


@pytest.mark.unit
def test_complete_deduplicates_shared_ids_via_uniquify(monkeypatch: pytest.MonkeyPatch) -> None:
    raw_calls = (
        _raw_tool_call("a", "{}", id_="dup"),
        _raw_tool_call("b", "{}", id_="dup"),
    )
    response = model_access.Response(
        content=None, tool_calls=raw_calls, finish_reason="tool_calls", usage=model_access.Usage()
    )
    monkeypatch.setattr(model_access, "send", lambda request: response)

    result = asyncio.run(complete(system="sys", messages=(), tools=(), provider="p", model="m"))
    ids = [tc["id"] for tc in result.tool_calls]
    assert ids == ["dup", "dup_d2"]


# ── turn loop ────────────────────────────────────────────────────────────

_SYSTEM_PROMPT = "You are a helpful assistant."


async def _fake_dispatch_ok(name: str, arguments: dict) -> str:
    return f"ran {name}"


def _tool_call_response(*names: str) -> model_access.Response:
    return model_access.Response(
        content=None,
        tool_calls=tuple({"function": {"name": n, "arguments": "{}"}} for n in names),
        finish_reason="tool_calls",
        usage=model_access.Usage(),
    )


def _text_response(text: str) -> model_access.Response:
    return model_access.Response(content=text, tool_calls=(), finish_reason="stop", usage=model_access.Usage())


def _run(surface: ToolSurface, **overrides):
    kwargs = {
        "conversation": "c1",
        "turn_seq": 0,
        "messages": (),
        "user_input": "hello",
        "system_prompt": _SYSTEM_PROMPT,
        "prompt_sha256": turn_prompt_hash(_SYSTEM_PROMPT, surface),
        "tool_surface": surface,
        "iteration_budget": IterationBudget(max_total=10),
        "wall_clock_budget": None,
        "now": 0.0,
        "provider": "p",
        "model": "m",
        "dispatch": _fake_dispatch_ok,
        "context_state": ContextState(),
        "stable_prompt_len": len(_SYSTEM_PROMPT),
    }
    kwargs.update(overrides)
    return asyncio.run(run_turn(**kwargs))


@pytest.mark.unit
def test_run_turn_completed_returns_final_text(monkeypatch: pytest.MonkeyPatch) -> None:
    surface = build_surface([_spec("noop", "noop")])
    monkeypatch.setattr(model_access, "send", lambda request: _text_response("hi there"))

    result, messages, _budget, _prompt = _run(surface)

    assert result.exit_reason == ExitReason.COMPLETED
    assert result.final_text == "hi there"
    assert result.model_calls == 1
    assert result.turn_key == TurnKey(conversation="c1", turn_seq=0)
    assert result.appended == range(0, len(messages))
    assert messages[-1].role == "assistant"
    assert messages[-1].content == "hi there"


@pytest.mark.unit
def test_run_turn_real_call_chain_marks_cache_boundary(monkeypatch: pytest.MonkeyPatch) -> None:
    """End-to-end through run_turn's own before_send/complete wiring, not
    just complete()'s own unit test above: a real turn's own system prompt
    shows up cache-marked in the request model_access.send actually sees."""
    surface = build_surface([_spec("noop", "noop")])
    captured: list[model_access.Request] = []

    def fake_send(request: model_access.Request) -> model_access.Outcome:
        captured.append(request)
        return _text_response("hi there")

    monkeypatch.setattr(model_access, "send", fake_send)

    _run(surface, stable_prompt_len=len(_SYSTEM_PROMPT))

    assert captured[0].messages[0]["content"] == [
        {"type": "text", "text": _SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}}
    ]


@pytest.mark.unit
def test_take_turn_accumulates_usage_across_turns(monkeypatch: pytest.MonkeyPatch) -> None:
    conversation, _t = create_conversation(_template(), "c1", "hi", iteration_budget=_budget())
    assert conversation.context_state == ContextState()

    usage1 = model_access.Usage(prompt_tokens=10, completion_tokens=2)
    monkeypatch.setattr(
        model_access,
        "send",
        lambda request: model_access.Response(content="one", tool_calls=(), finish_reason="stop", usage=usage1),
    )
    _result1, after_first = asyncio.run(
        take_turn(conversation, user_input="first", provider="p", model="m", dispatch=_fake_dispatch_ok, now=0.0)
    )
    assert after_first.context_state == ContextState(total_prompt_tokens=10, total_completion_tokens=2)

    usage2 = model_access.Usage(prompt_tokens=5, completion_tokens=1)
    monkeypatch.setattr(
        model_access,
        "send",
        lambda request: model_access.Response(content="two", tool_calls=(), finish_reason="stop", usage=usage2),
    )
    _result2, after_second = asyncio.run(
        take_turn(after_first, user_input="second", provider="p", model="m", dispatch=_fake_dispatch_ok, now=0.0)
    )
    assert after_second.context_state == ContextState(total_prompt_tokens=15, total_completion_tokens=3)


@pytest.mark.unit
def test_run_turn_budget_exhausted_summary_call(monkeypatch: pytest.MonkeyPatch) -> None:
    surface = build_surface([_spec("noop", "noop")])
    monkeypatch.setattr(model_access, "send", lambda request: _text_response("summary text"))

    result, _messages, budget, _prompt = _run(surface, iteration_budget=IterationBudget(max_total=0))

    assert result.exit_reason == ExitReason.BUDGET_EXHAUSTED
    assert result.final_text == "summary text"
    assert result.model_calls == 1  # only the epilogue summary call
    assert budget.max_total == 0


@pytest.mark.unit
def test_run_turn_wall_clock_exhausted(monkeypatch: pytest.MonkeyPatch) -> None:
    surface = build_surface([_spec("noop", "noop")])

    def fail_send(request: model_access.Request) -> model_access.Outcome:
        raise AssertionError("model_access.send should not be called")

    monkeypatch.setattr(model_access, "send", fail_send)

    result, _messages, _budget, _prompt = _run(surface, wall_clock_budget=WallClockBudget(deadline=100.0), now=200.0)

    assert result.exit_reason == ExitReason.WALL_CLOCK_EXHAUSTED
    assert result.model_calls == 0


@pytest.mark.unit
def test_run_turn_provider_failed(monkeypatch: pytest.MonkeyPatch) -> None:
    surface = build_surface([_spec("noop", "noop")])
    monkeypatch.setattr(model_access, "send", lambda request: model_access.Abort("bad request"))

    result, _messages, _budget, _prompt = _run(surface)

    assert result.exit_reason == ExitReason.PROVIDER_FAILED
    assert result.model_calls == 1  # a completed-but-failed attempt still counts, same as ContextOverflow


@pytest.mark.unit
def test_run_turn_context_overflow_unhandled(monkeypatch: pytest.MonkeyPatch) -> None:
    surface = build_surface([_spec("noop", "noop")])
    monkeypatch.setattr(model_access, "send", lambda request: model_access.NeedsContextCompression("too big"))

    result, _messages, _budget, _prompt = _run(surface)

    assert result.exit_reason == ExitReason.CONTEXT_OVERFLOW_UNHANDLED


@pytest.mark.unit
def test_run_turn_context_overflow_retries_with_compressed_prompt(monkeypatch: pytest.MonkeyPatch) -> None:
    surface = build_surface([_spec("noop", "noop")])
    outcomes = iter([model_access.NeedsContextCompression("too big"), _text_response("ok now")])
    calls = []

    def fake_send(request: model_access.Request) -> model_access.Outcome:
        calls.append(request)
        return next(outcomes)

    monkeypatch.setattr(model_access, "send", fake_send)

    def fake_turn_complete(
        state: ContextState, _messages: tuple[Message, ...], _system_prompt: str
    ) -> tuple[ContextState, str | None]:
        return state, "shorter prompt"

    monkeypatch.setattr(context, "turn_complete", fake_turn_complete)

    result, _messages, _budget, prompt = _run(surface)

    assert result.exit_reason == ExitReason.COMPLETED
    assert result.final_text == "ok now"
    assert prompt == "shorter prompt"
    assert len(calls) == 2


@pytest.mark.unit
def test_run_turn_interrupted(monkeypatch: pytest.MonkeyPatch) -> None:
    surface = build_surface([_spec("noop", "noop")])
    monkeypatch.setattr(model_access, "send", lambda request: _tool_call_response("noop"))

    async def slow_dispatch(name: str, arguments: dict) -> str:
        await asyncio.sleep(10)
        return "never"

    async def scenario():
        task = asyncio.ensure_future(
            run_turn(
                conversation="c1",
                turn_seq=0,
                messages=(),
                user_input="hello",
                system_prompt=_SYSTEM_PROMPT,
                prompt_sha256=turn_prompt_hash(_SYSTEM_PROMPT, surface),
                tool_surface=surface,
                iteration_budget=IterationBudget(max_total=10),
                wall_clock_budget=None,
                now=0.0,
                provider="p",
                model="m",
                dispatch=slow_dispatch,
                context_state=ContextState(),
                stable_prompt_len=len(_SYSTEM_PROMPT),
            )
        )
        # Real (short) sleep, not a fake clock: gives asyncio.to_thread's
        # real thread-pool round trip time to complete so the task is
        # actually inside slow_dispatch's own sleep before cancellation —
        # there is no fake event-loop clock in stdlib asyncio to drive
        # this deterministically instead. The assertion below does not
        # depend on how long that takes, only that INTERRUPTED is reached.
        await asyncio.sleep(0.1)
        task.cancel()
        return await task

    result, _messages, _budget, _prompt = asyncio.run(scenario())

    assert result.exit_reason == ExitReason.INTERRUPTED


@pytest.mark.unit
def test_run_turn_invalid_tool_calls_all_invalid(monkeypatch: pytest.MonkeyPatch) -> None:
    surface = build_surface([_spec("noop", "noop")])
    monkeypatch.setattr(model_access, "send", lambda request: _tool_call_response("nonexistent"))

    result, messages, _budget, _prompt = _run(surface)

    assert result.exit_reason == ExitReason.INVALID_TOOL_CALLS
    assert pending_tool_call_ids(messages) == frozenset()


@pytest.mark.unit
def test_run_turn_mixed_valid_invalid_continues(monkeypatch: pytest.MonkeyPatch) -> None:
    surface = build_surface([_spec("noop", "noop")])
    outcomes = iter([_tool_call_response("noop", "nonexistent"), _text_response("done")])
    monkeypatch.setattr(model_access, "send", lambda request: next(outcomes))

    result, _messages, _budget, _prompt = _run(surface)

    assert result.exit_reason == ExitReason.COMPLETED
    assert result.final_text == "done"
    assert result.model_calls == 2


@pytest.mark.unit
def test_run_turn_persistence_failed(monkeypatch: pytest.MonkeyPatch) -> None:
    surface = build_surface([_spec("noop", "noop")])
    monkeypatch.setattr(model_access, "send", lambda request: _tool_call_response("noop"))

    dispatch_called = []

    async def dispatch_tracker(name: str, arguments: dict) -> str:
        dispatch_called.append(name)
        return "ok"

    async def failing_persist(messages: tuple[Message, ...]) -> None:
        raise RuntimeError("disk full")

    result, _messages, _budget, _prompt = _run(surface, dispatch=dispatch_tracker, persist=failing_persist)

    assert result.exit_reason == ExitReason.PERSISTENCE_FAILED
    assert dispatch_called == []  # persist runs before any handler


@pytest.mark.unit
def test_run_turn_dispatch_raises_produces_tool_error(monkeypatch: pytest.MonkeyPatch) -> None:
    surface = build_surface([_spec("noop", "noop")])
    outcomes = iter([_tool_call_response("noop"), _text_response("done")])
    monkeypatch.setattr(model_access, "send", lambda request: next(outcomes))

    async def raising_dispatch(name: str, arguments: dict) -> str:
        raise ValueError("boom")

    result, messages, _budget, _prompt = _run(surface, dispatch=raising_dispatch)

    assert result.exit_reason == ExitReason.COMPLETED  # the turn continued past the error
    tool_messages = [m for m in messages if m.role == "tool"]
    assert tool_messages[0].content.startswith("tool_error:")


@pytest.mark.unit
def test_run_turn_calls_after_tool_result_before_appending(monkeypatch: pytest.MonkeyPatch) -> None:
    """Proves the TOOL_ROUND append site actually routes through
    context.after_tool_result rather than constructing the Message
    directly — cold review caught that, unlike turn_complete, nothing
    proved this call site is real rather than a silent no-op left over
    from a revert."""
    surface = build_surface([_spec("noop", "noop")])
    outcomes = iter([_tool_call_response("noop"), _text_response("done")])
    monkeypatch.setattr(model_access, "send", lambda request: next(outcomes))

    def fake_after_tool_result(_state: ContextState, result: Message) -> Message:
        return dataclasses.replace(result, content=f"reshaped:{result.content}")

    monkeypatch.setattr(context, "after_tool_result", fake_after_tool_result)

    _result, messages, _budget, _prompt = _run(surface)

    tool_messages = [m for m in messages if m.role == "tool"]
    assert tool_messages[0].content == "reshaped:ran noop"


@pytest.mark.unit
def test_run_turn_caps_oversized_tool_result(monkeypatch: pytest.MonkeyPatch) -> None:
    surface = build_surface([_spec("noop", "noop")])
    outcomes = iter([_tool_call_response("noop"), _text_response("done")])
    monkeypatch.setattr(model_access, "send", lambda request: next(outcomes))
    monkeypatch.setenv("SADANA_CONVERSATION_TOOL_RESULT_CHARS", "50")

    async def big_dispatch(name: str, arguments: dict) -> str:
        return "x" * 1000

    result, messages, _budget, _prompt = _run(surface, dispatch=big_dispatch)

    tool_messages = [m for m in messages if m.role == "tool"]
    assert len(tool_messages[0].content) < 1000
    assert "capped" in tool_messages[0].content


@pytest.mark.unit
def test_run_turn_caps_per_turn_budget_independently(monkeypatch: pytest.MonkeyPatch) -> None:
    surface = build_surface([_spec("noop", "noop")])
    first = model_access.Response(
        content=None,
        tool_calls=(
            {"function": {"name": "noop", "arguments": "{}"}},
            {"function": {"name": "noop", "arguments": '{"x": 1}'}},
        ),
        finish_reason="tool_calls",
        usage=model_access.Usage(),
    )
    outcomes = iter([first, _text_response("done")])
    monkeypatch.setattr(model_access, "send", lambda request: next(outcomes))
    monkeypatch.setenv("SADANA_CONVERSATION_TOOL_RESULT_CHARS", "1000")
    monkeypatch.setenv("SADANA_CONVERSATION_TOOL_TURN_BUDGET_CHARS", "150")

    async def medium_dispatch(name: str, arguments: dict) -> str:
        return "y" * 100

    result, messages, _budget, _prompt = _run(surface, dispatch=medium_dispatch)

    tool_messages = [m for m in messages if m.role == "tool"]
    assert len(tool_messages) == 2
    assert len(tool_messages[0].content) == 100  # fits under both caps
    assert "capped" in tool_messages[1].content  # pushed over the turn cap


@pytest.mark.unit
def test_run_turn_caps_invalid_tool_error_message(monkeypatch: pytest.MonkeyPatch) -> None:
    """A tool name comes from the model/provider — untrusted input, same as
    a dispatch result. An adversarial or misbehaving provider naming an
    enormous, nonexistent tool must not inject unbounded content into the
    transcript via the error message either."""
    surface = build_surface([_spec("noop", "noop")])
    huge_name = "x" * 1000
    monkeypatch.setattr(model_access, "send", lambda request: _tool_call_response(huge_name))
    monkeypatch.setenv("SADANA_CONVERSATION_TOOL_RESULT_CHARS", "50")

    result, messages, _budget, _prompt = _run(surface)

    assert result.exit_reason == ExitReason.INVALID_TOOL_CALLS
    tool_messages = [m for m in messages if m.role == "tool"]
    assert len(tool_messages[0].content) < 1000
    assert "capped" in tool_messages[0].content


@pytest.mark.unit
def test_run_turn_negative_result_cap_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    surface = build_surface([_spec("noop", "noop")])
    monkeypatch.setenv("SADANA_CONVERSATION_TOOL_RESULT_CHARS", "-1")
    with pytest.raises(ValueError):
        _run(surface)


@pytest.mark.unit
def test_run_turn_negative_turn_cap_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    surface = build_surface([_spec("noop", "noop")])
    monkeypatch.setenv("SADANA_CONVERSATION_TOOL_TURN_BUDGET_CHARS", "-1")
    with pytest.raises(ValueError):
        _run(surface)


@pytest.mark.unit
def test_run_turn_interrupted_during_epilogue_summary(monkeypatch: pytest.MonkeyPatch) -> None:
    """The CancelledError guard in the main loop doesn't automatically cover
    EPILOGUE's own extra complete() call for a budget-exhaustion summary —
    that needs its own guard, added after the cold review caught the gap."""
    surface = build_surface([_spec("noop", "noop")])

    def slow_send(request: model_access.Request) -> model_access.Outcome:
        time.sleep(0.3)  # blocks the to_thread worker only, not the event loop
        return _text_response("too late")

    monkeypatch.setattr(model_access, "send", slow_send)

    async def scenario():
        task = asyncio.ensure_future(
            run_turn(
                conversation="c1",
                turn_seq=0,
                messages=(),
                user_input="hello",
                system_prompt=_SYSTEM_PROMPT,
                prompt_sha256=turn_prompt_hash(_SYSTEM_PROMPT, surface),
                tool_surface=surface,
                iteration_budget=IterationBudget(max_total=0),  # straight to the epilogue summary call
                wall_clock_budget=None,
                now=0.0,
                provider="p",
                model="m",
                dispatch=_fake_dispatch_ok,
                context_state=ContextState(),
                stable_prompt_len=len(_SYSTEM_PROMPT),
            )
        )
        await asyncio.sleep(0.1)  # let it reach the epilogue's own complete() call
        task.cancel()
        return await task

    result, _messages, _budget, _prompt = asyncio.run(scenario())

    assert result.exit_reason == ExitReason.INTERRUPTED


@pytest.mark.unit
def test_turn_prompt_hash_changes_with_tool_surface() -> None:
    surface_a = build_surface([_spec("a", "alpha")])
    surface_b = build_surface([_spec("b", "beta")])
    assert turn_prompt_hash("same prompt", surface_a) != turn_prompt_hash("same prompt", surface_b)


@pytest.mark.unit
def test_run_turn_raises_prompt_drift_error() -> None:
    surface = build_surface([_spec("noop", "noop")])
    with pytest.raises(PromptDriftError):
        _run(surface, prompt_sha256="wrong-hash")


@pytest.mark.unit
def test_deduplicate_tool_calls_drops_duplicate() -> None:
    calls = (
        {"id": "1", "name": "a", "arguments": {"x": 1}},
        {"id": "2", "name": "a", "arguments": {"x": 1}},
    )
    result = _deduplicate_tool_calls(calls)
    assert len(result) == 1
    assert result[0]["id"] == "1"


@pytest.mark.unit
def test_deduplicate_tool_calls_leaves_unique_unchanged() -> None:
    calls = (
        {"id": "1", "name": "a", "arguments": {"x": 1}},
        {"id": "2", "name": "a", "arguments": {"x": 2}},
    )
    assert _deduplicate_tool_calls(calls) == calls


# ── conversation aggregate + template (CONV-06) ──────────────────────────


def _recipe(stable: str = "You are helpful.", catalog: tuple = (), tool_specs: tuple | None = None) -> TemplateRecipe:
    return TemplateRecipe(
        stable_prompt=stable,
        catalog=catalog,
        tool_specs=tool_specs if tool_specs is not None else (_spec("noop", "noop"),),
    )


def _template(name: str = "t1", recipe: TemplateRecipe | None = None) -> ConversationTemplate:
    return ConversationTemplate(name=name, recipe=recipe or _recipe())


def _budget(max_total: int = 5) -> IterationBudget:
    return IterationBudget(max_total=max_total)


@pytest.mark.unit
def test_create_conversation_prompt_sha256_matches_turn_prompt_hash() -> None:
    conversation, _t = create_conversation(_template(), "c1", "hi", iteration_budget=_budget())
    assert conversation.prompt_sha256 == turn_prompt_hash(conversation.system_prompt, conversation.tool_surface)


@pytest.mark.unit
def test_create_conversation_catalog_changes_the_hash() -> None:
    entry = PluginCatalogEntry(name="p1", purpose="does a thing", entry_tool="p1.start")
    template_plain = _template()
    template_with_catalog = _template(recipe=_recipe(catalog=(entry,)))

    conv_plain, _ = create_conversation(template_plain, "c1", "hi", iteration_budget=_budget())
    conv_with_catalog, _ = create_conversation(template_with_catalog, "c1", "hi", iteration_budget=_budget())

    assert conv_plain.system_prompt != conv_with_catalog.system_prompt
    assert conv_plain.prompt_sha256 != conv_with_catalog.prompt_sha256


@pytest.mark.unit
def test_defer_invalidation_returns_new_value_original_untouched() -> None:
    template = _template()
    new_recipe = _recipe(stable="You are different now.")

    updated = defer_invalidation(template, new_recipe)

    assert updated.pending_recipe == new_recipe
    assert template.pending_recipe is None
    assert template.recipe.stable_prompt == "You are helpful."


@pytest.mark.unit
def test_defer_invalidation_is_invisible_to_an_already_created_conversation() -> None:
    template = _template()
    conversation, template = create_conversation(template, "c1", "hi", iteration_budget=_budget())
    before = (conversation.system_prompt, conversation.prompt_sha256, conversation.tool_surface)

    defer_invalidation(template, _recipe(stable="A brand new identity."))

    assert (conversation.system_prompt, conversation.prompt_sha256, conversation.tool_surface) == before


@pytest.mark.unit
def test_create_conversation_promotes_pending_recipe_and_clears_it() -> None:
    template = _template()
    new_recipe = _recipe(stable="A brand new identity.")
    template = defer_invalidation(template, new_recipe)

    conversation, template = create_conversation(template, "c2", "hi", iteration_budget=_budget())

    assert conversation.system_prompt.startswith("A brand new identity.")
    assert template.pending_recipe is None
    assert template.recipe == new_recipe


@pytest.mark.unit
def test_create_conversation_reuses_promoted_recipe_without_further_defer() -> None:
    template = _template()
    new_recipe = _recipe(stable="A brand new identity.")
    template = defer_invalidation(template, new_recipe)
    _first, template = create_conversation(template, "c2", "hi", iteration_budget=_budget())

    second, _template2 = create_conversation(template, "c3", "hi", iteration_budget=_budget())

    assert second.system_prompt.startswith("A brand new identity.")


@pytest.mark.unit
def test_take_turn_completed_updates_conversation(monkeypatch: pytest.MonkeyPatch) -> None:
    conversation, _t = create_conversation(_template(), "c1", "hi", iteration_budget=_budget())
    monkeypatch.setattr(model_access, "send", lambda request: _text_response("hi there"))

    result, updated = asyncio.run(
        take_turn(
            conversation,
            user_input="hello",
            provider="p",
            model="m",
            dispatch=_fake_dispatch_ok,
            now=0.0,
        )
    )

    assert result.exit_reason == ExitReason.COMPLETED
    assert updated.messages[-2].role == "user"
    assert updated.messages[-2].content == "hello"
    assert updated.messages[-1].role == "assistant"
    assert updated.messages[-1].content == "hi there"
    assert updated.next_turn_seq == 1
    assert updated.iteration_budget.used == 1
    assert updated.prompt_epoch == 0


@pytest.mark.unit
def test_take_turn_compression_rotates_prompt_and_stays_self_consistent(monkeypatch: pytest.MonkeyPatch) -> None:
    conversation, _t = create_conversation(_template(), "c1", "hi", iteration_budget=_budget())

    outcomes = iter([model_access.NeedsContextCompression("too big"), _text_response("ok now")])
    monkeypatch.setattr(model_access, "send", lambda request: next(outcomes))

    def fake_turn_complete(
        state: ContextState, _messages: tuple[Message, ...], _system_prompt: str
    ) -> tuple[ContextState, str | None]:
        return state, "a shorter prompt"

    monkeypatch.setattr(context, "turn_complete", fake_turn_complete)

    result, updated = asyncio.run(
        take_turn(
            conversation,
            user_input="hello",
            provider="p",
            model="m",
            dispatch=_fake_dispatch_ok,
            now=0.0,
        )
    )

    assert result.exit_reason == ExitReason.COMPLETED
    assert updated.prompt_epoch == 1
    assert updated.prompt_sha256 == turn_prompt_hash(updated.system_prompt, updated.tool_surface)

    # Second call's mocked `send` completes immediately — turn_complete is
    # only ever invoked from the ContextOverflow branch, so it's never
    # called again here; the patch above staying in place is inert.
    monkeypatch.setattr(model_access, "send", lambda request: _text_response("second reply"))
    result2, _updated2 = asyncio.run(
        take_turn(
            updated,
            user_input="again",
            provider="p",
            model="m",
            dispatch=_fake_dispatch_ok,
            now=0.0,
        )
    )
    assert result2.exit_reason == ExitReason.COMPLETED  # no PromptDriftError raised


@pytest.mark.unit
def test_take_turn_twice_accumulates_one_shared_history(monkeypatch: pytest.MonkeyPatch) -> None:
    conversation, _t = create_conversation(_template(), "c1", "hi", iteration_budget=_budget())
    monkeypatch.setattr(model_access, "send", lambda request: _text_response("reply"))

    _result1, after_first = asyncio.run(
        take_turn(
            conversation,
            user_input="first",
            provider="p",
            model="m",
            dispatch=_fake_dispatch_ok,
            now=0.0,
        )
    )
    _result2, after_second = asyncio.run(
        take_turn(
            after_first,
            user_input="second",
            provider="p",
            model="m",
            dispatch=_fake_dispatch_ok,
            now=0.0,
        )
    )

    contents = [m.content for m in after_second.messages]
    assert "first" in contents
    assert "second" in contents
    assert len(after_second.messages) == 4
    assert after_second.next_turn_seq == 2


@pytest.mark.unit
def test_rotate_prompt_is_the_only_epoch_mutating_path(monkeypatch: pytest.MonkeyPatch) -> None:
    conversation, _t = create_conversation(_template(), "c1", "hi", iteration_budget=_budget())
    assert conversation.prompt_epoch == 0
    monkeypatch.setattr(model_access, "send", lambda request: _text_response("reply"))

    _result, updated = asyncio.run(
        take_turn(
            conversation,
            user_input="hello",
            provider="p",
            model="m",
            dispatch=_fake_dispatch_ok,
            now=0.0,
        )
    )
    assert updated.prompt_epoch == 0


# ── child conversation ───────────────────────────────────────────────────


def _write_skill(
    tmp_path: Path,
    *,
    plugin: str = "p1",
    skill: str = "s1",
    name: str | None = None,
    description: str | None = "Does one focused thing.",
    body: str = "Do the thing, then stop.",
) -> Path:
    skill_dir = tmp_path / "plugins" / plugin / "skills" / skill
    skill_dir.mkdir(parents=True)
    lines = [f"name: {skill if name is None else name}"]
    if description is not None:
        lines.append(f"description: {description}")
    (skill_dir / "SKILL.md").write_text("---\n" + "\n".join(lines) + f"\n---\n{body}")
    return tmp_path / "plugins"


def _install_skill(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, body: str = "Skill instructions.") -> SkillRef:
    root = _write_skill(tmp_path, body=body)
    monkeypatch.setenv("SADANA_PLUGINS_DIR", str(root))
    return SkillRef(plugin="p1", skill="s1")


@pytest.mark.unit
def test_load_skill_returns_body_for_valid_skill(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ref = _install_skill(tmp_path, monkeypatch, body="Do the thing, then stop.")
    assert load_skill(ref) == "Do the thing, then stop."


@pytest.mark.unit
def test_load_skill_raises_for_missing_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SADANA_PLUGINS_DIR", str(tmp_path / "plugins"))
    with pytest.raises(SkillLoadError):
        load_skill(SkillRef(plugin="nope", skill="nope"))


@pytest.mark.unit
def test_load_skill_raises_for_missing_frontmatter_delimiter(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    skill_dir = tmp_path / "plugins" / "p1" / "skills" / "s1"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("no frontmatter here")
    monkeypatch.setenv("SADANA_PLUGINS_DIR", str(tmp_path / "plugins"))
    with pytest.raises(SkillLoadError):
        load_skill(SkillRef(plugin="p1", skill="s1"))


@pytest.mark.unit
def test_load_skill_raises_for_unclosed_frontmatter(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    skill_dir = tmp_path / "plugins" / "p1" / "skills" / "s1"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("---\nname: s1\ndescription: x\nno closing delimiter")
    monkeypatch.setenv("SADANA_PLUGINS_DIR", str(tmp_path / "plugins"))
    with pytest.raises(SkillLoadError):
        load_skill(SkillRef(plugin="p1", skill="s1"))


@pytest.mark.unit
def test_load_skill_raises_for_name_mismatch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = _write_skill(tmp_path, name="a-different-name")
    monkeypatch.setenv("SADANA_PLUGINS_DIR", str(root))
    with pytest.raises(SkillLoadError):
        load_skill(SkillRef(plugin="p1", skill="s1"))


@pytest.mark.unit
def test_load_skill_raises_for_missing_description(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = _write_skill(tmp_path, description=None)
    monkeypatch.setenv("SADANA_PLUGINS_DIR", str(root))
    with pytest.raises(SkillLoadError):
        load_skill(SkillRef(plugin="p1", skill="s1"))


@pytest.mark.unit
def test_load_skill_raises_for_description_too_long(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = _write_skill(tmp_path, description="x" * 1025)
    monkeypatch.setenv("SADANA_PLUGINS_DIR", str(root))
    with pytest.raises(SkillLoadError):
        load_skill(SkillRef(plugin="p1", skill="s1"))


@pytest.mark.unit
def test_child_key_embeds_parent_and_node_name() -> None:
    assert _child_key("root", "search", 3) == "root/child/search/3"


@pytest.mark.unit
def test_conversation_depth_zero_for_bare_key() -> None:
    assert _conversation_depth("root") == 0


@pytest.mark.unit
def test_conversation_depth_counts_child_hops() -> None:
    once = _child_key("root", "a", 0)
    twice = _child_key(once, "b", 0)
    assert _conversation_depth(once) == 1
    assert _conversation_depth(twice) == 2


@pytest.mark.unit
def test_child_iteration_budget_from_config_defaults_to_20(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SADANA_CONVERSATION_CHILD_MAX_ITERATIONS", raising=False)
    assert child_iteration_budget_from_config().max_total == 20


@pytest.mark.unit
def test_child_iteration_budget_from_config_reads_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SADANA_CONVERSATION_CHILD_MAX_ITERATIONS", "7")
    assert child_iteration_budget_from_config().max_total == 7


@pytest.mark.unit
def test_child_iteration_budget_from_config_raises_for_negative(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SADANA_CONVERSATION_CHILD_MAX_ITERATIONS", "-1")
    with pytest.raises(ValueError):
        child_iteration_budget_from_config()


@pytest.mark.unit
def test_child_max_depth_from_config_defaults_to_2(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SADANA_CONVERSATION_CHILD_MAX_DEPTH", raising=False)
    assert child_max_depth_from_config() == 2


@pytest.mark.unit
def test_child_max_depth_from_config_reads_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SADANA_CONVERSATION_CHILD_MAX_DEPTH", "5")
    assert child_max_depth_from_config() == 5


@pytest.mark.unit
def test_child_max_depth_from_config_raises_for_negative(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SADANA_CONVERSATION_CHILD_MAX_DEPTH", "-1")
    with pytest.raises(ValueError):
        child_max_depth_from_config()


def _child_spec(ref: SkillRef, **overrides) -> ChildSpec:
    kwargs = {
        "node_name": "task",
        "skill": ref,
        "input": "do the thing",
        "tools": frozenset({"noop"}),
    }
    kwargs.update(overrides)
    return ChildSpec(**kwargs)


def _spawn(parent, spec: ChildSpec, **overrides):
    kwargs = {
        "stable_prompt": _SYSTEM_PROMPT,
        "provider": "p",
        "model": "m",
        "dispatch": _fake_dispatch_ok,
        "now": 0.0,
    }
    kwargs.update(overrides)
    return asyncio.run(run_child(parent, spec, **kwargs))


@pytest.mark.unit
def test_run_child_key_embeds_parent_and_node_name(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ref = _install_skill(tmp_path, monkeypatch)
    parent, _t = create_conversation(_template(), "parent", "hi", iteration_budget=_budget())
    monkeypatch.setattr(model_access, "send", lambda request: _text_response("done"))

    result, _child, _updated_parent = _spawn(parent, _child_spec(ref))

    assert result.turn_key.conversation == "parent/child/task/0"


@pytest.mark.unit
def test_run_child_two_spawns_produce_distinct_sequential_keys(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ref = _install_skill(tmp_path, monkeypatch)
    parent, _t = create_conversation(_template(), "parent", "hi", iteration_budget=_budget())
    monkeypatch.setattr(model_access, "send", lambda request: _text_response("done"))

    first_result, _c1, after_first = _spawn(parent, _child_spec(ref))
    second_result, _c2, _after_second = _spawn(after_first, _child_spec(ref))

    assert first_result.turn_key.conversation == "parent/child/task/0"
    assert second_result.turn_key.conversation == "parent/child/task/1"


@pytest.mark.unit
def test_run_child_restricts_tool_surface_to_spec_tools(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ref = _install_skill(tmp_path, monkeypatch)
    recipe = _recipe(tool_specs=(_spec("allowed", "allowed"), _spec("blocked", "blocked")))
    parent, _t = create_conversation(_template(recipe=recipe), "parent", "hi", iteration_budget=_budget())
    monkeypatch.setattr(model_access, "send", lambda request: _tool_call_response("blocked"))

    calls: list[str] = []

    async def recording_dispatch(name: str, arguments: dict) -> str:
        calls.append(name)
        return "unreachable"

    result, _child, _p = _spawn(parent, _child_spec(ref, tools=frozenset({"allowed"})), dispatch=recording_dispatch)

    assert result.exit_reason == ExitReason.INVALID_TOOL_CALLS
    assert calls == []


@pytest.mark.unit
def test_run_child_uses_given_budget_verbatim_even_over_config_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ref = _install_skill(tmp_path, monkeypatch)
    monkeypatch.setenv("SADANA_CONVERSATION_CHILD_MAX_ITERATIONS", "3")
    parent, _t = create_conversation(_template(), "parent", "hi", iteration_budget=_budget())
    monkeypatch.setattr(model_access, "send", lambda request: _text_response("done"))

    _result, child, _p = _spawn(parent, _child_spec(ref, budget=IterationBudget(max_total=999)))

    assert child.iteration_budget.max_total == 999


@pytest.mark.unit
def test_run_child_uses_config_default_budget_when_none_given(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ref = _install_skill(tmp_path, monkeypatch)
    monkeypatch.setenv("SADANA_CONVERSATION_CHILD_MAX_ITERATIONS", "7")
    parent, _t = create_conversation(_template(), "parent", "hi", iteration_budget=_budget())
    monkeypatch.setattr(model_access, "send", lambda request: _text_response("done"))

    _result, child, _p = _spawn(parent, _child_spec(ref))

    assert child.iteration_budget.max_total == 7


@pytest.mark.unit
def test_run_child_wall_clock_budget_is_the_same_object_as_parent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ref = _install_skill(tmp_path, monkeypatch)
    wall_clock = WallClockBudget(deadline=123.0)
    parent, _t = create_conversation(
        _template(), "parent", "hi", iteration_budget=_budget(), wall_clock_budget=wall_clock
    )
    monkeypatch.setattr(model_access, "send", lambda request: _text_response("done"))

    _result, child, _p = _spawn(parent, _child_spec(ref))

    assert child.wall_clock_budget is wall_clock


@pytest.mark.unit
def test_run_child_uses_given_model_over_parents(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ref = _install_skill(tmp_path, monkeypatch)
    parent, _t = create_conversation(_template(), "parent", "hi", iteration_budget=_budget())
    sent: list[model_access.Request] = []

    def record_send(request: model_access.Request) -> model_access.Response:
        sent.append(request)
        return _text_response("done")

    monkeypatch.setattr(model_access, "send", record_send)

    _spawn(parent, _child_spec(ref, model="cheap-model"), model="parent-model")

    assert sent[-1].model == "cheap-model"


@pytest.mark.unit
def test_run_child_inherits_parents_model_when_none_given(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ref = _install_skill(tmp_path, monkeypatch)
    parent, _t = create_conversation(_template(), "parent", "hi", iteration_budget=_budget())
    sent: list[model_access.Request] = []

    def record_send(request: model_access.Request) -> model_access.Response:
        sent.append(request)
        return _text_response("done")

    monkeypatch.setattr(model_access, "send", record_send)

    _spawn(parent, _child_spec(ref), model="parent-model")

    assert sent[-1].model == "parent-model"


@pytest.mark.unit
def test_run_child_raises_child_depth_exceeded_before_calling_provider_or_dispatch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ref = _install_skill(tmp_path, monkeypatch)
    monkeypatch.setenv("SADANA_CONVERSATION_CHILD_MAX_DEPTH", "1")
    # A parent whose own key already sits at depth 1 — one more hop would reach 2.
    parent, _t = create_conversation(_template(), "root/child/nodeA/0", "hi", iteration_budget=_budget())

    def fail_send(request: model_access.Request) -> model_access.Response:
        raise AssertionError("model_access.send should not be called")

    async def fail_dispatch(name: str, arguments: dict) -> str:
        raise AssertionError("dispatch should not be called")

    monkeypatch.setattr(model_access, "send", fail_send)

    with pytest.raises(ChildDepthExceeded):
        _spawn(parent, _child_spec(ref), dispatch=fail_dispatch)


@pytest.mark.unit
def test_run_child_never_mutates_parent_messages_only_bumps_next_child_seq(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ref = _install_skill(tmp_path, monkeypatch)
    parent, _t = create_conversation(_template(), "parent", "hi", iteration_budget=_budget())
    monkeypatch.setattr(model_access, "send", lambda request: _text_response("done"))

    _result, _child, updated_parent = _spawn(parent, _child_spec(ref))

    assert updated_parent.messages == parent.messages == ()
    assert updated_parent.next_child_seq == parent.next_child_seq + 1
    assert dataclasses.replace(updated_parent, next_child_seq=parent.next_child_seq) == parent


@pytest.mark.unit
def test_run_child_system_prompt_is_byte_identical_regardless_of_input(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ref = _install_skill(tmp_path, monkeypatch)
    parent, _t = create_conversation(_template(), "parent", "hi", iteration_budget=_budget())
    monkeypatch.setattr(model_access, "send", lambda request: _text_response("done"))

    _r1, child_a, _p1 = _spawn(parent, _child_spec(ref, node_name="a", input="first task"))
    _r2, child_b, _p2 = _spawn(parent, _child_spec(ref, node_name="b", input="a completely different task"))

    assert child_a.system_prompt == child_b.system_prompt
    assert child_a.prompt_sha256 == child_b.prompt_sha256
