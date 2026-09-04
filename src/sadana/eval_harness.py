"""A check on the agent's actual behaviour can be written once and run again.

EVAL-01: full contract is `docs/tasks/EVAL-01-task-runner-core/spec.md`.

A `Task` is a prompt plus a plain, programmatic grading function — never a
second model asked to render an opinion (spec.md's own audit of hermes's
`evals/core_tool_deferral`/`readtool`/`browser_use`: none of the three use
an LLM-as-judge either). `run_task()` drives the real
`create_conversation()`/`take_turn()` machinery this project already has,
against a real model, for exactly one turn. `save_result()` writes one
JSON file per run — no resume/dedup machinery, no battery of tasks exists
yet to make that worth building.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path

from sadana import config
from sadana.conversation import (
    ConversationKey,
    ConversationTemplate,
    ExitReason,
    IterationBudget,
    Message,
    TurnResult,
    create_conversation,
    take_turn,
)


@dataclass(frozen=True)
class Task:
    """One behavioural check: what to ask, and how to judge the answer.

    ``grade`` reads the turn's own ``TurnResult`` and resulting message
    history directly — no new type wraps them; `conversation.py` already
    defines both, and a grader reading them is the smallest surface that
    could work (spec.md's own guideline-3 reasoning)."""

    task_id: str
    prompt: str
    grade: Callable[[TurnResult, tuple[Message, ...]], float]


@dataclass(frozen=True)
class TaskRun:
    """One task's own recorded outcome."""

    task_id: str
    score: float
    exit_reason: ExitReason
    final_text: str | None
    provider: str
    model: str
    timestamp: float
    detail: str | None = None


async def _no_tools_dispatch(name: str, arguments: dict) -> str:
    """Satisfies `take_turn()`'s required `dispatch` parameter (no
    default) for a task whose template offers no tools — never actually
    invoked, since a model with nothing to call has nothing to ask for."""
    return f"tool_error: no tools available (unexpected call to {name!r} with {arguments!r})"


async def _compress_noop(_messages: tuple[Message, ...], _system_prompt: str) -> str | None:
    return None  # compression isn't built yet; matches every other caller's own precedent.


def run_task_key(task_id: str, now: float) -> ConversationKey:
    """The default `key` a caller gets if it doesn't supply its own —
    readable, but not a uniqueness guarantee (spec.md's own Concerns:
    nothing here persists a conversation, so a collision has no observable
    consequence today)."""
    return f"eval/{task_id}/{now}"


async def run_task(
    task: Task,
    template: ConversationTemplate,
    *,
    provider: str,
    model: str,
    iteration_budget: IterationBudget,
    now: float,
    key: ConversationKey | None = None,
) -> TaskRun:
    """Builds a fresh `Conversation` from `template`, takes exactly one
    turn with `task.prompt`, grades the result with `task.grade`, returns
    a `TaskRun`. A turn that doesn't `COMPLETED` is still graded and
    recorded — `task.grade` decides what that's worth, not this function."""
    conversation, _template = create_conversation(
        template,
        key=key if key is not None else run_task_key(task.task_id, now),
        system_message="",
        iteration_budget=iteration_budget,
    )
    result, updated = await take_turn(
        conversation,
        user_input=task.prompt,
        provider=provider,
        model=model,
        dispatch=_no_tools_dispatch,
        compress=_compress_noop,
        now=now,
    )
    score = task.grade(result, updated.messages)
    return TaskRun(
        task_id=task.task_id,
        score=score,
        exit_reason=result.exit_reason,
        final_text=result.final_text,
        provider=provider,
        model=model,
        timestamp=now,
        detail=result.detail,
    )


def results_dir_from_config() -> Path:
    """``SADANA_EVAL_RESULTS_DIR``, default
    ``config.get_paths().state_dir / "eval" / "results"`` — same pattern
    as ``conversation_store.py``'s ``store_path_from_config()``, not a
    path improvised relative to whichever script happens to call this."""
    return config.env_path(
        "SADANA_EVAL_RESULTS_DIR",
        default=config.get_paths().state_dir / "eval" / "results",
    )


def save_result(run: TaskRun, results_dir: Path) -> Path:
    """Writes one JSON file per run to
    `results_dir/<task_id>__<int(timestamp)>.json`, creating `results_dir`
    if needed. Never overwrites — a second run of the same task at a
    different `timestamp` gets its own file, by construction."""
    results_dir.mkdir(parents=True, exist_ok=True)
    path = results_dir / f"{run.task_id}__{int(run.timestamp)}.json"
    payload = asdict(run)
    payload["exit_reason"] = run.exit_reason.value
    path.write_text(json.dumps(payload, indent=2, sort_keys=True))
    return path


def load_result(path: Path) -> TaskRun:
    """The inverse of `save_result()` — reads one JSON file back into a
    `TaskRun`, used by its own round-trip test."""
    payload = json.loads(path.read_text())
    payload["exit_reason"] = ExitReason(payload["exit_reason"])
    return TaskRun(**payload)
