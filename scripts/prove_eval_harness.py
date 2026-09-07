#!/usr/bin/env python3
"""Standalone proof that the eval harness genuinely drives the real
conversation loop against a real model and produces a graded, saved
record — not that the harness's own claims about itself are trusted.

Not a pytest test — testing-conventions bars the network and the model API
from the unit suite. Run manually with a real OPENROUTER_API_KEY, paste its
output into review.md's ## Evidence. See
docs/tasks/EVAL-01-task-runner-core/spec.md for the full contract.

This is one deliberately minimal, throwaway task — proving the mechanism,
not a check worth keeping. Choosing what this project should actually
check for is separate, later work (EVAL-03).
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from sadana import plugins  # noqa: E402
from sadana.conversation import (  # noqa: E402
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


def _exact_match(expected: str):
    def grade(result: TurnResult, _messages: tuple[Message, ...], _dag_results: tuple[plugins.DagResult, ...]) -> float:
        return 1.0 if (result.final_text or "").strip() == expected else 0.0

    return grade


SMOKE_TASK = Task(
    task_id="smoke_exact_reply",
    prompt="Reply with exactly the single word PASS and nothing else — no punctuation, no explanation.",
    grade=_exact_match("PASS"),
)


async def main() -> None:
    template = ConversationTemplate(
        name="eval-smoke",
        recipe=TemplateRecipe(
            stable_prompt=(
                "You are a plainly-behaved assistant used only by sadana-harness's own "
                "eval-harness proof script. Follow instructions exactly and literally."
            ),
            catalog=(),
            tool_specs=(),
        ),
    )

    run = await run_task(
        SMOKE_TASK,
        template,
        provider=PROVIDER,
        model=MODEL,
        iteration_budget=IterationBudget(max_total=3),
        now=0.0,
    )

    print(f"task_id={run.task_id}")
    print(f"exit_reason={run.exit_reason}")
    print(f"final_text={run.final_text!r}")
    print(f"score={run.score}")

    assert run.exit_reason == ExitReason.COMPLETED, f"expected COMPLETED, got {run.exit_reason}"
    print("[ok] task completed")

    path = save_result(run, results_dir_from_config())
    print(f"[ok] result saved to {path}")
    assert path.exists()

    print("\nALL ASSERTIONS PASSED")


if __name__ == "__main__":
    if not os.environ.get("OPENROUTER_API_KEY"):
        print("OPENROUTER_API_KEY is not set in the real environment. Set it and re-run.", file=sys.stderr)
        raise SystemExit(1)
    asyncio.run(main())
