#!/usr/bin/env python3
"""Standalone proof that a real, multi-turn conversation with two mounted
plugins keeps every promise the CONVERSATION block's prior work items made
— and, since docs/tasks/G3-real-plugin-under-eval/spec.md, that both
plugins are real, on-disk manifests walked through the actual dispatch
mechanism, not a hand-written closure.

Not a pytest test — testing-conventions bars the network and the model API
from the unit suite. Run manually with a real OPENROUTER_API_KEY, paste its
output into review.md's ## Evidence. See
docs/tasks/CONV-09-deploy-evidence/spec.md for the original contract and
docs/tasks/G3-real-plugin-under-eval/spec.md for what changed here.

Exercises: byte-stable system prompt across turns (spec.md req. 3), a
spawned child conversation with its own key/history/budget/restricted tool
surface (req. 4, now proven once as a unit test —
tests/unit/test_conversation.py — rather than by this script's own direct
inspection, per G3's own Concerns), deliberate budget exhaustion on a
fourth turn (req. 5), a well-formed final transcript with no dangling tool
calls (req. 6), and — new — plugin-a's real `call` node reaching outside
for real, under real approval, and handing back a link `Artifact`.
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from sadana import model_access, plugin_dispatch, plugin_manifest, plugins  # noqa: E402
from sadana.conversation import (  # noqa: E402
    Conversation,
    ConversationTemplate,
    ExitReason,
    IterationBudget,
    TemplateRecipe,
    create_conversation,
    pending_tool_call_ids,
)

MODEL = "deepseek/deepseek-v4-flash-0731"
PROVIDER = "openrouter"
STABLE_PROMPT = (
    "You are a plainly-behaved assistant used only by sadana-harness's own "
    "CONV-09 proof script. Follow instructions exactly and literally."
)

FIXTURES_ROOT = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "plugins"


def _print_dag_result(label: str, result: plugins.DagResult) -> None:
    print(f"{label}: plugin={result.plugin!r} entry={result.entry!r} failed_node={result.failed_node!r}")
    for t in result.trace:
        print(f"  node={t.node!r} kind={t.kind!r} ok={t.ok} port={t.port!r} detail={t.detail!r}")
    for a in result.artifacts:
        print(f"  artifact kind={a.kind!r} name={a.name!r} ref={a.ref!r}")


async def main() -> None:
    os.environ["SADANA_PLUGINS_DIR"] = str(FIXTURES_ROOT)

    approved_calls: list[tuple[str, str]] = []

    async def auto_approve(plugin: str, node: str, _value: object) -> bool:
        """This script's own honest stand-in for a person — answers the
        real approval question every time, without waiting on one
        (docs/tasks/G3-real-plugin-under-eval/spec.md)."""
        approved_calls.append((plugin, node))
        print(f"    [auto-approved] {plugin}'s {node!r} step")
        return True

    installed = plugin_manifest.discover_plugins()
    names = sorted(p.name for p in installed)
    print(f"discover_plugins() found: {names}")
    assert "plugin-a" in names and "plugin-b" in names, f"expected plugin-a and plugin-b among {names}"

    plugin_set = plugin_dispatch.build_plugin_set(p for p in installed if p.name in ("plugin-a", "plugin-b"))
    print(f"catalog entry_tools: {[e.entry_tool for e in plugin_set.catalog]}")

    template = ConversationTemplate(
        name="conv09-proof",
        recipe=TemplateRecipe(
            stable_prompt=STABLE_PROMPT, catalog=plugin_set.catalog, tool_specs=plugin_set.tool_specs
        ),
    )
    conversation, _template = create_conversation(
        template,
        key="conv09-proof/run-1",
        system_message="This is a proof-of-concept conversation for sadana-harness's CONV-09 evidence.",
        iteration_budget=IterationBudget(max_total=5),
    )

    initial_hash = conversation.prompt_sha256
    print(f"initial prompt_sha256={initial_hash}")

    def build_dispatch(
        conversation: Conversation,
    ) -> tuple[plugin_dispatch.DispatchFn, plugin_dispatch.ChildSeqTracker]:
        """A fresh `build_dispatch()` call per turn, using that turn's own
        `conversation` — never one built once and reused, which would
        close over a stale `conversation`
        (docs/reference/dispatch_closure_state_bug.md), exactly the
        discipline `scripts/prove_plugin_dispatch_e2e.py` already
        follows."""
        return plugin_dispatch.build_dispatch(
            conversation,
            plugin_set,
            provider=PROVIDER,
            model=MODEL,
            now=0.0,
            approve=auto_approve,
        )

    print("\n=== turn 1: plain exchange, no plugin ===")
    dispatch, tracker = build_dispatch(conversation)
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
    result1, conversation = await plugin_dispatch.take_turn_and_reconcile(
        conversation,
        dispatch,
        tracker,
        user_input="Reply with a short one-sentence greeting and nothing else.",
        provider=PROVIDER,
        model=MODEL,
        now=0.0,
    )
    model_access.send = real_send
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

    print("\n=== turn 2: plugin-a, a real call node under real approval ===")
    dispatch, tracker = build_dispatch(conversation)
    capturing_dispatch_2, captured_2 = plugin_dispatch.capturing_dispatch(dispatch)
    result2, conversation = await plugin_dispatch.take_turn_and_reconcile(
        conversation,
        capturing_dispatch_2,
        tracker,
        user_input="Call the plugin_a_entry tool now.",
        provider=PROVIDER,
        model=MODEL,
        now=0.0,
    )
    print(f"exit_reason={result2.exit_reason} final_text={result2.final_text!r}")
    assert result2.exit_reason == ExitReason.COMPLETED, f"turn 2: expected COMPLETED, got {result2.exit_reason}"
    assert conversation.prompt_sha256 == initial_hash, "prompt_sha256 drifted after turn 2"
    assert captured_2, "turn 2: plugin_a_entry was never actually dispatched (capture list is empty)"
    dag2 = captured_2[-1]
    _print_dag_result("turn 2 dag_result", dag2)
    assert dag2.failed_node is None, f"turn 2: expected no failed_node, got {dag2.failed_node!r}"
    assert [t.node for t in dag2.trace][:2] == [
        "fetch_webhook",
        "interpret",
    ], f"turn 2: expected the walk to start fetch_webhook -> interpret, got {[t.node for t in dag2.trace]}"
    assert dag2.trace[0].ok is True, "turn 2: expected the call node to have succeeded"
    assert dag2.artifacts == (
        plugins.Artifact(kind="link", name="webhook", ref="https://example.com"),
    ), f"turn 2: expected the real webhook link artifact, got {dag2.artifacts}"
    assert approved_calls, "turn 2: the call node's approval question was never actually asked"
    print("[ok] turn 2 completed; prompt_sha256 unchanged; a real call node ran under real approval")

    print("\n=== turn 3: plugin-b ===")
    dispatch, tracker = build_dispatch(conversation)
    capturing_dispatch_3, captured_3 = plugin_dispatch.capturing_dispatch(dispatch)
    result3, conversation = await plugin_dispatch.take_turn_and_reconcile(
        conversation,
        capturing_dispatch_3,
        tracker,
        user_input="Call the plugin_b_entry tool now, with note='hello from turn 3'.",
        provider=PROVIDER,
        model=MODEL,
        now=0.0,
    )
    print(f"exit_reason={result3.exit_reason} final_text={result3.final_text!r}")
    assert result3.exit_reason == ExitReason.COMPLETED, f"turn 3: expected COMPLETED, got {result3.exit_reason}"
    assert conversation.prompt_sha256 == initial_hash, "prompt_sha256 drifted after turn 3"
    assert captured_3, "turn 3: plugin_b_entry was never actually dispatched (capture list is empty)"
    dag3 = captured_3[-1]
    _print_dag_result("turn 3 dag_result", dag3)
    assert dag3.failed_node is None, f"turn 3: expected no failed_node, got {dag3.failed_node!r}"
    assert [t.node for t in dag3.trace] == ["ask_helper"], f"turn 3: expected one ask node, got {dag3.trace}"
    print("[ok] turn 3 completed; prompt_sha256 unchanged")
    print(
        f"iteration_budget after turn 3: {conversation.iteration_budget.used}/{conversation.iteration_budget.max_total}"
    )

    print("\n=== turn 4: forced budget exhaustion ===")
    dispatch, tracker = build_dispatch(conversation)
    result4, conversation = await plugin_dispatch.take_turn_and_reconcile(
        conversation,
        dispatch,
        tracker,
        user_input="Reply with a short one-sentence greeting and nothing else.",
        provider=PROVIDER,
        model=MODEL,
        now=0.0,
    )
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
