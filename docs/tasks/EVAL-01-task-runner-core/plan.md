# Plan: A check on the agent's actual behaviour can be written once and run again (from intent.md 2026-09-04)

## Files that change

- `src/sadana/eval_harness.py` (new) — `Task`, `TaskRun`, `run_task()`,
  `save_result()`, `load_result()`, `run_task_key()`,
  `results_dir_from_config()`, `_no_tools_dispatch()`, `_compress_noop()`.
  Imports from `sadana.conversation` (`create_conversation`, `take_turn`,
  `ConversationTemplate`, `ConversationKey`, `ExitReason`, `IterationBudget`,
  `Message`, `TurnResult`) and `sadana.config` (`env_path`, `get_paths`,
  matching `conversation_store.py`'s `store_path_from_config()` pattern —
  a self-check finding, not part of the original design; see spec.md
  §Concerns).
- `tests/unit/test_eval_harness.py` (new) — mocked `model_access.send`,
  no real network, per `testing-conventions`.
- `scripts/prove_eval_harness.py` (new) — the one real, throwaway smoke
  task, following `scripts/prove_conversation_e2e.py`'s established shape
  (sys.path shim, `MODEL`/`PROVIDER` constants, `__main__` guard checking
  `OPENROUTER_API_KEY`).
- Nothing under `src/sadana/conversation.py` changes (spec.md requirement 8).

## Order of work

1. **`eval_harness.py` core**, no callers yet — `Task`/`TaskRun` dataclasses,
   `_no_tools_dispatch`/`_compress_noop` helpers, `run_task()`,
   `save_result()`.
2. **`tests/unit/test_eval_harness.py`**, mocked `model_access.send`:
   `run_task()`'s happy path (COMPLETED, graded), a non-COMPLETED exit
   still producing a graded `TaskRun`, `save_result()`'s round trip, and
   the default-`key` derivation not colliding across two calls with
   different `now`. Run via `make test` to confirm mechanics before any
   real network is involved.
3. **`scripts/prove_eval_harness.py`** — one throwaway smoke task (e.g.
   "reply with exactly the word PASS"), built like
   `prove_model_access.py`/`prove_conversation_e2e.py`: real model,
   `OPENROUTER_API_KEY` guard, prints the resulting `TaskRun`.
4. **Local, no-network smoke-check of the script itself** (mocked
   `model_access.send`, ad-hoc, not committed) before spending real money.
5. **Real run**, `OPENROUTER_API_KEY` sourced by hand, captured as this
   work item's Evidence.
6. `/ponytail-review` + `/simplify` self-check.
7. `make verify`.

## Risks

**What could this change break that already works?** Nothing — new
module, new test file, new script, no existing file touched. `make
verify`'s existing suite has zero new surface to regress against beyond
the new test file itself.

**Which step is riskiest, and why that one?** Step 1/2 together — the
first time this project builds a *new* `src/` module on top of
`conversation.py` rather than extending it (CONV-08's
`conversation_store.py` is the only precedent, and that one persisted a
`Conversation`, it didn't drive one through a turn). Getting `run_task()`'s
exact call shape right against `create_conversation()`/`take_turn()`
(right budget, right template, right dispatch/compress defaults) is the
actual new ground; ordered first and proven with mocked tests before any
real spend, so a wrong call shape is caught for free, not after paying
for it.

**Which options did spec.md already reject, and is this plan drifting
toward one?** Checked spec.md § Rejected alternatives: `Task` doesn't
embed its own `ConversationTemplate` (`run_task()` takes `template` as a
separate argument); no `ctx` aggregation type for graders (`grade` takes
the plain `TurnResult` + message tuple); no resume/dedup result storage
(`save_result()` writes one file per run). No drift found.

## Proof

- `tests/unit/test_eval_harness.py` passing under `make test`.
- The real run's full output (step 5), pasted as this work item's
  Evidence — the Test stage's deliverable, per CLAUDE.md's rule on a
  block's first real external round trip.
- The self-check's findings and what was taken from them.
- `make verify` output, pasted in full, ending `VERIFY OK`.
