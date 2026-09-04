# Review: One real conversation proves every promise this backbone made (from plan.md 2026-09-04)

Reviewed: `git diff HEAD` (nothing in this work item is committed yet) — 6 files, +768/-0
Reviewer context: same session as build. The actual three-pass review was delegated to a fresh subagent given no prior context beyond `git diff HEAD` and `intent.md`/`spec.md`/`plan.md` (deploy-skill §1); a specific, subtle claim it made (a closure/mutable-state bug) was independently re-verified in this session with two standalone, no-network repros before being written up here — not taken on the subagent's word alone.
Second opinion: none — ran during build (self-check: `/ponytail-review` + four parallel `/simplify` passes), not repeated here by design.

## Evidence

The real, billed run against OpenRouter (`deepseek/deepseek-v4-flash-0731`) happened once, during build, before a subsequent self-check refactor. That refactor (extracting `_assert_child_isolated()`, replacing a one-element-list state-sharing trick with a `nonlocal conversation` closure variable, dropping one logically-redundant assertion) was verified behaviour-identical via a local, mocked, no-network re-run rather than spending real API credit a second time on a purely structural change — see `## Findings` below for where that same closure mechanism turned out to hide a real bug the mocked re-run's scenario never exercised (it never called a plugin twice, so it never triggered the collision).

```
$ [real run, pre-refactor code — see note above]
initial prompt_sha256=b61311c5dc915d9649f8833e2e5e9da709c1cd893ddd29a5104290962a1149db

=== turn 1: plain exchange, no plugin ===
exit_reason=ExitReason.COMPLETED final_text='Hello there!'
[ok] turn 1 completed; prompt_sha256 unchanged

=== turn 2: plugin-a ===
    [plugin-a] child key=conv09-proof/run-1/child/plugin_a_child/0 exit=ExitReason.COMPLETED final_text=' The webhook payload indicates a ping event, confirming the connection is active. ACKNOWLEDGED'
exit_reason=ExitReason.COMPLETED final_text='Plugin-a flow ran successfully and reported the connection as active.'
[ok] turn 2 completed; prompt_sha256 unchanged; child isolation verified above

=== turn 3: plugin-b ===
    [plugin-b] child key=conv09-proof/run-1/child/plugin_b_child/0 exit=ExitReason.COMPLETED final_text='Hello!'
exit_reason=ExitReason.COMPLETED final_text='Plugin-b flow completed and acknowledged the note.'
[ok] turn 3 completed; prompt_sha256 unchanged
iteration_budget after turn 3: 5/5

=== turn 4: forced budget exhaustion ===
exit_reason=ExitReason.BUDGET_EXHAUSTED detail='iteration budget exhausted (5)'
[ok] turn 4 exhausted the budget as intended
[ok] final transcript is well-formed: no dangling tool calls

ALL ASSERTIONS PASSED
```

```
$ make verify
docs/tasks/CONV-09-deploy-evidence: all present artifacts valid
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
ruff-format..............................................................Passed
shellcheck...............................................................Passed
Detect secrets...........................................................Passed
LINT OK
Success: no issues found in 5 source files
TYPES OK
▶ hermetic run: TZ=UTC LANG=C.UTF-8 PYTHONHASHSEED=0, empty env
........................................................................ [ 38%]
........................................................................ [ 77%]
..........................................                               [100%]
186 passed in 1.81s
TESTS OK
VERIFY OK
```

Note, matching CONV-08's own finding: the first `make lint` runs for this work item's new files were clean only because they were untracked; `git add -N` before this run surfaced (and this session fixed) an executable-bit gap and one over-length line before the output above.

## Findings

**Compliance verification performed:** every `plan.md § Proof` item traced to what discharges it; every `spec.md § Acceptance criteria` bullet matched to the specific assertion/print statement that satisfies it; `spec.md § Rejected alternatives` checked for drift on all four items (none found — real provider used, `pending_tool_call_ids()` used directly, tool choice directed explicitly, unprefixed tool names); the five design principles checked explicitly, including confirming the script's shape matches `scripts/prove_model_access.py`'s established precedent; every file in the diff matched against `plan.md § Files that change` (exact match).

### Important

