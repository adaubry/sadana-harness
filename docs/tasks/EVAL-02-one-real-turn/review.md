# Review: One real turn proves the whole pipeline is actually right, not just running (from plan.md 2026-09-04)

Reviewed: `git diff HEAD` (nothing in this work item is committed yet) — 6 files, +682/-5
Reviewer context: same session as build. The actual three-pass review was delegated to a fresh subagent given no prior context beyond `git diff HEAD` and `intent.md`/`spec.md`/`plan.md` (deploy-skill §1); it independently re-traced `dispatch_factory`'s call site in `run_task()` and `make_dispatch()`'s use of it against the real code, specifically checking whether this diff repeats the class of bug `docs/reference/dispatch_closure_state_bug.md` describes in a different file, rather than trusting spec.md's own account that it doesn't.
Second opinion: none — ran during build (self-check: `/ponytail-review` + four parallel `/simplify` passes — the passes that actually caught and drove this work item's own `dispatch_factory` redesign), not repeated here by design.

## Evidence

```
$ make verify
docs/tasks/EVAL-02-one-real-turn: all present artifacts valid
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
ruff-format.................................................................Passed
shellcheck...................................................................Passed
Detect secrets................................................................Passed
LINT OK
Success: no issues found in 6 source files
TYPES OK
▶ hermetic run: TZ=UTC LANG=C.UTF-8 PYTHONHASHSEED=0, empty env
........................................................................ [ 37%]
........................................................................ [ 74%]
.................................................                        [100%]
193 passed in 1.81s
TESTS OK
VERIFY OK
```

Real run, `scripts/eval/tasks/plugin_dispatch.py` against live OpenRouter
(`deepseek/deepseek-v4-flash-0731`), after this work item's own
`dispatch_factory` redesign (local mocked re-run confirmed identical
outcome before this real run — the model call itself was never affected
by the redesign, only how the dispatch handler reaches its parent
conversation):

```
task_id=plugin_dispatch
exit_reason=ExitReason.COMPLETED
final_text='The plugin-a flow was run successfully. It received a webhook-style input with a ping event payload and acknowledged it. Report: **ACKNOWLEDGED**'
score=1.0
[ok] plugin dispatch verified end to end
[ok] result saved to /home/adam/.local/state/sadana/eval/results/plugin_dispatch__0.json

ALL ASSERTIONS PASSED
```

## Findings

**Compliance verification performed:** every `plan.md § Proof` item traced to what discharges it; every `spec.md § Acceptance criteria` item matched to the code that satisfies it; `spec.md § Rejected alternatives` checked for drift on all four items, including its own last entry (the plain-`dispatch` first draft, reversed) — confirmed the shipped code matches what that entry says was decided, not the discarded draft; the five design principles checked explicitly, including confirming zero lines of `git diff --stat HEAD` touch `src/sadana/conversation.py`, and that `plugin_dispatch.py` reuses the existing `tests/fixtures/plugins/plugin-a/skills/plugin-a-skill` file rather than duplicating it; every file in the diff matched against `plan.md § Files that change` (itself corrected mid-build to describe `dispatch_factory`, not the original plan).

### Important

None. Specifically verified — not merely asserted — that this diff does not repeat the class of bug `docs/reference/dispatch_closure_state_bug.md` documents in a different file: `dispatch_factory` is called with the actual `conversation` `run_task()` builds, immediately after `create_conversation()` and before `take_turn()` (`src/sadana/eval_harness.py`), and `make_dispatch(parent)` threads that exact object into `run_child(parent, ...)` — no independent second `create_conversation()` call anywhere in the shipped code, no `nonlocal`, no state that could go stale between calls.

### Nits

- [Bugs] `make_dispatch()`'s `parent` is a single closed-over snapshot, not reconciled across multiple calls the way `prove_conversation_e2e.py`'s post-CONV-10 fix does. If the model ever issued `plugin_a_entry` twice within one graded turn (the prompt makes this unlikely, but nothing in the code rules it out), both `run_child()` calls would reuse `next_child_seq=0` and mint the same child key. Not a repeat of `dispatch_closure_state_bug.md`'s actual bug (no silently-discarded state — this is a pure function of an immutable value) and has no observable consequence today (nothing persists or dedupes child keys), but the in-code justification ("this task calls `run_child()` exactly once") is an assumption about model behaviour the code doesn't itself enforce.
- [Compliance] Spec's acceptance criterion for a local, no-network mocked proof of `plugin_dispatch.py`'s wiring has no artifact in this diff to check — by design, matching CONV-09's own precedent (an intermediate build-time check, not pasted anywhere). Noted as unverifiable from the diff alone, not treated as a gap.
- [Security] None. No new external input surface; credential handling is unchanged, pre-existing lower-layer behaviour.

## What this proves, in one place

A quick reminder for a reader who doesn't want to re-derive this from
spec.md: this real run is evidence that, end to end, against a real
model — (1) a model told about one mounted add-on procedure, via this
project's own plugin-catalog rendering, actually calls it when the
prompt asks for it; (2) that call correctly spawns a focused
child conversation (`run_child()`), which gets its own real model
completion and comes back with a genuine acknowledgement, not a stub;
(3) the parent conversation correctly folds that child's report into its
own final answer; and (4) the eval harness's own grading — a plain,
programmatic check of the message history, no second model's opinion —
correctly recognizes all of that happened and scores it `1.0`. Before
this work item, every one of those four things had only ever been
checked by a person watching a script run once. Now it's a `Task` value
that can be run again.

## Decision

Approved by Adam, 2026-09-04. Both Nits accepted as-is, not fixed — the
single-spawn assumption in `make_dispatch()` and the unpasted local
mocked check are both low-impact at this work item's current scope, per
the findings above.
