# Review: A check on the agent's actual behaviour can be written once and run again (from plan.md 2026-09-04)

Reviewed: `git diff HEAD` (nothing in this work item is committed yet) — 7 files, +975/-0
Reviewer context: same session as build. The actual three-pass review was delegated to a fresh subagent given no prior context beyond `git diff HEAD` and `intent.md`/`spec.md`/`plan.md` (deploy-skill §1); it independently re-traced `run_task()` against `create_conversation()`/`take_turn()`'s real signatures and confirmed `results_dir_from_config()` matches `conversation_store.py`'s `store_path_from_config()` pattern exactly, rather than trusting spec.md's own account of either.
Second opinion: none — ran during build (self-check: `/ponytail-review` + four parallel `/simplify` passes), not repeated here by design.

## Evidence

```
$ make verify
docs/tasks/EVAL-01-task-runner-core: all present artifacts valid
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
shellcheck.................................................................Passed
Detect secrets.............................................................Passed
LINT OK
Success: no issues found in 6 source files
TYPES OK
▶ hermetic run: TZ=UTC LANG=C.UTF-8 PYTHONHASHSEED=0, empty env
........................................................................ [ 37%]
........................................................................ [ 75%]
................................................                         [100%]
192 passed in 2.00s
TESTS OK
VERIFY OK
```

Real run, `scripts/prove_eval_harness.py` against live OpenRouter
(`deepseek/deepseek-v4-flash-0731`), before this session's self-check fix
to where results are saved (the fix changed only the destination path,
not the model-calling logic exercised below — see § Findings):

```
task_id=smoke_exact_reply
exit_reason=ExitReason.COMPLETED
final_text=' PASS'
score=1.0
[ok] task completed
[ok] result saved to /home/adam/sadana-wip/sadana-harness/scripts/eval/results/smoke_exact_reply__0.json

ALL ASSERTIONS PASSED
```

After the fix, a local mocked re-run (no real network — the same
discipline `prove_conversation_e2e.py`'s own build used) confirmed
`save_result()` now correctly writes under `results_dir_from_config()`'s
path instead:

```
[ok] result saved to /home/adam/.local/state/sadana/eval/results/smoke_exact_reply__0.json
```

No second real-money spend was made to re-prove this — the change only
moved *where* the file lands, not the real model call already proven
above, mirroring CONV-10's own reasoning for not re-spending on a purely
structural fix.

## Findings

**Compliance verification performed:** every `plan.md § Proof` item traced to what discharges it; every `spec.md § Acceptance criteria` item matched to the code that satisfies it; `spec.md § Rejected alternatives` checked for drift on all three items (none found — `Task` has no embedded template or `ctx` object, `save_result()` has no resume/dedup logic); the five design principles checked explicitly, including confirming zero lines of `git diff --stat HEAD` touch `src/sadana/conversation.py`; every file in the diff matched against `plan.md § Files that change`; and `spec.md § Concerns`' own self-check finding (a first draft hardcoding a script-relative results path) verified fixed in the *current* code, with no leftover hardcoded path anywhere in the diff.

### Important

None.

### Nits

- [Bugs] `save_result()` (`src/sadana/eval_harness.py`) keys its filename with `int(run.timestamp)` — second-resolution, not sub-second. Two runs of the same `task_id` within the same integer-second window (e.g. `timestamp=10.1` and `timestamp=10.9`) would collide and the second would silently overwrite the first, with no error. Both the function's own docstring and `spec.md § Design` state "never overwrites... by construction," which is slightly stronger than what the code actually guarantees. Low real-world impact at this work item's current scale (one throwaway task, run by hand), but worth knowing before a battery of same-second automated runs (EVAL-03) is built on top of it.

## Decision

Approved by Adam, 2026-09-04. The Nit (second-resolution filename
timestamps) accepted as-is, not fixed — worth revisiting if EVAL-03
builds an automated battery capable of two same-second runs of one task.
