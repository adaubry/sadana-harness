#!/usr/bin/env python3
"""Standalone proof that C11-context-completion's real behavior works
against a real model: real compaction (a genuine second model call
summarizing dropped history, then a genuine retry), a second compaction
refining the first summary instead of resummarizing it, and a real
oversized tool result spilling to a real file.

Not a pytest test — testing-conventions bars the network and the model API
from the unit suite. Run manually with a real OPENROUTER_API_KEY, paste its
output into review.md's ## Evidence. See
docs/tasks/C11-context-completion/spec.md for the full contract.

Hybrid proof, and this is deliberate, not a shortcut taken quietly: six
real, paid API calls across two OpenRouter models (documented in this
work item's own build-session record) showed OpenRouter silently
truncating an oversized request before it reaches billing or inference,
regardless of model, provider, or the `transforms` request flag — so a
genuine provider-side "too large" rejection is not affordably
reproducible today. The *trigger* (model_access.send returning
NeedsContextCompression) is simulated for exactly the calls named below;
every call after each trigger — the real summarization call, the real
retried completion, the real result-spill — is genuine, unmocked, against
a live model.
"""

from __future__ import annotations

import asyncio
import os
import sys
from collections.abc import Callable
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from sadana import model_access, plugins  # noqa: E402
from sadana.conversation import (  # noqa: E402
    ConversationTemplate,
    ExitReason,
    IterationBudget,
    TemplateRecipe,
    ToolSpec,
    create_conversation,
    take_turn,
)

MODEL = "deepseek/deepseek-v4-flash-0731"
PROVIDER = "openrouter"
STABLE_PROMPT = (
    "You are a plainly-behaved assistant used only by sadana-harness's own "
    "C11 proof script. Follow instructions exactly and literally."
)


def _install_forced_overflow(real_send: Callable, force_call_numbers: set[int]) -> tuple[Callable, list]:
    """Wraps `real_send`: for each 1-indexed call number in
    `force_call_numbers`, returns a simulated NeedsContextCompression
    instead of calling through. Every other call — including every real
    call this simulated one triggers CONTEXT to make — reaches the real
    provider unchanged."""
    calls: list[model_access.Request] = []

    def wrapped(request: model_access.Request) -> model_access.Outcome:
        calls.append(request)
        if len(calls) in force_call_numbers:
            return model_access.NeedsContextCompression(
                "simulated for this proof — see scripts/prove_context_completion.py's own module docstring"
            )
        return real_send(request)

    return wrapped, calls


async def _no_tools_dispatch(name: str, arguments: dict) -> plugins.DagResult:
    return plugins.DagResult(
        plugin="none",
        entry=name,
        text=f"tool_error: no tools available (unexpected call to {name!r})",
        artifacts=(),
        trace=(),
        failed_node="entry",
    )


async def _big_result_dispatch(name: str, arguments: dict) -> plugins.DagResult:
    text = "REAL DATA " * 20_000  # ~200,000 chars — well over any real spill threshold
    return plugins.DagResult(plugin="test", entry=name, text=text, artifacts=(), trace=(), failed_node=None)


