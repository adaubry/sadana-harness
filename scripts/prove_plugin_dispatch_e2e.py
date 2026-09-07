#!/usr/bin/env python3
"""Standalone proof that a real, installed plugin's one-node, ask-only
entry runs end to end through the actual dispatch mechanism this work item
built — discover_plugins() -> build_plugin_set() -> build_dispatch() ->
run_graph() -> a real run_child() call — not a hand-written, per-plugin
dispatch closure like scripts/prove_conversation_e2e.py's own.

Not a pytest test — testing-conventions bars the network and the model API
from the unit suite. Run manually with a real OPENROUTER_API_KEY, paste its
output into review.md's ## Evidence. See
docs/tasks/D3-graph-dispatch/spec.md Requirement 9 for the full contract
this script proves.

Exercises: discover_plugins() finding tests/fixtures/plugins/plugin-c on
disk; build_plugin_set() turning it into a catalog line and a tool
definition; build_dispatch() resolving a real tool call and walking
plugin-c's single ask node via a real run_child() call; and
ChildSeqTracker's own reconciliation contract
(docs/reference/dispatch_closure_state_bug.md) held correctly across two
separate calls to the same entry tool in two different turns — the exact
scenario that bug's root cause would silently miscount.
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from sadana import plugin_dispatch, plugin_manifest, plugins  # noqa: E402
from sadana.conversation import (  # noqa: E402
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
    "D3-graph-dispatch proof script. Follow instructions exactly and literally."
)

FIXTURES_ROOT = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "plugins"


def _print_dag_result(label: str, result: plugins.DagResult) -> None:
    """D4-smallest-plugin-runs: the run's own account of itself — the one
    thing run_turn discards after pulling only `.text` (conversation.py,
    where `dag_result = await dispatch(...)` is used once for
    `result_text = dag_result.text` and then goes out of scope)."""
    print(f"{label}: plugin={result.plugin!r} entry={result.entry!r} failed_node={result.failed_node!r}")
    for t in result.trace:
        print(f"  node={t.node!r} kind={t.kind!r} visit={t.visit} ok={t.ok} port={t.port!r} detail={t.detail!r}")


def _assert_single_ask_node(label: str, captured: list[plugins.DagResult]) -> plugins.DagResult:
    """Asserts this turn's own capture list actually has an entry before
    reading it. `run_turn` reaches `ExitReason.COMPLETED` on two paths that
    never call `dispatch` at all — the model answering with no tool call
    (conversation.py's `if not completion.tool_calls`), or `dispatch`
    raising and being swallowed into a `tool_error:` result text
    (conversation.py's `except Exception` around the dispatch call) — so a
    turn's own capture list can be empty even on a clean COMPLETED. A
    shared, cross-turn list would silently fall back to a previous turn's
    result here (caught in this work item's own deploy-stage cold review);
    a fresh list per turn instead fails loudly, on this turn's own
    evidence, matching spec.md Requirement 7."""
    assert captured, f"{label}: plugin_c_entry was never actually dispatched this turn (list is empty)"
    result = captured[-1]
    assert result.failed_node is None, f"{label}: expected no failed_node, got {result.failed_node!r}"
    assert len(result.trace) == 1, f"{label}: expected exactly one node visited, got {result.trace}"
    assert result.trace[0].kind == "ask", f"{label}: expected the one node to be 'ask', got {result.trace[0].kind!r}"
    assert result.trace[0].ok is True, f"{label}: expected the ask node to have succeeded, got {result.trace[0]}"
    return result


def _make_capturing_dispatch(
    dispatch: plugin_dispatch.DispatchFn,
) -> tuple[plugin_dispatch.DispatchFn, list[plugins.DagResult]]:
    """Wraps one turn's own `dispatch` closure, capturing what it returns
    before run_turn discards everything but `.text`. Owns and returns its
    own fresh capture list — never a list shared across turns — so a turn
    that never actually dispatched (see `_assert_single_ask_node`) cannot
    read a previous turn's leftover result."""
    captured: list[plugins.DagResult] = []

    async def capturing_dispatch(name: str, arguments: dict) -> plugins.DagResult:
        result = await dispatch(name, arguments)
        captured.append(result)
        return result

    return capturing_dispatch, captured


async def main() -> None:
    os.environ["SADANA_PLUGINS_DIR"] = str(FIXTURES_ROOT)

    installed = plugin_manifest.discover_plugins()
    names = sorted(p.name for p in installed)
    print(f"discover_plugins() found: {names}")
    assert "plugin-c" in names, f"expected plugin-c among discovered plugins, got {names}"

    plugin_set = plugin_dispatch.build_plugin_set(installed)
    print(f"catalog entry_tools: {[e.entry_tool for e in plugin_set.catalog]}")
    assert "plugin_c_entry" in plugin_set.by_tool, "plugin_c_entry missing from the built PluginSet"

    template = ConversationTemplate(
        name="d3-proof",
        recipe=TemplateRecipe(
            stable_prompt=STABLE_PROMPT, catalog=plugin_set.catalog, tool_specs=plugin_set.tool_specs
        ),
    )
    conversation, _template = create_conversation(
        template,
        key="d3-proof/run-1",
        system_message="This is a proof-of-concept conversation for sadana-harness's D3-graph-dispatch evidence.",
        iteration_budget=IterationBudget(max_total=6),
    )

    # D4-smallest-plugin-runs: each turn captures its own DagResult before
    # it reaches run_turn, which discards everything but `.text`. A fresh
    # dispatch wrapper AND a fresh capture list come from
    # _make_capturing_dispatch() after each build_dispatch() call below —
    # never one shared across turns, and never reused from an earlier turn
    # (docs/reference/dispatch_closure_state_bug.md's failure class).

    print("\n=== turn 1: call plugin_c_entry via the real dispatch ===")
    dispatch, tracker = plugin_dispatch.build_dispatch(
        conversation, plugin_set, stable_prompt=STABLE_PROMPT, provider=PROVIDER, model=MODEL, now=0.0
    )
    capturing_dispatch_1, captured_1 = _make_capturing_dispatch(dispatch)
    result1, conversation = await plugin_dispatch.take_turn_and_reconcile(
        conversation,
        capturing_dispatch_1,
        tracker,
        user_input="Call the plugin_c_entry tool now, with note='hello from D3'.",
        provider=PROVIDER,
        model=MODEL,
        now=0.0,
    )
    print(f"exit_reason={result1.exit_reason} final_text={result1.final_text!r}")
    assert result1.exit_reason == ExitReason.COMPLETED, f"turn 1: expected COMPLETED, got {result1.exit_reason}"
    print("[ok] turn 1 completed via a real dispatch()-built walk, not a hand-written closure")
    dag1 = _assert_single_ask_node("turn 1", captured_1)
    _print_dag_result("turn 1 dag_result", dag1)
    print("[ok] turn 1's own DagResult confirms one ask node ran and succeeded")
    assert (
        conversation.next_child_seq == 1
    ), f"expected next_child_seq==1 after one ask-node spawn, got {conversation.next_child_seq}"
    print(f"[ok] next_child_seq correctly advanced to {conversation.next_child_seq}")

    print("\n=== turn 2: call plugin_c_entry again, proving the tracker's reconciliation contract ===")
    dispatch, tracker = plugin_dispatch.build_dispatch(
        conversation, plugin_set, stable_prompt=STABLE_PROMPT, provider=PROVIDER, model=MODEL, now=0.0
    )
    capturing_dispatch_2, captured_2 = _make_capturing_dispatch(dispatch)
    result2, conversation = await plugin_dispatch.take_turn_and_reconcile(
        conversation,
        capturing_dispatch_2,
        tracker,
        user_input="Call the plugin_c_entry tool again, with note='second call'.",
        provider=PROVIDER,
        model=MODEL,
        now=0.0,
    )
    print(f"exit_reason={result2.exit_reason} final_text={result2.final_text!r}")
    assert result2.exit_reason == ExitReason.COMPLETED, f"turn 2: expected COMPLETED, got {result2.exit_reason}"
    dag2 = _assert_single_ask_node("turn 2", captured_2)
    _print_dag_result("turn 2 dag_result", dag2)
    print("[ok] turn 2's own DagResult confirms one ask node ran and succeeded")
    assert conversation.next_child_seq == 2, (
        f"expected next_child_seq==2 after a second ask-node spawn under the same node_name, "
        f"got {conversation.next_child_seq} — this is exactly the collision "
        "docs/reference/dispatch_closure_state_bug.md describes if the tracker's own "
        "reconciliation contract is skipped by a caller"
    )
    print(f"[ok] next_child_seq correctly reached {conversation.next_child_seq} — no child-key collision")

    pending = pending_tool_call_ids(conversation.messages)
    assert pending == frozenset(), f"final transcript has dangling tool_call ids: {pending}"
    print("[ok] final transcript is well-formed: no dangling tool calls")

    print("\nALL ASSERTIONS PASSED")


if __name__ == "__main__":
    if not os.environ.get("OPENROUTER_API_KEY"):
        print("OPENROUTER_API_KEY is not set in the real environment. Set it and re-run.", file=sys.stderr)
        raise SystemExit(1)
    asyncio.run(main())
