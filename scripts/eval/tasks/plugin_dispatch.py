#!/usr/bin/env python3
"""EVAL-02: one real turn proves the whole pipeline is actually right.

The first real, keep-forever eval task — not a `scripts/prove_*.py`
mechanism proof. Checks genuine plugin dispatch: does the model, told
about one mounted add-on procedure, actually call it when the prompt
asks for it. Reuses CONV-09/CONV-10's own `plugin-a` fixture
(`tests/fixtures/plugins/plugin-a/skills/plugin-a-skill`) as-is — the
same webhook-style-input -> child-spawn -> branch shape, not a
reimplementation. See docs/tasks/EVAL-02-one-real-turn/spec.md for the
full contract.

Not a pytest test — testing-conventions bars the network and the model API
from the unit suite. Run manually with a real OPENROUTER_API_KEY, paste its
output into review.md's ## Evidence.

"One real turn" describes the observable unit (one prompt in, one graded
response out), not a single model call: internally this costs three real
calls — the parent asking for the tool, the spawned child's own
completion, the parent's final response after the tool result — the same
shape CONV-09's own turn 2 already proved (spec.md §Design).
"""

from __future__ import annotations

import asyncio
import os
import sys
from collections.abc import Awaitable, Callable
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent.parent / "src"))

from sadana.conversation import (  # noqa: E402
    ChildSpec,
    Conversation,
    ConversationTemplate,
    ExitReason,
    IterationBudget,
    Message,
    PluginCatalogEntry,
    SkillRef,
    TemplateRecipe,
    ToolSpec,
    TurnResult,
    run_child,
)
from sadana.eval_harness import Task, results_dir_from_config, run_task, save_result  # noqa: E402

MODEL = "deepseek/deepseek-v4-flash-0731"
PROVIDER = "openrouter"
STABLE_PROMPT = (
    "You are a plainly-behaved assistant used only by sadana-harness's own "
    "EVAL-02 real-turn task. Follow instructions exactly and literally."
)
_ACK_MARKER = "ACKNOWLEDGED"
_TASK_KEY = "eval/plugin_dispatch/0"

FIXTURES_ROOT = Path(__file__).resolve().parent.parent.parent.parent / "tests" / "fixtures" / "plugins"


def build_template() -> ConversationTemplate:
    recipe = TemplateRecipe(
        stable_prompt=STABLE_PROMPT,
        catalog=(
            PluginCatalogEntry(
                name="plugin-a",
                purpose="Runs a small flow via a focused helper.",
                entry_tool="plugin_a_entry",
            ),
        ),
        tool_specs=(
            ToolSpec(
                key="plugin_a_entry",
                name="plugin_a_entry",
                parameters={"type": "object", "properties": {}},
                describe=lambda _resolved: (
                    "Runs plugin-a's flow: fetches a webhook-style input, hands it to a "
                    "focused helper, and reports back."
                ),
            ),
        ),
    )
    return ConversationTemplate(name="eval02-plugin-dispatch", recipe=recipe)


def make_dispatch(parent: Conversation) -> Callable[[str, dict], Awaitable[str]]:
    """A `dispatch_factory` for `run_task()` (EVAL-02's own addition to
    `eval_harness.py`): receives the real `Conversation` `run_task()`
    built, so `run_child()` below gets the actual parent, never an
    independently-guessed one. This task calls `run_child()` exactly
    once, so there is no second spawn's `next_child_seq` to propagate —
    CONV-10's propagation fix has nothing to apply to here."""

    async def dispatch(name: str, arguments: dict) -> str:
        webhook_input = "incoming webhook payload: {'event': 'ping'}"
        spec = ChildSpec(
            node_name="plugin_a_child",
            skill=SkillRef(plugin="plugin-a", skill="plugin-a-skill"),
            input=webhook_input,
            tools=frozenset(),
            budget=IterationBudget(max_total=3),
        )
        result, _child, _updated_parent = await run_child(
            parent,
            spec,
            stable_prompt=STABLE_PROMPT,
            provider=PROVIDER,
            model=MODEL,
            dispatch=dispatch,
            now=0.0,
        )

        if result.final_text and _ACK_MARKER in result.final_text:
            return f"plugin-a: child acknowledged. report: {result.final_text}"
        return f"plugin-a: child did not acknowledge as expected. raw report: {result.final_text!r}"

    return dispatch


def grade(_result: TurnResult, messages: tuple[Message, ...]) -> float:
    """Structural grading (hermes's own `core_tool_deferral` shape, no
    LLM-as-judge): did the model call `plugin_a_entry`, and did the tool
    result show the spawned child correctly acknowledged. Partial credit
    for "called but didn't come back right" — the mechanism partly
    worked; spec.md §Concerns names this as this task's own judgement
    call, not a project-wide scale."""
    called = any(m.role == "assistant" and any(tc["name"] == "plugin_a_entry" for tc in m.tool_calls) for m in messages)
    if not called:
        return 0.0
    acknowledged = any(m.role == "tool" and m.content and "child acknowledged" in m.content for m in messages)
    return 1.0 if acknowledged else 0.5


TASK = Task(
    task_id="plugin_dispatch",
    prompt="Call the plugin_a_entry tool now.",
    grade=grade,
)


async def main() -> None:
    os.environ["SADANA_PLUGINS_DIR"] = str(FIXTURES_ROOT)

    template = build_template()

    run = await run_task(
        TASK,
        template,
        provider=PROVIDER,
        model=MODEL,
        iteration_budget=IterationBudget(max_total=3),
        now=0.0,
        key=_TASK_KEY,
        dispatch_factory=make_dispatch,
    )

    print(f"task_id={run.task_id}")
    print(f"exit_reason={run.exit_reason}")
    print(f"final_text={run.final_text!r}")
    print(f"score={run.score}")

    assert run.exit_reason == ExitReason.COMPLETED, f"expected COMPLETED, got {run.exit_reason}"
    assert run.score == 1.0, f"expected a full score (tool called and child acknowledged), got {run.score}"
    print("[ok] plugin dispatch verified end to end")

    path = save_result(run, results_dir_from_config())
    print(f"[ok] result saved to {path}")

    print("\nALL ASSERTIONS PASSED")


if __name__ == "__main__":
    if not os.environ.get("OPENROUTER_API_KEY"):
        print("OPENROUTER_API_KEY is not set in the real environment. Set it and re-run.", file=sys.stderr)
        raise SystemExit(1)
    asyncio.run(main())
