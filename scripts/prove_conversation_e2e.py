#!/usr/bin/env python3
"""Standalone proof that a real, multi-turn conversation with two mounted
plugins keeps every promise the CONVERSATION block's prior work items made.

Not a pytest test — testing-conventions bars the network and the model API
from the unit suite. Run manually with a real OPENROUTER_API_KEY, paste its
output into review.md's ## Evidence. See
docs/tasks/CONV-09-deploy-evidence/spec.md for the full contract this
script proves.

Exercises: byte-stable system prompt across turns (spec.md req. 3), a
spawned child conversation with its own key/history/budget/restricted tool
surface (req. 4), deliberate budget exhaustion on a fourth turn (req. 5),
and a well-formed final transcript with no dangling tool calls (req. 6).
"""

from __future__ import annotations

import asyncio
import os
import sys
from dataclasses import replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from sadana import model_access  # noqa: E402
from sadana.conversation import (  # noqa: E402
    ChildSpec,
    Conversation,
    ConversationTemplate,
    ExitReason,
    IterationBudget,
    PluginCatalogEntry,
    SkillRef,
    TemplateRecipe,
    ToolSpec,
    create_conversation,
    pending_tool_call_ids,
    run_child,
    take_turn,
)

MODEL = "deepseek/deepseek-v4-flash-0731"
PROVIDER = "openrouter"
STABLE_PROMPT = (
    "You are a plainly-behaved assistant used only by sadana-harness's own "
    "CONV-09 proof script. Follow instructions exactly and literally."
)
_ACK_MARKER = "ACKNOWLEDGED"

FIXTURES_ROOT = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "plugins"


def _tool_specs() -> tuple[ToolSpec, ...]:
    return (
        ToolSpec(
            key="plugin_a_entry",
            name="plugin_a_entry",
            parameters={"type": "object", "properties": {}},
            describe=lambda _resolved: (
                "Runs plugin-a's flow: fetches a webhook-style input, hands it to a focused helper, and reports back."
            ),
        ),
        ToolSpec(
            key="plugin_b_entry",
            name="plugin_b_entry",
            parameters={
                "type": "object",
                "properties": {"note": {"type": "string", "description": "A short note to pass along."}},
                "required": ["note"],
            },
            describe=lambda _resolved: "Runs plugin-b's flow: hands a short note to a focused helper and reports back.",
        ),
    )


def _assert_child_isolated(child: Conversation, parent: Conversation) -> None:
    """The two checks both dispatch branches need on a spawned child: its
    own identity, its own history. Shared here so the two branches don't
    each restate them."""
    assert child.key != parent.key, "child must have its own key"
    assert len(child.messages) > 0, "child must have its own message history"


def _build_template() -> ConversationTemplate:
    recipe = TemplateRecipe(
        stable_prompt=STABLE_PROMPT,
        catalog=(
            PluginCatalogEntry(
                name="plugin-a",
                purpose="Runs a small three-step flow via a focused helper.",
                entry_tool="plugin_a_entry",
            ),
            PluginCatalogEntry(
                name="plugin-b",
                purpose="The simplest possible flow: one helper, one report.",
                entry_tool="plugin_b_entry",
            ),
        ),
        tool_specs=_tool_specs(),
    )
    return ConversationTemplate(name="conv09-proof", recipe=recipe)


