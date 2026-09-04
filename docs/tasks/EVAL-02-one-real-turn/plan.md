# Plan: One real turn proves the whole pipeline is actually right, not just running (from intent.md 2026-09-04)

## Files that change

- `src/sadana/eval_harness.py` — `run_task()` gains a `dispatch_factory: Callable[[Conversation], Callable[[str, dict], Awaitable[str]]] = _no_tools_dispatch_factory` keyword argument. Purely additive; every existing call site keeps working unchanged. Revised from a first, simpler `dispatch:
  Callable[[str, dict], Awaitable[str]]` shape once this work item's own
  self-check found it forced a caller needing `run_child()`'s real parent
  to build an independent, silently-divergence-prone second `Conversation`
  — see spec.md §Design and §Concerns for the full account.
- `tests/unit/test_eval_harness.py` — one new case: `run_task()` with a
  real `dispatch_factory` override (mocked network), proving a tool call
  reaches the grader via the message history, and that the factory
  receives the real `Conversation` `run_task()` built.
- `scripts/eval/tasks/plugin_dispatch.py` (new) — the task itself: a
  `ConversationTemplate` mounting `plugin-a`, a `make_dispatch(parent)`
  reusing CONV-09's own plugin-a shape against the already-committed
  `tests/fixtures/plugins/plugin-a/skills/plugin-a-skill`, a `Task` whose
  `grade()` reads the message history structurally (tool called? child
  acknowledged?) with partial credit, and a `__main__` guard doing a real
  run (passing `make_dispatch` as `run_task()`'s `dispatch_factory`) +
  `save_result()`.
- Nothing else. `conversation.py` doesn't change; `results_dir_from_config()`
  is reused unchanged.

## Order of work

1. **`run_task()`'s new `dispatch_factory` parameter** in `eval_harness.py`
   — smallest possible change, no callers touched, existing tests still
   pass unchanged. Confirms nothing broke before building on top of it.
2. **New mocked test** in `tests/unit/test_eval_harness.py` exercising
   the new parameter directly — proves the plumbing (a real dispatch
   function's result reaching the grader) before the real task exists,
   isolating that risk from the fixture/prompt-design risk in step 3.
3. **`scripts/eval/tasks/plugin_dispatch.py`** — template, `make_dispatch`
   (reusing plugin-a's fixture as-is), `Task` with its structural grader,
   `__main__` guard. Built and locally smoke-checked (mocked
   `model_access.send`, no network) before any real spend, same
   discipline every prior real-round-trip work item in this project used.
4. **Real run**, `OPENROUTER_API_KEY` sourced by hand, captured as this
   work item's Evidence.
5. `/ponytail-review` + `/simplify` self-check.
6. `make verify`.

## Risks

**What could this change break that already works?** Minimal surface:
`run_task()`'s new parameter has a default matching its current
hardcoded behavior, so every existing caller (EVAL-01's own tests,
`prove_eval_harness.py`) is provably unaffected — step 1 confirms this
with the existing suite before anything else is built. `conversation.py`
isn't touched at all.

**Which step is riskiest, and why that one?** Step 3 — real-model
behavior over multiple internal calls (parent asks for the tool, the
spawned child completes, the parent finishes) is the same shape CONV-09's
own turn 2 already proved works, but this is the first time it's driven
through `run_task()`'s own machinery rather than a hand-written turn
loop, and the first time its outcome is *graded* rather than just
asserted inline. Ordered after step 2's isolated plumbing proof and
behind a mandatory local mocked check, so a wrong grader or a wrong
dispatch wiring is caught for free before real money is spent finding it.

**Which options did spec.md already reject, and is this plan drifting
toward one?** Checked spec.md § Rejected alternatives: not building a
generic battery runner (only one task file exists, no runner beyond its
own `__main__` guard); not mounting both CONV-09 fixture plugins (only
`plugin-a`); not moving `dispatch_factory` onto `Task` itself (it stays a
`run_task()` call-site argument, same as `template`); not reimplementing
the fixture plugin from scratch (step 3 imports the existing
`tests/fixtures/plugins/plugin-a/skills/plugin-a-skill` path directly).
No drift found.

## Proof

- `tests/unit/test_eval_harness.py` passing under `make test`, including
  the new dispatch-override case.
- The local mocked smoke-check's output, confirming the wiring before
  real spend (not pasted into review.md — an intermediate build-time
  check, per this project's established pattern).
- The real run's full output (step 4), pasted as this work item's
  Evidence — `exit_reason == COMPLETED`, `score == 1.0`.
- `make verify` output, pasted in full, ending `VERIFY OK`.
