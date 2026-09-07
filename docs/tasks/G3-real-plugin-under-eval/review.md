# Review: a real plugin run, actually checked (from plan.md 2026-09-07)

Reviewed: 33e4d1e..working-tree — 15 files, +506/-310 (10 tracked files modified, +420/-310; 5 new untracked fixture files, +86/-0)
Reviewer context: fresh session, no builder context.
Second opinion: none — ran during build (self-check /ponytail-review + /simplify), not repeated here by design.

## Evidence

```
docs/tasks/G3-real-plugin-under-eval: all present artifacts valid
CHAIN OK
trim trailing whitespace.................................................Passed
fix end of files.........................................................Passed
mixed line ending........................................................Passed
check for case conflicts.................................................Passed
check yaml...............................................................Passed
check toml...............................................................Passed
check json...............................................................Passed
check for merge conflicts................................................Passed
check for added large files..............................................Passed
check that scripts with shebangs are executable..........................Passed
check that executables have shebangs.....................................Passed
detect private key.......................................................Passed
ruff.....................................................................Passed
ruff-format...............................................................Passed
shellcheck................................................................Passed
Detect secrets............................................................Passed
docs/reference/ citations resolve to tracked files........................Passed
LINT OK
Success: no issues found in 12 source files
TYPES OK
▶ hermetic run: TZ=UTC LANG=C.UTF-8 PYTHONHASHSEED=0, empty env
........................................................................ [ 20%]
........................................................................ [ 41%]
........................................................................ [ 62%]
........................................................................ [ 83%]
........................................................                 [100%]
344 passed in 3.00s
TESTS OK
VERIFY OK
```

Syntax/import check of the two rewritten live-proof scripts (NOT a substitute for the live run — only proves the rewrite parses; no network or model call was made):

```
--- prove_conversation_e2e.py ---
OK
--- plugin_dispatch.py (eval task) ---
OK
```

## Findings

Two passes ran — bugs/security (clean) and compliance (two Important gaps found, detailed below) — against plan.md, spec.md, and this project's design principles.

### Important

1. **`CLAUDE.md` is touched by this diff but is not listed in `plan.md`'s `## Files that change` section.** `git diff HEAD -- CLAUDE.md` adds one bullet under "Please do": `A Task's grade() reads DagResult/NodeTrace structure to judge plugin behavior — never a substring match against rendered message content.` The content itself is accurate and consistent with what the diff actually does (`scripts/eval/tasks/plugin_dispatch.py`'s rewritten `grade()`, `src/sadana/eval_harness.py`'s widened `Task.grade` signature), but plan.md never named this file, so per this project's own Deploy policy ("A file touched that the plan did not name is an Important finding") this is flagged.

   **Fixed**: `plan.md`'s `## Files that change` now lists `CLAUDE.md` and the rule it adds, matching the same precedent F1/G1's own reviews already established for this exact gap.

