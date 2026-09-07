# Plan: DagResult and the dispatch signature (from intent.md 2026-09-07)

## Files that change

**New:**
- `src/sadana/plugins.py` — `Artifact`, `NodeTrace`, `DagResult`, three
  frozen dataclasses, per spec.md's Design section.
- `tests/unit/test_plugins.py` — construction/frozen-ness tests for the
  three dataclasses.

**The one behavioural change, plus three signature-only changes:**
- `src/sadana/conversation.py` — `run_turn`/`take_turn`/`run_child`'s
  `dispatch` parameter type; `run_turn`'s tool-call loop
  (`result_text = str(raw_result)` → `result_text = result.text`); new
  `from sadana import plugins` import. `take_turn`/`run_child` change only
  their type annotation — neither inspects a dispatch result itself.

**Real dispatch implementations that construct the return value directly**
(each gets a small local helper to avoid repeating six keyword arguments
per branch — see Order of work step 5):
- `scripts/prove_conversation_e2e.py` — the `dispatch` closure, three
  branches (`plugin_a_entry`, `plugin_b_entry`, the unreachable
  unknown-tool fallback). `trace` populated from the closure's own
  existing "node 1/2/3" comments (spec.md's Design section).
- `scripts/prove_context_completion.py` — `_no_tools_dispatch` (never
  actually invoked — a model with no tools has nothing to call) and
  `_big_result_dispatch` (returns ~200,000 chars of `text`, unchanged
  content, to keep exercising C11's own spill-threshold proof).
- `scripts/eval/tasks/plugin_dispatch.py` — `make_dispatch`'s inner
  `dispatch` closure (EVAL-02's own proof; unchanged grading logic,
  since `grade()` reads message content, which stays a string either
  way).
- `src/sadana/eval_harness.py` — `_no_tools_dispatch`,
  `_no_tools_dispatch_factory`'s return type, and `run_task`'s own
  `dispatch_factory` parameter type
  (`Callable[[Conversation], Callable[[str, dict], Awaitable[DagResult]]]`).

**Tests with dispatch-shaped stubs, type-only except where noted:**
- `tests/unit/test_conversation.py` — the largest single file: roughly
  ten stub functions (`_fake_dispatch_ok`, `slow_dispatch`,
  `dispatch_tracker`, `raising_dispatch`, three `big_dispatch`s,
  `medium_dispatch`, `recording_dispatch`, `fail_dispatch`). Every one
  that currently `return`s a string gets a `plugins.DagResult` return
  instead (via a local `_ok(text)` helper — see step 5); `raising_dispatch`
  only needs its type annotation updated, since it always raises before
  returning and that path is unchanged. One new test asserts `run_turn`
  renders `DagResult.text`, not `str(DagResult(...))`.
- `tests/unit/test_conversation_store.py` — `dispatch_tracker` (returns
  `"ok"`), `dispatch_ok` (returns `"ran"`) — same `_ok(text)` treatment.
- `tests/unit/test_eval_harness.py` — the inline `dispatch_factory` /
  `dispatch` closure (returns `"TOOL_RAN_OK"`) and its own
  `Callable[[str, dict], Awaitable[str]]` type annotation.

**Documentation:**
- `docs/reference/dispatch_closure_state_bug.md` — append a
  `## Resolution (D1-dagresult-dispatch)` section, matching the existing
  `## Resolution (CONV-10-dispatch-parent-propagation)` section's style.
- `CLAUDE.md` — two new bullets under "Please do", naming the interface
  this item establishes as project-wide policy: a plugin's outcome returns
  as `DagResult`, never a side channel; a classified outcome with
  meaningfully different data per branch is a named, closed set of types
  (unlike `DagResult` itself, which isn't one). Missed in this section on
  first pass — added here during review, per review.md's Important finding,
  once the actual diff made clear the rule change was real and load-bearing,
  not incidental.

**Explicitly not touched:** any file under
`docs/tasks/CONV-10-dispatch-parent-propagation/` or
`docs/tasks/EVAL-02-one-real-turn/` (closed work items' own artifact
chains — only their *source* files, `plugin_dispatch.py` above, are in
scope, which is not the same thing).

## Order of work

1. **`src/sadana/plugins.py`** — the three dataclasses, module docstring
   pointing at this spec. No dependency on `conversation.py`, so it can
   land and typecheck in isolation.
2. **`tests/unit/test_plugins.py`** — construct each dataclass, assert
   `frozen=True` isn't decorative (mutating raises
   `dataclasses.FrozenInstanceError`).
3. **`conversation.py`'s three signatures** — `Callable[[str, dict],
   Awaitable[plugins.DagResult]]` on `run_turn`/`take_turn`/`run_child`;
   add `plugins` to the existing `from sadana import ...` line.
4. **`run_turn`'s tool-call loop** — the one behavioural change:
   `result: plugins.DagResult = await dispatch(...)` /
   `result_text = result.text`. Exception handling (`except Exception as
   e: result_text = f"tool_error: {e}"`) is unchanged.
5. **Every test stub in `test_conversation.py`, `test_conversation_store.py`,
   `test_eval_harness.py`** — a small local `_ok(text: str) ->
   plugins.DagResult` helper per file (six keyword arguments repeated
   ~13 times across three files is the actual case for one two-line
   helper, not a shared cross-file utility — each file already defines
   its own small fixtures independently, matching this codebase's
   existing per-file-helper pattern rather than inventing a shared test
   module nothing else needs). Update each stub's `return "..."` to
   `return _ok("...")`; update `raising_dispatch`'s type annotation only.
   Add the one new `test_conversation.py` assertion (DagResult.text
   renders, not `str(DagResult(...))`). Run `make test` after this step —
   narrowest point at which the whole non-network suite can go green.
6. **`eval_harness.py`** — `_no_tools_dispatch` returns a `DagResult`
   (via its own tiny local literal — it's the one implementation, no
   helper needed); `dispatch_factory`'s declared type updates. Re-run
   `test_eval_harness.py` (already updated in step 5) to confirm.
7. **The three hand-run proof/eval scripts**
   (`prove_conversation_e2e.py`, `prove_context_completion.py`,
   `plugin_dispatch.py`) — each gets its own local `_ok(text, ...)` or
   direct `DagResult(...)` construction per spec.md's Design section.
   These are network/model scripts, not run as part of this plan's own
   verification (testing-conventions' network ban; this project's
   standing exception for exactly these scripts) — proof is a manual run
   of each, pasted into `review.md`'s `## Evidence` at deploy stage.
8. **`docs/reference/dispatch_closure_state_bug.md`** — append the
   Resolution section.
9. **`mypy src`** — confirm the whole signature change typechecks clean.
10. **`make verify`** — the full gate.

Step 7 (the three network scripts) lands after everything else already
typechecks and the unit suite is green (steps 1-6, 9) — a manual-run
failure there is then isolated to that one closure's own logic, not a
sign the type contract underneath it is wrong.

## Risks

**What could this break?** Every real dispatch-shaped callable in the
repo — confirmed complete by `grep -rl dispatch src tests scripts`
(false positive excluded: `test_model_access.py`'s "dispatched" is
unrelated prose about HTTP attempts, not this callable). That's the
eleven files listed above. Nothing outside them constructs a
`dispatch`-shaped callable or reads a `dispatch()` return value directly
— `take_turn`/`run_child`'s own callers never touch the return value
themselves, so their own callers (all inside the files already listed)
need no further changes beyond what's already planned.

**Which step is riskiest, and why?** Step 7 — the three hand-run
scripts, for the same reason spec.md's own Concerns names: none of them
run under `make test`, so a mistake in how one constructs its
`DagResult` (a wrong `trace` shape, an empty `text` on a failure path)
is caught only by a manual run against a real model, not by `mypy` (the
shapes still typecheck even if the values inside are wrong) or the unit
suite. Mitigated by ordering: everything else is green first, so a
failure here is isolated to one script's own logic.

**Second-riskiest:** step 5's volume — roughly thirteen call sites
across three test files, mechanical but easy to miss one. Mitigated by
`grep -rn "return \"" tests/unit/test_conversation.py
tests/unit/test_conversation_store.py tests/unit/test_eval_harness.py`
inside each dispatch-shaped `async def` as a completeness check before
running `make test`, and by `make test` itself — a stub still returning a
bare string fails at the `mypy` step (9) even if pytest itself doesn't
catch it structurally, so nothing silently stays on the old contract.

**Which options did spec.md already reject, and is this plan drifting
back toward one?** Re-read: (a) JSON-string-in-`text` as a substitute for
real fields — not done; `Artifact`/`trace` are real fields, never encoded
into `text`. (b) A dual signature / transition shim — every one of the
eleven files above moves together; nothing is left on the old contract
after this plan's steps complete. (c) A `metadata: dict[str, Any]` escape
hatch — not present; every field on `DagResult`/`Artifact`/`NodeTrace`
traces to spec.md's Requirements. No drift found. One thing this wider
scope does confirm rather than contradict: spec.md's own guideline-2
reasoning (no speculative fields) matters more, not less, now that
thirteen-plus call sites construct this type by hand — a field nothing
needs yet would be thirteen-plus places guessing at a value for it.

## Proof

- `mypy src` — clean, no new `# type: ignore`, no `Any` introduced.
  (`make typecheck` → `TYPES OK`.)
- `make test` — every updated test file green:
  `test_plugins.py` (new) proves the three dataclasses are frozen and
  constructible; `test_conversation.py`, `test_conversation_store.py`,
  `test_eval_harness.py` all pass with every stub returning `DagResult`;
  the new `test_conversation.py` assertion proves `run_turn` renders
  `DagResult.text` specifically (a `DagResult` whose `text` differs from
  its `repr()` would fail this if the old `str(raw_result)` behaviour
  regressed).
- `make lint` — `LINT OK`, including the pre-commit reference-citation
  check (this plan and the eventual review.md cite
  `docs/reference/plugin_blueprint.md` and
  `docs/reference/dispatch_closure_state_bug.md`, both already tracked).
- `make chain` — `CHAIN OK`.
- Deploy-stage evidence (not part of this plan's own verification, but
  required before D1 can close): fresh manual runs of all three network
  scripts (`prove_conversation_e2e.py`, `prove_context_completion.py`,
  `plugin_dispatch.py`) against a real `OPENROUTER_API_KEY`, full stdout
  of each pasted into `review.md`'s `## Evidence`.
- `make verify` → `VERIFY OK`, full output pasted when this item is
  reported done.
