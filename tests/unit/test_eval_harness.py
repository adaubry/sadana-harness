"""Tests for sadana.eval_harness: run_task(), save_result(), load_result()."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from pathlib import Path

import pytest

from sadana import model_access, plugins
from sadana.conversation import (
    Conversation,
    ConversationTemplate,
    ExitReason,
    IterationBudget,
    Message,
    TemplateRecipe,
    ToolSpec,
    TurnResult,
)
from sadana.eval_harness import Task, TaskRun, load_result, run_task, run_task_key, save_result

_TEMPLATE = ConversationTemplate(
    name="eval-test",
    recipe=TemplateRecipe(stable_prompt="You are a test fixture.", catalog=(), tool_specs=()),
)


def _text_response(text: str) -> model_access.Response:
    return model_access.Response(content=text, tool_calls=(), finish_reason="stop", usage=model_access.Usage())


def _exact_match_grader(
    expected: str,
) -> Callable[[TurnResult, tuple[Message, ...], tuple[plugins.DagResult, ...]], float]:
    def grade(result: TurnResult, _messages: tuple[Message, ...], _dag_results: tuple[plugins.DagResult, ...]) -> float:
        return 1.0 if result.final_text == expected else 0.0

    return grade


@pytest.mark.unit
def test_run_task_completed_and_graded(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(model_access, "send", lambda request: _text_response("PASS"))
    task = Task(task_id="smoke", prompt="say PASS", grade=_exact_match_grader("PASS"))

    run = asyncio.run(
        run_task(
            task,
            _TEMPLATE,
            provider="p",
            model="m",
            iteration_budget=IterationBudget(max_total=5),
            now=100.0,
        )
    )

    assert run.task_id == "smoke"
    assert run.exit_reason == ExitReason.COMPLETED
    assert run.final_text == "PASS"
    assert run.score == 1.0
    assert run.timestamp == 100.0


@pytest.mark.unit
def test_run_task_wrong_answer_scores_zero(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(model_access, "send", lambda request: _text_response("nope"))
    task = Task(task_id="smoke", prompt="say PASS", grade=_exact_match_grader("PASS"))

    run = asyncio.run(
        run_task(task, _TEMPLATE, provider="p", model="m", iteration_budget=IterationBudget(max_total=5), now=0.0)
    )

    assert run.exit_reason == ExitReason.COMPLETED
    assert run.score == 0.0


@pytest.mark.unit
def test_run_task_non_completed_exit_still_graded(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(model_access, "send", lambda request: _text_response("irrelevant"))
    grader_saw: list[ExitReason] = []

    def grade(result: TurnResult, _messages: tuple[Message, ...], _dag_results: tuple[plugins.DagResult, ...]) -> float:
        grader_saw.append(result.exit_reason)
        return 0.0

    task = Task(task_id="exhausted", prompt="go", grade=grade)

    run = asyncio.run(
        run_task(
            task,
            _TEMPLATE,
            provider="p",
            model="m",
            iteration_budget=IterationBudget(max_total=0),  # exhausted before the first model call
            now=0.0,
        )
    )

    assert run.exit_reason == ExitReason.BUDGET_EXHAUSTED
    assert grader_saw == [ExitReason.BUDGET_EXHAUSTED]  # the grader ran, not skipped, on a non-COMPLETED exit


def _tool_call_response(name: str) -> model_access.Response:
    return model_access.Response(
        content=None,
        tool_calls=({"function": {"name": name, "arguments": "{}"}},),
        finish_reason="tool_calls",
        usage=model_access.Usage(),
    )


@pytest.mark.unit
def test_run_task_with_real_dispatch_reaches_the_grader(monkeypatch: pytest.MonkeyPatch) -> None:
    """EVAL-02's own reason to exist: a task whose template mounts a tool
    and whose dispatch actually handles it — the grader sees the tool's
    result via the message history, not a placeholder. Also proves
    `dispatch_factory` receives the real `Conversation` `run_task()`
    built, not one the caller guessed at independently."""
    template = ConversationTemplate(
        name="eval-tool-test",
        recipe=TemplateRecipe(
            stable_prompt="You are a test fixture.",
            catalog=(),
            tool_specs=(
                ToolSpec(
                    key="noop_tool",
                    name="noop_tool",
                    parameters={"type": "object", "properties": {}},
                    describe=lambda _resolved: "does nothing",
                ),
            ),
        ),
    )
    responses = iter([_tool_call_response("noop_tool"), _text_response("done")])
    monkeypatch.setattr(model_access, "send", lambda request: next(responses))

    factory_saw_key: list[str] = []

    def dispatch_factory(conversation: Conversation) -> Callable[[str, dict], Awaitable[plugins.DagResult]]:
        factory_saw_key.append(conversation.key)

        async def dispatch(name: str, arguments: dict) -> plugins.DagResult:
            assert name == "noop_tool"
            return plugins.DagResult(
                plugin="test", entry="test", text="TOOL_RAN_OK", artifacts=(), trace=(), failed_node=None
            )

        return dispatch

    def grade(_result: TurnResult, messages: tuple[Message, ...], _dag_results: tuple[plugins.DagResult, ...]) -> float:
        tool_messages = [m for m in messages if m.role == "tool"]
        return 1.0 if any(m.content == "TOOL_RAN_OK" for m in tool_messages) else 0.0

    task = Task(task_id="tool_dispatch", prompt="call the tool", grade=grade)

    run = asyncio.run(
        run_task(
            task,
            template,
            provider="p",
            model="m",
            iteration_budget=IterationBudget(max_total=5),
            now=0.0,
            key="explicit-key",
            dispatch_factory=dispatch_factory,
        )
    )

    assert run.exit_reason == ExitReason.COMPLETED
    assert run.score == 1.0
    assert factory_saw_key == ["explicit-key"]  # the factory got the real conversation, not a guess


@pytest.mark.unit
def test_run_task_threads_captured_dag_results_to_the_grader(monkeypatch: pytest.MonkeyPatch) -> None:
    """G3-real-plugin-under-eval: task.grade's third parameter is every
    DagResult the turn's own dispatch produced, not just TurnResult and
    the message history."""
    template = ConversationTemplate(
        name="eval-dag-results-test",
        recipe=TemplateRecipe(
            stable_prompt="You are a test fixture.",
            catalog=(),
            tool_specs=(
                ToolSpec(
                    key="noop_tool",
                    name="noop_tool",
                    parameters={"type": "object", "properties": {}},
                    describe=lambda _resolved: "does nothing",
                ),
            ),
        ),
    )
    responses = iter([_tool_call_response("noop_tool"), _text_response("done")])
    monkeypatch.setattr(model_access, "send", lambda request: next(responses))

    known_result = plugins.DagResult(
        plugin="test", entry="noop_tool", text="ok", artifacts=(), trace=(), failed_node=None
    )

    def dispatch_factory(_conversation: Conversation) -> Callable[[str, dict], Awaitable[plugins.DagResult]]:
        async def dispatch(_name: str, _arguments: dict) -> plugins.DagResult:
            return known_result

        return dispatch

    seen_dag_results: list[tuple[plugins.DagResult, ...]] = []

    def grade(_result: TurnResult, _messages: tuple[Message, ...], dag_results: tuple[plugins.DagResult, ...]) -> float:
        seen_dag_results.append(dag_results)
        return 1.0

    task = Task(task_id="dag_results", prompt="call the tool", grade=grade)

    asyncio.run(
        run_task(
            task,
            template,
            provider="p",
            model="m",
            iteration_budget=IterationBudget(max_total=5),
            now=0.0,
            dispatch_factory=dispatch_factory,
        )
    )

    assert seen_dag_results == [(known_result,)]


@pytest.mark.unit
def test_run_task_key_is_readable_and_distinct_per_call() -> None:
    assert run_task_key("smoke", 1.0) != run_task_key("smoke", 2.0)
    assert run_task_key("smoke", 1.0) != run_task_key("other", 1.0)


@pytest.mark.unit
def test_save_result_then_load_result_round_trips(tmp_path: Path) -> None:
    run = TaskRun(
        task_id="smoke",
        score=0.75,
        exit_reason=ExitReason.COMPLETED,
        final_text="PASS",
        provider="openrouter",
        model="deepseek/deepseek-v4-flash-0731",
        timestamp=1234.5,
        detail=None,
    )

    path = save_result(run, tmp_path / "results")

    assert path.exists()
    assert path.parent == tmp_path / "results"
    assert load_result(path) == run


@pytest.mark.unit
def test_save_result_two_runs_of_same_task_do_not_collide(tmp_path: Path) -> None:
    results_dir = tmp_path / "results"
    first = TaskRun(
        task_id="smoke",
        score=0.0,
        exit_reason=ExitReason.COMPLETED,
        final_text="a",
        provider="p",
        model="m",
        timestamp=1.0,
    )
    second = TaskRun(
        task_id="smoke",
        score=1.0,
        exit_reason=ExitReason.COMPLETED,
        final_text="b",
        provider="p",
        model="m",
        timestamp=2.0,
    )

    path1 = save_result(first, results_dir)
    path2 = save_result(second, results_dir)

    assert path1 != path2
    assert load_result(path1) == first
    assert load_result(path2) == second