- [Bugs] **`dispatch()`'s `nonlocal conversation` write from `run_child()` is silently discarded the moment `take_turn()` returns**, so `next_child_seq` never actually advances across turns. `take_turn()` (`conversation.py:1024-1070`) builds its return value from its *own* local `conversation` parameter — a snapshot bound at the moment it was called — not from whatever the nested `dispatch` closure mutated via `nonlocal` while running deep inside it. Verified independently with two no-network repros against the real code: inside `dispatch`, `conversation.next_child_seq` correctly reads `1` right after `run_child()` returns; the instant the enclosing `take_turn()` call returns, `main()`'s own `conversation.next_child_seq` is back to `0`. The comment justifying the `nonlocal` choice ("`next_child_seq` advancing... needs to reach the next `take_turn()` call") is factually wrong — it reaches the next `dispatch` call *within the same turn*, never the next turn. This doesn't invalidate the captured Evidence above: plugin-a and plugin-b spawn children under different `node_name`s (`plugin_a_child` vs `plugin_b_child`), so the bug never produces a key collision in this specific 4-turn script, and every assertion made about the specific children spawned remains true. But it is a real, confirmed defect — a repeat call to the same plugin (or a future extension of this script) would silently mint two children under the identical key, exactly the "unique natural key" property this proof exists to demonstrate.
  **Proposed fix** (not yet applied — see Decision): track `next_child_seq` as its own separate counter inside `main()`, since `run_child`'s own docstring already establishes that a parent's only field that changes across a spawn is `next_child_seq` — nothing else needs threading through `dispatch` at all. Construct each call's `parent` argument as `dataclasses.replace(conversation, next_child_seq=<the separately-tracked counter>)`, and update that counter (not `conversation` itself) from `run_child`'s returned updated-parent value. Removes the incorrect `nonlocal conversation = ...` reassignment inside `dispatch` entirely; `conversation` stays owned solely by `main()`'s own turn loop, which is the one place `take_turn()` actually threads it correctly.
- [Compliance] **`spec.md § Design`'s "Sizing the iteration budget" narrative is imprecise about turn 4's real cost.** It says turn 4 exits "before any model call" / "before spending anything on that turn" — true only of the *tracked* `IterationBudget`. `run_turn`'s EPILOGUE (`conversation.py:838-861`, pre-existing, unchanged, out of this work item's reach) is explicitly *not* gated by `consume_iteration()`: whenever `exit_reason == BUDGET_EXHAUSTED and final_text is None`, it fires one more real, uncounted `complete()` call requesting a summary — exactly turn 4's situation. The real run made one more billed call than spec.md's own narrative describes. This doesn't break any assertion (`exit_reason` stays `BUDGET_EXHAUSTED` regardless), but the script doesn't even print the outcome (`result4.final_text` is never printed, only `exit_reason`/`detail`), so a reader of the pasted Evidence can't see it either.
- [Compliance] **`spec.md` documents nothing about the `nonlocal conversation` mechanism the build-stage self-check introduced**, despite being amended (during that same self-check) to describe `_assert_child_isolated()` in detail. This is the same class of gap CONV-08's own review caught: code drifting ahead of what `spec.md`'s Design/Interface sections actually say, once a self-check changes the shipped mechanism. It is not cosmetic here — the undocumented mechanism is exactly where the bug above lives, so a reviewer reading `spec.md` alone had no way to reason about it.

### Nits

- [Bugs] `now=0.0` is repeated as a literal at every `take_turn`/`run_child` call site instead of one module-level constant, unlike `MODEL`/`PROVIDER` at the top of the file.
- [Compliance] Requirement 4's "its own iteration budget" clause is satisfied only by construction (`ChildSpec(budget=IterationBudget(max_total=3))` visibly differs from the parent's `max_total=5`), not by a runtime `assert` — consistent with `spec.md § Assertions`' own scoping (which doesn't claim to check this at runtime either), so not drift, just worth a one-line note in `spec.md` if a future reader might expect an assert here.
- [Security] None. The script never prints, logs, or hardcodes `OPENROUTER_API_KEY` — only checks truthiness before exiting, matching `prove_model_access.py`'s own guard exactly. `SADANA_PLUGINS_DIR` points at a local fixtures path, never attacker-influenced.

### Raised, not findings

- `spec.md`'s requirement 4 could be strengthened with an explicit `assert child.iteration_budget.max_total != parent.iteration_budget.max_total`, but the current construction-based evidence is defensible as-is — a possible future tightening, not a gap in this diff.

## Decision

Approved by Adam, 2026-09-04. All three Important findings accepted as
known, documented limitations rather than fixed in this branch — the
`next_child_seq` propagation bug (finding #1) is deferred to a future
maintain-stage work item rather than patched into this closed one; its
root cause, blast radius (none, for this specific proof — the two fixture
plugins never reuse a `node_name`, so the collision it causes never
triggers), and what a real fix would and would not resolve are written up
separately in `docs/reference/dispatch_closure_state_bug.md`, produced
during this same review conversation. Findings #2 and #3 (the imprecise
budget narrative and the undocumented `nonlocal` mechanism in spec.md) are
accepted as-is for the same reason — both are downstream of the same
deferred fix and would need re-writing again once it lands.