async def main() -> None:
    os.environ["SADANA_PLUGINS_DIR"] = str(FIXTURES_ROOT)

    template = _build_template()
    conversation, _template = create_conversation(
        template,
        key="conv09-proof/run-1",
        system_message="This is a proof-of-concept conversation for sadana-harness's CONV-09 evidence.",
        iteration_budget=IterationBudget(max_total=5),
    )
    # The source of truth for how many children this run has spawned —
    # not `conversation.next_child_seq` itself, which take_turn()'s own
    # threading can't be told about mid-turn. See dispatch()'s docstring.
    next_child_seq = conversation.next_child_seq

    async def dispatch(name: str, arguments: dict) -> str:
        # take_turn() builds its own return value from the `conversation`
        # it was called with — a stale local snapshot nothing running
        # inside it (this function included) can retroactively change. So
        # a spawn's only real effect on the parent (next_child_seq
        # advancing — run_child's own docstring: nothing else differs)
        # is tracked here as its own counter instead, and reconciled back
        # onto `conversation` by main()'s own turn loop after each
        # take_turn() call returns — the one place that's actually safe
        # to do it. See docs/reference/dispatch_closure_state_bug.md.
        nonlocal next_child_seq
        parent = replace(conversation, next_child_seq=next_child_seq)
        now = 0.0  # wall-clock budget isn't exercised by this proof; a fixed value is fine.

        if name == "plugin_a_entry":
            # node 1: webhook-ish input node — a fixed stand-in; no real webhook exists (intent §Constraints).
            webhook_input = "incoming webhook payload: {'event': 'ping'}"
            # node 2: subagent-with-skill node.
            spec = ChildSpec(
                node_name="plugin_a_child",
                skill=SkillRef(plugin="plugin-a", skill="plugin-a-skill"),
                input=webhook_input,
                tools=frozenset(),
                budget=IterationBudget(max_total=3),
            )
            result, child, updated_parent = await run_child(
                parent,
                spec,
                stable_prompt=STABLE_PROMPT,
                provider=PROVIDER,
                model=MODEL,
                dispatch=dispatch,
                now=now,
            )
            next_child_seq = updated_parent.next_child_seq

            _assert_child_isolated(child, parent)
            # tool_surface == () is the strongest possible "excludes every
            # tool the parent has" proof — a name-exclusion check over an
            # already-known-empty tuple would only restate this.
            assert child.tool_surface == (), "plugin-a's child must be given zero tools"

            print(f"    [plugin-a] child key={child.key} exit={result.exit_reason} final_text={result.final_text!r}")

            # node 3: branch node.
            if result.final_text and _ACK_MARKER in result.final_text:
                return f"plugin-a: child acknowledged. report: {result.final_text}"
            return f"plugin-a: child did not acknowledge as expected. raw report: {result.final_text!r}"

        if name == "plugin_b_entry":
            spec = ChildSpec(
                node_name="plugin_b_child",
                skill=SkillRef(plugin="plugin-b", skill="plugin-b-skill"),
                input=str(arguments.get("note", "no note given")),
                tools=frozenset(),
                budget=IterationBudget(max_total=3),
            )
            result, child, updated_parent = await run_child(
                parent,
                spec,
                stable_prompt=STABLE_PROMPT,
                provider=PROVIDER,
                model=MODEL,
                dispatch=dispatch,
                now=now,
            )
            next_child_seq = updated_parent.next_child_seq

            _assert_child_isolated(child, parent)

            print(f"    [plugin-b] child key={child.key} exit={result.exit_reason} final_text={result.final_text!r}")
            return f"plugin-b: {result.final_text}"

        return f"tool_error: unknown tool {name!r}"

    initial_hash = conversation.prompt_sha256
    print(f"initial prompt_sha256={initial_hash}")

    # C10-context-lifecycle's own acceptance criterion: a real OpenRouter
    # request body must actually carry cache_control on the system
    # message's stable prefix. Wraps the real send() once, for turn 1 only,
    # to capture the exact wire request without faking anything about it.
    real_send = model_access.send
    captured_requests: list[model_access.Request] = []

    def _capturing_send(request: model_access.Request) -> model_access.Outcome:
        captured_requests.append(request)
        return real_send(request)

    model_access.send = _capturing_send

    print("\n=== turn 1: plain exchange, no plugin ===")
    result1, conversation = await take_turn(
        conversation,
        user_input="Reply with a short one-sentence greeting and nothing else.",
        provider=PROVIDER,
        model=MODEL,
        dispatch=dispatch,
        now=0.0,
    )
    model_access.send = real_send
    conversation = replace(conversation, next_child_seq=next_child_seq)
    print(f"exit_reason={result1.exit_reason} final_text={result1.final_text!r}")
    assert result1.exit_reason == ExitReason.COMPLETED, f"turn 1: expected COMPLETED, got {result1.exit_reason}"
    assert conversation.prompt_sha256 == initial_hash, "prompt_sha256 drifted after turn 1"
    print("[ok] turn 1 completed; prompt_sha256 unchanged")

    system_message = captured_requests[0].messages[0]
    print(f"    turn 1 real request system message content: {system_message['content']!r}")
    assert isinstance(
        system_message["content"], list
    ), "expected the system message to be cache-marked (a list of parts)"
    assert any(
        isinstance(part, dict) and "cache_control" in part for part in system_message["content"]
    ), "expected at least one cache_control marker on the real system message"
    print("[ok] turn 1's real request body carries a cache_control marker on the system message")

    print("\n=== turn 2: plugin-a ===")
    result2, conversation = await take_turn(
        conversation,
        user_input="Call the plugin_a_entry tool now.",
        provider=PROVIDER,
        model=MODEL,
        dispatch=dispatch,
        now=0.0,
    )
    conversation = replace(conversation, next_child_seq=next_child_seq)
    print(f"exit_reason={result2.exit_reason} final_text={result2.final_text!r}")
    assert result2.exit_reason == ExitReason.COMPLETED, f"turn 2: expected COMPLETED, got {result2.exit_reason}"
    assert conversation.prompt_sha256 == initial_hash, "prompt_sha256 drifted after turn 2"
    print("[ok] turn 2 completed; prompt_sha256 unchanged; child isolation verified above")

    print("\n=== turn 3: plugin-b ===")
    result3, conversation = await take_turn(
        conversation,
        user_input="Call the plugin_b_entry tool now, with note='hello from turn 3'.",
        provider=PROVIDER,
        model=MODEL,
        dispatch=dispatch,
        now=0.0,
    )
    conversation = replace(conversation, next_child_seq=next_child_seq)
    print(f"exit_reason={result3.exit_reason} final_text={result3.final_text!r}")
    assert result3.exit_reason == ExitReason.COMPLETED, f"turn 3: expected COMPLETED, got {result3.exit_reason}"
    assert conversation.prompt_sha256 == initial_hash, "prompt_sha256 drifted after turn 3"
    print("[ok] turn 3 completed; prompt_sha256 unchanged")
    print(
        f"iteration_budget after turn 3: {conversation.iteration_budget.used}/{conversation.iteration_budget.max_total}"
    )

    print("\n=== turn 4: forced budget exhaustion ===")
    result4, conversation = await take_turn(
        conversation,
        user_input="Reply with a short one-sentence greeting and nothing else.",
        provider=PROVIDER,
        model=MODEL,
        dispatch=dispatch,
        now=0.0,
    )
    conversation = replace(conversation, next_child_seq=next_child_seq)
    print(f"exit_reason={result4.exit_reason} detail={result4.detail!r}")
    assert result4.exit_reason == ExitReason.BUDGET_EXHAUSTED, (
        f"turn 4: expected BUDGET_EXHAUSTED, got {result4.exit_reason} — "
        f"turns 1-3 used {conversation.iteration_budget.used}/{conversation.iteration_budget.max_total}; "
        "raise IterationBudget(max_total=...) above if turns 1-3 cost more than expected"
    )
    print("[ok] turn 4 exhausted the budget as intended")

    pending = pending_tool_call_ids(conversation.messages)
    assert pending == frozenset(), f"final transcript has dangling tool_call ids: {pending}"
    print("[ok] final transcript is well-formed: no dangling tool calls")

    print("\nALL ASSERTIONS PASSED")


if __name__ == "__main__":
    if not os.environ.get("OPENROUTER_API_KEY"):
        print("OPENROUTER_API_KEY is not set in the real environment. Set it and re-run.", file=sys.stderr)
        raise SystemExit(1)
    asyncio.run(main())