async def main() -> None:
    real_send = model_access.send
    os.environ["SADANA_CONTEXT_COMPACTION_TAIL_MESSAGES"] = "0"  # every trigger summarizes the whole history

    template = ConversationTemplate(
        name="c11-proof", recipe=TemplateRecipe(stable_prompt=STABLE_PROMPT, catalog=(), tool_specs=())
    )
    conversation, _t = create_conversation(
        template, key="c11-proof/compaction", system_message="", iteration_budget=IterationBudget(max_total=5)
    )

    print("=== phase 1: a simulated overflow triggers real compaction ===")
    model_access.send, calls1 = _install_forced_overflow(real_send, {1})
    result1, conversation = await take_turn(
        conversation,
        user_input="Reply with a short one-sentence greeting and nothing else.",
        provider=PROVIDER,
        model=MODEL,
        dispatch=_no_tools_dispatch,
        now=0.0,
    )
    print(f"exit_reason={result1.exit_reason} final_text={result1.final_text!r}")
    assert result1.exit_reason == ExitReason.COMPLETED, f"expected COMPLETED, got {result1.exit_reason}"
    summaries = [m for m in conversation.messages if m.is_summary]
    assert len(summaries) == 1, f"expected exactly one summary message, got {len(summaries)}"
    print("[ok] real compaction recovered the turn via a real second model call")
    print(f"    summary (fresh framing): {summaries[0].content[:200]!r}")
    first_summary_text = summaries[0].content
    assert len(calls1) == 3, f"expected 3 real send() calls (forced + summarize + retry), got {len(calls1)}"

    print("\n=== phase 2: a second simulated overflow refines the existing summary ===")
    model_access.send, calls2 = _install_forced_overflow(real_send, {1})
    result2, conversation = await take_turn(
        conversation,
        user_input="Reply with a different short sentence.",
        provider=PROVIDER,
        model=MODEL,
        dispatch=_no_tools_dispatch,
        now=0.0,
    )
    print(f"exit_reason={result2.exit_reason} final_text={result2.final_text!r}")
    assert result2.exit_reason == ExitReason.COMPLETED, f"expected COMPLETED, got {result2.exit_reason}"
    summaries2 = [m for m in conversation.messages if m.is_summary]
    assert len(summaries2) == 1, f"expected exactly one summary message after refining, got {len(summaries2)}"
    refine_request = calls2[1]  # calls2[0] is the forced/simulated attempt; this is the real summarization call
    refine_prompt = refine_request.messages[0]["content"]
    assert "own summary" in refine_prompt, "expected the refine framing to name the existing summary explicitly"
    assert first_summary_text[:50] in refine_prompt, "expected the first summary's own text inside the refine prompt"
    print("[ok] the real second summarization call was asked to refine, not resummarize from scratch")
    print(f"    summary (refined): {summaries2[0].content[:200]!r}")

    model_access.send = real_send  # done simulating anything — the rest of this script is fully real

    print("\n=== phase 3: a real oversized tool result spills to a real file ===")
    os.environ["SADANA_CONTEXT_RESULT_SPILL_CHARS"] = "1000"
    spill_template = ConversationTemplate(
        name="c11-proof-spill",
        recipe=TemplateRecipe(
            stable_prompt=STABLE_PROMPT,
            catalog=(),
            tool_specs=(
                ToolSpec(
                    key="fetch_big_data",
                    name="fetch_big_data",
                    parameters={"type": "object", "properties": {}},
                    describe=lambda _resolved: "Fetches a large dataset. Call this when asked to fetch data.",
                ),
            ),
        ),
    )
    spill_conversation, _t = create_conversation(
        spill_template, key="c11-proof/spill", system_message="", iteration_budget=IterationBudget(max_total=3)
    )
    result3, spill_conversation = await take_turn(
        spill_conversation,
        user_input="Call fetch_big_data, then just say 'done' once you see the result.",
        provider=PROVIDER,
        model=MODEL,
        dispatch=_big_result_dispatch,
        now=0.0,
    )
    print(f"exit_reason={result3.exit_reason} final_text={result3.final_text!r}")
    assert result3.exit_reason == ExitReason.COMPLETED, f"expected COMPLETED, got {result3.exit_reason}"
    tool_messages = [m for m in spill_conversation.messages if m.role == "tool"]
    assert tool_messages, "expected at least one tool result message"
    reference = tool_messages[0].content or ""
    assert "full result saved to" in reference, f"expected a real spill reference, got: {reference[:200]!r}"
    spill_path = reference.split("full result saved to ", 1)[1].split(" —", 1)[0]
    assert Path(spill_path).is_file(), f"expected a real file at {spill_path}"
    assert len(Path(spill_path).read_text(encoding="utf-8")) == len("REAL DATA " * 20_000)
    print(f"[ok] real oversized result spilled to a real file: {spill_path}")

    print("\nALL ASSERTIONS PASSED")


if __name__ == "__main__":
    if not os.environ.get("OPENROUTER_API_KEY"):
        print("OPENROUTER_API_KEY is not set in the real environment. Set it and re-run.", file=sys.stderr)
        raise SystemExit(1)
    asyncio.run(main())
