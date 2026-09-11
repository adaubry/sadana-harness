#!/usr/bin/env python3
"""EVAL-02: one real turn proves the whole pipeline is actually right.

The first real, keep-forever eval task — not a `scripts/prove_*.py`
mechanism proof. Checks genuine plugin dispatch: does the model, told
about one mounted add-on procedure, actually call it when the prompt
asks for it. Drives the real, on-disk `plugin-a` fixture
(`tests/fixtures/plugins/plugin-a`) through the actual dispatch
mechanism — `discover_plugins()` -> `build_plugin_set()` ->
`build_dispatch()` -> `run_graph()` -> a real `run_child()` call — not a
hand-written, per-plugin dispatch closure. See
docs/tasks/EVAL-02-one-real-turn/spec.md and
docs/tasks/G3-real-plugin-under-eval/spec.md for the full contract.

Not a pytest test — testing-conventions bars the network and the model API
from the unit suite. Run manually with a real OPENROUTER_API_KEY, paste its
output into review.md's ## Evidence.

"One real turn" describes the observable unit (one prompt in, one graded
response out), not a single model call: internally this costs three real
calls — the parent asking for the tool, the spawned child's own
completion, the parent's final response after the tool result — the same
shape CONV-09's own turn 2 already proved (spec.md §Design).

`grade()` reads the turn's own captured `DagResult.trace` — G3's own point:
a `Task` whose grading function reads structure, never a substring match
against rendered message content (CLAUDE.md).
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent.parent / "src"))

from sadana import plugin_dispatch, plugin_manifest, plugins  # noqa: E402
from sadana.conversation import (  # noqa: E402
    Conversation,
    ConversationTemplate,
    ExitReason,
    IterationBudget,
    Message,
    TemplateRecipe,
    TurnResult,
)
from sadana.eval_harness import Task, results_dir_from_config, run_task, save_result  # noqa: E402

MODEL = "deepseek/deepseek-v4-flash-0731"
PROVIDER = "openrouter"
STABLE_PROMPT = (
    "You are a plainly-behaved assistant used only by sadana-harness's own "
    "EVAL-02 real-turn task. Follow instructions exactly and literally."
)
_TASK_KEY = "eval/plugin_dispatch/0"

FIXTURES_ROOT = Path(__file__).resolve().parent.parent.parent.parent / "tests" / "fixtures" / "plugins"


async def auto_approve(plugin: str, node: str, _value: object) -> bool:
    """This task's own honest stand-in for a person — answers the real
    approval question every time, without waiting on one
    (docs/tasks/G3-real-plugin-under-eval/spec.md)."""
    print(f"    [auto-approved] {plugin}'s {node!r} step")
    return True


def grade(_result: TurnResult, _messages: tuple[Message, ...], dag_results: tuple[plugins.DagResult, ...]) -> float:
    """Structural grading (hermes's own `core_tool_deferral` shape, no
    LLM-as-judge): did plugin-a's own DAG actually run, and did it reach
    the `acknowledged` port. Partial credit for "ran but didn't come back
    right" — the mechanism partly worked; spec.md §Concerns names this as
    this task's own judgement call, not a project-wide scale."""
    if not dag_results:
        return 0.0
    dag = dag_results[-1]
    if dag.failed_node is not None:
        return 0.0
    return 1.0 if any(t.port == "acknowledged" for t in dag.trace) else 0.5


TASK = Task(
    task_id="plugin_dispatch",
    prompt="Call the plugin_a_entry tool now.",
    grade=grade,
)


async def main() -> None:
    os.environ["SADANA_PLUGINS_DIR"] = str(FIXTURES_ROOT)

    installed = plugin_manifest.discover_plugins()
    plugin_set = plugin_dispatch.build_plugin_set(p for p in installed if p.name == "plugin-a")
    assert "plugin_a_entry" in plugin_set.by_tool, f"plugin_a_entry missing from the built PluginSet: {installed}"

    template = ConversationTemplate(
        name="eval02-plugin-dispatch",
        recipe=TemplateRecipe(
            stable_prompt=STABLE_PROMPT, catalog=plugin_set.catalog, tool_specs=plugin_set.tool_specs
        ),
    )

    def dispatch_factory(conversation: Conversation) -> plugin_dispatch.DispatchFn:
        dispatch, _tracker = plugin_dispatch.build_dispatch(
            conversation,
            plugin_set,
            provider=PROVIDER,
            model=MODEL,
            now=0.0,
            approve=auto_approve,
        )
        return dispatch

    run = await run_task(
        TASK,
        template,
        provider=PROVIDER,
        model=MODEL,
        iteration_budget=IterationBudget(max_total=3),
        now=0.0,
        key=_TASK_KEY,
        dispatch_factory=dispatch_factory,
    )

    print(f"task_id={run.task_id}")
    print(f"exit_reason={run.exit_reason}")
    print(f"final_text={run.final_text!r}")
    print(f"score={run.score}")

    assert run.exit_reason == ExitReason.COMPLETED, f"expected COMPLETED, got {run.exit_reason}"
    assert run.score == 1.0, f"expected a full score (plugin-a ran and acknowledged), got {run.score}"
    print("[ok] plugin dispatch verified end to end, graded by structure")

    path = save_result(run, results_dir_from_config())
    print(f"[ok] result saved to {path}")

    print("\nALL ASSERTIONS PASSED")


if __name__ == "__main__":
    if not os.environ.get("OPENROUTER_API_KEY"):
        print("OPENROUTER_API_KEY is not set in the real environment. Set it and re-run.", file=sys.stderr)
        raise SystemExit(1)
    asyncio.run(main())
