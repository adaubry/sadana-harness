# Plan: The smallest complete plugin runs, visibly (from intent.md 2026-09-07)

## Files that change

`scripts/prove_plugin_dispatch_e2e.py` — the only file. Not new; this is
D3-graph-dispatch's own kept, re-runnable script (merged `6e3c3b3`),
extended in place per `spec.md`'s approved design. No file under `src/` or
`tests/` changes.

## Order of work

1. Edit `scripts/prove_plugin_dispatch_e2e.py`:
   - Add `from sadana import plugins` alongside the existing
     `from sadana import plugin_dispatch, plugin_manifest` import.
   - Add a module-level `_print_dag_result(label: str, result:
     plugins.DagResult) -> None` helper that prints `result.plugin`,
     `result.entry`, `result.failed_node`, then one line per `NodeTrace` in
     `result.trace` (`node`, `kind`, `visit`, `ok`, `port`, `detail`).
   - Add `_make_capturing_dispatch(dispatch: plugin_dispatch.DispatchFn) ->
     tuple[plugin_dispatch.DispatchFn, list[plugins.DagResult]]`: a factory
     that creates one fresh `captured` list and one wrapping closure over
     it, and returns both. Call it once per turn, after that turn's own
     `build_dispatch()` call, and pass the returned wrapper (not the raw
     `dispatch`) into that turn's `take_turn_and_reconcile()` call.
   - Add `_assert_single_ask_node(label: str, captured:
     list[plugins.DagResult]) -> plugins.DagResult`: asserts `captured` is
     non-empty, then asserts its last entry's `failed_node is None`,
     `len(trace) == 1`, `trace[0].kind == "ask"`, `trace[0].ok is True`, and
     returns that entry.
   - After each `take_turn_and_reconcile()` call: assert `exit_reason ==
     COMPLETED` (existing), then call `_assert_single_ask_node("turn N",
     <that turn's own captured list>)` and `_print_dag_result` on its
     return value.

   **Corrected during build, after this work item's own deploy-stage cold
   review** (see `review.md` § Findings): the original draft of this step
   used one `captured` list shared across both turns instead of one per
   turn. That is wrong, not just less tidy — `run_turn` reaches
   `ExitReason.COMPLETED` on two paths that never call `dispatch` at all
   (the model answering with no tool call; `dispatch` raising and being
   swallowed into a `tool_error:` string), so a turn that never actually
   dispatched would read the *previous* turn's already-captured
   `DagResult` off the end of a shared list instead of failing — silently
   inferring a turn's outcome from stale data, which is exactly what
   spec.md Requirement 7 rules out. The bullets above describe the fixed,
   per-turn-scoped shape that actually shipped, not the original draft.
2. Run the script for real, with a real `OPENROUTER_API_KEY` sourced from
   `.env` (no `.env` loader exists in this project; this is the
   established manual step every other real-model proof script uses).
   Confirm it ends `ALL ASSERTIONS PASSED`, and that both turns print
   `plugin='plugin-c' entry='plugin_c_entry' failed_node=None` plus one
   trace line naming the `interpret` node, `kind='ask'`, `ok=True`.
3. Self-check: `/ponytail-review` + `/simplify` against the diff (this
   project's build-stage self-check, per
   `docs/tasks/SDLC-second-opinion-timing/`). Apply anything worth taking
   now; note anything that would change behaviour as a future work item's
   problem instead.
4. `make verify`, confirm it ends `VERIFY OK`.
5. Deploy stage (separate skill, separate step) writes `review.md` with
   this run's output and `make verify`'s output as `## Evidence`.

## Risks

**What could this change break?** Nothing that already works. The two
existing `build_dispatch()` calls, the two existing
`take_turn_and_reconcile()` calls, and every assertion already in the
script (`ExitReason.COMPLETED`, `next_child_seq` advancing to 1 then 2,
`pending_tool_call_ids == frozenset()`) stay exactly where they are,
unmoved. No `src/` file changes, so no existing caller of
`plugin_dispatch.py` or `conversation.py` — nor any test — is affected by
this edit at all.

**Which step is most risky, and why.** Step 1's capture scoping —
in two ways, one caught before implementation and one caught after by
deploy-stage cold review (see the correction note above).
`plugin_dispatch.build_dispatch()` is called twice — once per turn — and
each call returns a distinct `dispatch` closure bound to that call's own
`ChildSeqTracker`. If the wrapping closure were written once, referencing
`dispatch` by name from an enclosing scope rather than being rebuilt after
each `build_dispatch()` call, Python's late-binding closures would make
turn 2's capture silently call turn 1's `dispatch` — the same "a captured
reference outlives the point it should have been rebuilt" failure
`docs/reference/dispatch_closure_state_bug.md` already documents, in a new
file. The mitigation is structural: `_make_capturing_dispatch` is called
fresh, immediately after each `build_dispatch()` call, and takes `dispatch`
as a parameter rather than a closed-over name. The second, subtler version
of the same shape: even with a correctly-rebuilt *dispatch* wrapper each
turn, a *shared* `captured` list across both turns has an equivalent
staleness risk on the read side — see the correction note above. Running
turn 1 to completion (its own assertions passing on its own, non-shared
capture list) before turn 2 begins is what proves the wrapping introduced
no regression before the reconciliation-across-calls path — the one this
bug class actually threatens — is exercised a second time.

**Rejected-alternatives drift check.** Re-read against `spec.md`'s three
rejected alternatives before writing this plan: a new eval Task under
`scripts/eval/tasks/` (not done — no `Task`, no `run_task`, no
`save_result` anywhere in this plan), a new standalone script (not done —
one file, the existing one, edited), and hermes's `agent/moa_trace.py`
persisted-trace shape (not done — no config key, no disk write, no
`try`/`except`-swallowed background persistence; the capture list is a
local variable inside one script's `main()`, gone when the process exits).
None of the three are drifted back into.

## Proof

- The real run's full stdout (a real `OPENROUTER_API_KEY`, live OpenRouter
  call), ending `ALL ASSERTIONS PASSED`, with both turns showing
  `plugin='plugin-c' entry='plugin_c_entry' failed_node=None` and a
  `NodeTrace` line for `node='interpret' kind='ask' ... ok=True`.
- `make verify` output ending `VERIFY OK`.
- Both pasted into `review.md`'s `## Evidence` at the Deploy stage.
