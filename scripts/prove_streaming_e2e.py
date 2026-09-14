#!/usr/bin/env python3
"""Standalone proof that H21's own three promises hold against a real
OpenRouter round trip: a turn's text really streams as it is produced, a
running turn really honors a stop request, and a real plugin dispatch
really leaves live `spans` rows behind.

Not a pytest test — testing-conventions bars the network and the model API
from the unit suite. Run manually with a real `OPENROUTER_API_KEY`, paste
its output into review.md's ## Evidence.
docs/tasks/H21-watched-streaming-live-runs-stop/spec.md is the contract.

Exercises: `run_turn`'s own `text_delta` callback firing with a strictly
increasing per-turn `seq`, streamed from a real completion (Requirement 1);
`should_stop()` returning `True` at its second call — the safe point right
after a tool round's own dispatch finishes, before a second model call
would start — ending a turn with `ExitReason.INTERRUPTED, detail="stopped"`
(Requirement 4); a real `plugin_manifest.run_graph` walk, driven through a
real `plugin_dispatch.build_dispatch`, leaving one real `spans` row per
node it visited (`observability.py`'s own `record_node`).

A standalone `TurnObserver`/`should_stop` pair, not the door's own
`_DoorTurnObserver` — this script drives `client_surface.take_turn`
directly, the same one door away from a real HTTP call, to keep the proof
focused on `run_turn`'s own contract rather than the door's request/
response plumbing (`tests/contract/nouns/test_messages.py`'s own
`test_create_succeeds_and_returns_the_user_message` already proves the
door's own observer wiring, with a fake provider).
"""

from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

MODEL = "deepseek/deepseek-v4-flash-0731"
PROVIDER = "openrouter"
FIXTURES_ROOT = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "plugins"


class _RecordingObserver:
    """A minimal, real `conversation.TurnObserver` — every delta printed as
    it arrives. `should_stop()` is polled only at `run_turn`'s own two
    named safe points (before a model call, after a tool round) — never
    mid-stream — so "stop after the first round" is `stop_from_round=2`:
    `False` on the first check (round 1, whatever it streams, runs to
    completion), `True` from the second check onward (before round 2's own
    model call would start)."""

    def __init__(self, *, stop_from_round: int | None = None) -> None:
        self.deltas: list[tuple[int, str]] = []
        self.result = None
        self._stop_from_round = stop_from_round
        self._should_stop_calls = 0

    def turn_started(self, turn_key, user_message_seq: int) -> None:  # noqa: ANN001
        print(f"    turn_started turn_key={turn_key!r} user_message_seq={user_message_seq}")

    def text_delta(self, seq: int, text: str) -> None:
        self.deltas.append((seq, text))
        print(f"    delta seq={seq} text={text!r}")

    def turn_finished(self, result) -> None:  # noqa: ANN001
        self.result = result
        print(f"    turn_finished exit_reason={result.exit_reason} final_text={result.final_text!r}")

    def should_stop(self) -> bool:
        self._should_stop_calls += 1
        return self._stop_from_round is not None and self._should_stop_calls >= self._stop_from_round