2. **`scripts/prove_conversation_e2e.py` does not build the dispatch "once and reused across all four turns" the way `plan.md`'s step 9 describes it.** Plan.md's Order of work, step 9, said: "build the real `plugin_set` and dispatch once, reused across all four turns via `plugin_dispatch.take_turn_and_reconcile`... Turns 1 and 4 keep their existing bodies and assertions untouched." What the diff actually does (`scripts/prove_conversation_e2e.py`'s `build_dispatch(conversation)` helper and its four call sites at the `=== turn N ===` prints) is rebuild `dispatch`/`tracker` freshly before *every* turn, including turns 1 and 4. This is not a bug: it is required by `plugin_dispatch.build_dispatch`'s own docstring and by `docs/reference/dispatch_closure_state_bug.md`, which documents exactly why reusing one dispatch closure across multiple `take_turn` calls silently loses `next_child_seq` updates. `scripts/prove_plugin_dispatch_e2e.py` (D3/D4, already-closed prior art) rebuilds fresh every turn for the same reason.

   **Fixed**: `plan.md`'s step 9 rewritten to describe the actual, correct behavior (fresh `build_dispatch` before every turn, plugin_set built once) instead of the looser "built once, reused" phrasing.

No other Important findings. Checked and clean:
- The three-copies-of-`capturing_dispatch` duplication the build-stage self-check found is actually fixed: `grep -rn "_capturing_dispatch\|capturing_dispatch"` across the repo shows exactly one definition, `plugin_dispatch.capturing_dispatch()` (`src/sadana/plugin_dispatch.py:34`), imported and called by `src/sadana/eval_harness.py:131` and `scripts/prove_conversation_e2e.py:159,189`. Neither file redefines a local `_capturing_dispatch`. (`scripts/prove_plugin_dispatch_e2e.py` still has its own pre-existing `_make_capturing_dispatch` — that script is not in this diff, not named in plan.md's file list, and belongs to the already-closed D3/D4 work items; leaving a closed work item's script alone rather than editing it here is correct per CLAUDE.md's "do not edit a closed work item's artifacts" posture, so this is not a stray fourth copy introduced by G3.)
- `src/sadana/plugin_manifest.py`, `src/sadana/plugins.py`, and `src/sadana/conversation.py` are untouched (`git diff HEAD --stat` for all three is empty), matching plan.md's explicit "No change" list.
- `run_graph`'s existing `call`-node handling (`src/sadana/plugin_manifest.py:357-369`) already does exactly what `plugin-a/init.py`'s `fetch_webhook` needs: on an `Artifact` return it appends to `artifacts` and threads `.ref` forward as the next node's input — verified by reading the function, and independently confirmed by `test_real_plugin_a_acknowledged_branch_end_to_end` (`tests/unit/test_plugin_manifest.py`) asserting the exact `artifacts` tuple and trace order spec.md's acceptance criteria name.
- `execution.run_http`/`HttpRequest`/`Success` are called with the real signatures (`src/sadana/execution.py:27-48`); `plugin-a/init.py`'s usage matches exactly.
- No drift toward any of spec.md's `## Rejected alternatives`: `conversation.py`/`TurnResult` untouched, `TaskRun` (`src/sadana/eval_harness.py:55-65`) gained no new field, `scripts/eval/tasks/plugin_dispatch.py`'s old hand-written `make_dispatch()` is fully deleted (not patched), and `plugin-a`'s `call` node still targets `https://example.com`.
- No secrets, no injection surface: the `call` node's URL is a fixed literal, never built from `arguments`; no credentials appear in any new or changed file.
- Five design principles: (1) learn from the reference — spec.md itself documents this item draws on the project's own prior art (`prove_plugin_dispatch_e2e.py`, `prove_execution.py`) rather than hermes-agent, which is a reasonable reading since every mechanism reused here (`call`/`route`/`ask`, `Artifact`, `DagResult`) was already ported from hermes in earlier blocks (F1/G1/G2); (2) reduce the number of bets — no new module, no new dependency, one new function (`capturing_dispatch`) reused three ways; (3) more plugins, not more core — the only core-file change is one defaulted kwarg on `build_dispatch` and one widened `Callable` type on `Task.grade`, both additive and backward-compatible (confirmed no existing `test_plugin_dispatch.py`/`test_eval_harness.py` call site needed further change beyond the mechanical signature bump); (4) catch the scenario at the least step-cost — plan.md's own step 3 (fixtures proven end-to-end with faked network/model in a unit test) runs before the live scripts, exactly as the Risks section argues; (5) minimise mutable state — `capturing_dispatch`'s `captured` list is function-local and freshly allocated per call, `ChildSeqTracker` is unchanged pre-existing state scoped to one dispatch closure's lifetime.

### Nits

1. `scripts/prove_conversation_e2e.py:54` and `scripts/prove_plugin_dispatch_e2e.py:53` now each define an identical 4-line `_print_dag_result` helper. Small, but it's the same duplication shape the build-stage self-check already fixed once for `capturing_dispatch` — could be lifted into a shared script-utility next time either file is touched again.

## Outstanding before this item can be considered fully deployed

`scripts/prove_conversation_e2e.py` and `scripts/eval/tasks/plugin_dispatch.py` still need to be run manually with a real `OPENROUTER_API_KEY`, and their full output pasted into this file's `## Evidence` section, per plan.md's own Proof requirements (the final two bullets of `## Proof`, and spec.md's last two `## Acceptance criteria` items). This reviewing environment has no such key available, and running these scripts spends a human's real API budget against a real model and a real network call — not something an automated review should do on its own judgement. This review's `make verify` run and the two `ast.parse` syntax checks above are the closest substitute available here, and are explicitly not a replacement for the live run. A human needs to: export a real `OPENROUTER_API_KEY`, run both scripts, confirm `scripts/prove_conversation_e2e.py` prints `ALL ASSERTIONS PASSED` (or equivalent) for all four turns including the new turn-2 `call`-node/approval/artifact assertions, confirm `scripts/eval/tasks/plugin_dispatch.py` prints `score=1.0` and its own final assertions, and paste both outputs into this file's `## Evidence` section before this item is truly done.

## Decision

Approved by Adam Aubry, 2026-09-08, with both Important findings fixed in
this branch: `plan.md`'s `## Files that change` corrected to list
`CLAUDE.md`, and step 9 rewritten to describe the dispatch actually being
rebuilt fresh every turn rather than "built once, reused."

Live evidence still outstanding: no `OPENROUTER_API_KEY` was available in
either the build or review session, so `scripts/prove_conversation_e2e.py`
and `scripts/eval/tasks/plugin_dispatch.py` have not yet been run for
real. Per `## Outstanding` above, their full output still needs to be
pasted into `## Evidence` before this item is a complete, evidence-backed
deploy — approval here covers the code, not a claim that the live proof
has run.
