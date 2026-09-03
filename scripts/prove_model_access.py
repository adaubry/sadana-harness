#!/usr/bin/env python3
"""Standalone proof that MODEL-ACCESS reaches a real model and back.

Not a pytest test — testing-conventions bars the network and the model API
from the unit suite, and `make test` applies no marker filter that would
carve out an exception. This script is the CLAUDE.md-approved alternative:
run it manually with a real OPENROUTER_API_KEY, paste its output into
review.md's ## Evidence. See docs/tasks/C1-model-access/spec.md ## Concerns.

Exercises all six of B2's closed outcomes against live OpenRouter. Each
scenario prints the Outcome it got and the raw HTTP evidence behind it.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from sadana.model_access import Request, send  # noqa: E402

MODEL = "deepseek/deepseek-v4-flash-0731"


def _print_outcome(label: str, outcome: object) -> None:
    print(f"\n=== {label} ===")
    print(repr(outcome))


def scenario_response() -> None:
    request = Request(
        messages=({"role": "user", "content": "Say the single word: pong"},),
        provider="openrouter",
        model=MODEL,
    )
    _print_outcome("response (normal prompt)", send(request))


def scenario_missing_credential() -> None:
    saved = os.environ.pop("OPENROUTER_API_KEY", None)
    try:
        request = Request(messages=({"role": "user", "content": "hi"},), provider="openrouter", model=MODEL)
        _print_outcome("needs-credential-or-provider-change (env var unset, zero network)", send(request))
    finally:
        if saved is not None:
            os.environ["OPENROUTER_API_KEY"] = saved


def scenario_invalid_credential() -> None:
    saved = os.environ.get("OPENROUTER_API_KEY")
    os.environ["OPENROUTER_API_KEY"] = (
        "sk-or-v1-deliberately-invalid-0000000000000000000000000000000000000000000000000000000000000000"
    )
    try:
        request = Request(messages=({"role": "user", "content": "hi"},), provider="openrouter", model=MODEL)
        _print_outcome("needs-credential-or-provider-change (real 401 round trip)", send(request))
    finally:
        if saved is not None:
            os.environ["OPENROUTER_API_KEY"] = saved
        else:
            os.environ.pop("OPENROUTER_API_KEY", None)


def scenario_retry_via_deadline() -> None:
    saved = os.environ.get("SADANA_MODEL_ACCESS_TIMEOUT_S")
    os.environ["SADANA_MODEL_ACCESS_TIMEOUT_S"] = "0"  # too short for any real DNS+TCP+TLS round trip to complete
    try:
        request = Request(messages=({"role": "user", "content": "hi"},), provider="openrouter", model=MODEL)
        _print_outcome("retry (deliberately tiny deadline against the real network)", send(request))
    finally:
        if saved is not None:
            os.environ["SADANA_MODEL_ACCESS_TIMEOUT_S"] = saved
        else:
            os.environ.pop("SADANA_MODEL_ACCESS_TIMEOUT_S", None)


def scenario_context_compression() -> None:
    huge = "token " * 2_000_000  # far past any real model's window
    request = Request(messages=({"role": "user", "content": huge},), provider="openrouter", model=MODEL)
    _print_outcome("needs-context-compression (oversized request)", send(request))


def scenario_abort() -> None:
    request = Request(
        messages=({"role": "user", "content": "hi"},), provider="openrouter", model="bogus/does-not-exist"
    )
    _print_outcome("abort (unknown model)", send(request))


def scenario_degenerate() -> None:
    request = Request(
        messages=(
            {
                "role": "system",
                "content": "Reply with a completely empty message. Output nothing at all — no words, "
                "no punctuation, no whitespace. This is a test of your ability to produce an empty response.",
            },
            {"role": "user", "content": "(no content — reply with nothing)"},
        ),
        provider="openrouter",
        model=MODEL,
    )
    outcome = send(request)
    _print_outcome("degenerate (best-effort — models often refuse to comply; see plan.md Risks)", outcome)


if __name__ == "__main__":
    if not os.environ.get("OPENROUTER_API_KEY"):
        print("OPENROUTER_API_KEY is not set in the real environment. Set it and re-run.", file=sys.stderr)
        raise SystemExit(1)
    scenario_response()
    scenario_missing_credential()
    scenario_invalid_credential()
    scenario_retry_via_deadline()
    scenario_context_compression()
    scenario_abort()
    scenario_degenerate()