async def main() -> None:
    from sadana import client_surface, memory, plugin_manifest

    with tempfile.TemporaryDirectory() as state_dir:
        os.environ["SADANA_STATE_DIR"] = state_dir
        os.environ["SADANA_PLUGINS_DIR"] = str(FIXTURES_ROOT)

        runtime = client_surface.open_runtime(provider=PROVIDER, model=MODEL)
        account = memory.owner_account()

        print("\n=== turn 1: a real streamed reply ===")
        conv1 = "h21-proof-stream-1"
        client_surface.open_conversation(runtime, account=account, conversation=conv1, template_name="h21-proof")
        observer1 = _RecordingObserver()
        outcome1 = await client_surface.take_turn(
            runtime,
            account=account,
            conversation=conv1,
            text="Reply with a short one-sentence greeting and nothing else.",
            create_as=None,
            observer=observer1,
        )
        print(f"outcome1.ok={outcome1.ok} answer={outcome1.answer!r}")
        assert outcome1.ok, f"turn 1: expected a completed turn, got diagnostic={outcome1.diagnostic!r}"
        assert observer1.deltas, "turn 1: text_delta was never called — the request did not actually stream"
        seqs = [seq for seq, _text in observer1.deltas]
        assert seqs == sorted(seqs) and len(set(seqs)) == len(seqs), f"turn 1: deltas not strictly increasing: {seqs}"
        streamed_text = "".join(text for _seq, text in observer1.deltas)
        assert (
            streamed_text == outcome1.answer
        ), f"turn 1: streamed text {streamed_text!r} does not match the turn's own final answer {outcome1.answer!r}"
        print(f"[ok] turn 1 streamed {len(observer1.deltas)} deltas, concatenating to the turn's own final answer")

        installed = plugin_manifest.discover_plugins()
        names = sorted(p.name for p in installed)
        print(f"discover_plugins() found: {names}")
        assert "plugin-b" in names, f"expected plugin-b among {names}"

        print("\n=== turn 2: stopped after its own first round ===")
        conv2 = "h21-proof-stream-2"
        client_surface.open_conversation(runtime, account=account, conversation=conv2, template_name="h21-proof")
        # `stop_from_round=2`: round 1 (a real plugin_b tool call, dispatched
        # for real) runs to completion; `should_stop()` then answers `True`
        # for the first time right where `run_turn` checks it — after that
        # tool round, before round 2's own model call would begin.
        observer2 = _RecordingObserver(stop_from_round=2)
        outcome2 = await client_surface.take_turn(
            runtime,
            account=account,
            conversation=conv2,
            text="Call the plugin_b_entry tool now, with note='hello from round 1', then say something else after.",
            create_as=None,
            observer=observer2,
        )
        print(f"outcome2.ok={outcome2.ok} diagnostic={outcome2.diagnostic!r}")
        assert not outcome2.ok, "turn 2: expected a stopped (not ok) turn"
        assert observer2.result is not None
        assert (
            observer2.result.detail == "stopped"
        ), f"turn 2: expected detail='stopped', got {observer2.result.detail!r}"
        print("[ok] turn 2 honored should_stop() after its own first round: detail=stopped")

        print("\n=== turn 3: a real plugin dispatch, real spans rows ===")
        conv3 = "h21-proof-stream-3"
        client_surface.open_conversation(runtime, account=account, conversation=conv3, template_name="h21-proof")
        observer3 = _RecordingObserver()
        outcome3 = await client_surface.take_turn(
            runtime,
            account=account,
            conversation=conv3,
            text="Call the plugin_b_entry tool now, with note='hello from the H21 proof script'.",
            create_as=None,
            observer=observer3,
        )
        print(f"outcome3.ok={outcome3.ok} answer={outcome3.answer!r}")
        assert outcome3.ok, f"turn 3: expected a completed turn, got diagnostic={outcome3.diagnostic!r}"

        reader = runtime.connections.reader()
        rows = reader.execute(
            "SELECT node_seq, node, kind, status, started_at, ended_at, input_preview, output_preview, error "
            "FROM spans WHERE conversation_key = ? ORDER BY node_seq",
            (conv3,),
        ).fetchall()
        assert rows, "turn 3: no spans rows were written for the real plugin dispatch"
        print(f"spans rows for turn 3's own plugin_b dispatch ({len(rows)} total):")
        for row in rows:
            print(
                f"    node_seq={row['node_seq']} node={row['node']!r} kind={row['kind']!r} "
                f"status={row['status']!r} started_at={row['started_at']} ended_at={row['ended_at']} "
                f"input_preview={row['input_preview']!r} output_preview={row['output_preview']!r} "
                f"error={row['error']!r}"
            )
            assert row["started_at"] is not None and row["ended_at"] is not None
            assert row["started_at"] <= row["ended_at"]
        print(f"[ok] turn 3's real plugin dispatch left {len(rows)} real spans rows behind")

        print("\nALL ASSERTIONS PASSED")


if __name__ == "__main__":
    if not os.environ.get("OPENROUTER_API_KEY"):
        print("OPENROUTER_API_KEY is not set in the real environment. Set it and re-run.", file=sys.stderr)
        raise SystemExit(1)
    asyncio.run(main())
