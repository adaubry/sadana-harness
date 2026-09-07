# Review: The smallest complete plugin runs, visibly (from plan.md 2026-09-07)

Reviewed: HEAD..working tree (all changes uncommitted; there is no
`<base>..HEAD` range yet) — 1 file, +67/-3 (`scripts/prove_plugin_dispatch_e2e.py`)
Reviewer context: same session as build for the initial diff — but the
compliance/bugs/security review itself was **delegated to a fresh
subagent with no context beyond the diff and the three artifacts**, per
this stage's own rule about reviewing cold. Its findings were verified
independently against the actual code before being trusted (see
`## Findings`). One Important bug it found was then fixed in this same
pass, and the fix's own evidence is the final entry below.
Second opinion: none beyond the cold-review delegation above — the
build-stage self-check (`/ponytail-review`) already ran during build, per
this project's `SDLC-second-opinion-timing` decision, and is not repeated
here by design.

## Evidence

Final state, after the Important finding below was fixed:

```
$ make verify
docs/tasks/D4-smallest-plugin-runs: all present artifacts valid
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
Detect secrets...........................................................Passed
docs/reference/ citations resolve to tracked files.......................Passed
LINT OK
Success: no issues found in 11 source files
TYPES OK
▶ hermetic run: TZ=UTC LANG=C.UTF-8 PYTHONHASHSEED=0, empty env
........................................................................ [ 22%]
........................................................................ [ 45%]
........................................................................ [ 67%]
........................................................................ [ 90%]
..............................                                          [100%]
318 passed in 2.68s
TESTS OK
VERIFY OK
```

**Real-model evidence** (`scripts/prove_plugin_dispatch_e2e.py`), re-run
live after the Important finding's fix, using a real `OPENROUTER_API_KEY`
against `deepseek/deepseek-v4-flash-0731` on OpenRouter — this is the
run whose output actually exercises the shipped, per-turn-scoped capture
code, not an earlier draft:

```
discover_plugins() found: ['plugin-c']
catalog entry_tools: ['plugin_c_entry']

=== turn 1: call plugin_c_entry via the real dispatch ===
exit_reason=ExitReason.COMPLETED final_text='The note "hello from D3" was acknowledged successfully.'
[ok] turn 1 completed via a real dispatch()-built walk, not a hand-written closure
turn 1 dag_result: plugin='plugin-c' entry='plugin_c_entry' failed_node=None
  node='interpret' kind='ask' visit=0 ok=True port=None detail=None
[ok] turn 1's own DagResult confirms one ask node ran and succeeded
[ok] next_child_seq correctly advanced to 1

=== turn 2: call plugin_c_entry again, proving the tracker's reconciliation contract ===
exit_reason=ExitReason.COMPLETED final_text='The note "second call" was acknowledged.'
turn 2 dag_result: plugin='plugin-c' entry='plugin_c_entry' failed_node=None
  node='interpret' kind='ask' visit=0 ok=True port=None detail=None
[ok] turn 2's own DagResult confirms one ask node ran and succeeded
[ok] next_child_seq correctly reached 2 — no child-key collision
[ok] final transcript is well-formed: no dangling tool calls

ALL ASSERTIONS PASSED
```

Both turns show `plugin='plugin-c' entry='plugin_c_entry' failed_node=None`
and a `NodeTrace` line for the `interpret` node (`kind='ask' ok=True`) —
`intent.md`'s "what the plugin was" and "a real record of what happened"
requirements, satisfied, in the run's own printed output rather than
inferred.

## Findings

Cold review (delegated to a fresh subagent, no context beyond the diff and
`intent.md`/`spec.md`/`plan.md`) ran three passes plus the plan.md-Proof /
spec.md-Acceptance-criteria / Rejected-alternatives / files-touched /
five-design-principles compliance walk. Its Important finding was
independently re-verified against the actual source before being accepted
— confirmed real, not a false positive.

### Important

- **[Bugs, fixed]** The original diff's `captured: list[plugins.DagResult]`
  was one list shared across both turns, appended to only when
  `capturing_dispatch`'s inner `await dispatch(...)` actually succeeded.
  But `run_turn` (`src/sadana/conversation.py:844-848`) reaches
  `ExitReason.COMPLETED` without ever calling `dispatch` at all when the
  model's response carries no tool call, and separately
  (`conversation.py:886-889`) swallows a raising `dispatch` into a
  `tool_error:` result string rather than propagating it. Either path on
  turn 2 would leave `captured` un-appended-to for that turn, so
  `captured[-1]` would silently read turn 1's already-passed `DagResult` —
  the assertions would then pass on stale data instead of catching a real
  turn-2 failure, directly contradicting spec.md Requirement 7 ("never
  inferred, always the run's own structured result"). Verified directly
  against `conversation.py` before accepting this finding (both code paths
  cited above read exactly as described). **Fixed in this pass**:
  `_make_capturing_dispatch` now creates and returns a fresh
  `list[plugins.DagResult]` per call (one per turn) instead of taking a
  shared list; `_assert_single_ask_node` asserts the list is non-empty
  before indexing it, giving a clear failure
  (`"{label}: plugin_c_entry was never actually dispatched this turn"`)
  instead of silently reading a neighboring turn's data or crashing with a
  bare `IndexError`. Re-verified: `make verify` → `VERIFY OK`; a fresh real
  run → `ALL ASSERTIONS PASSED` (both pasted above). `spec.md` § Interface
  and `plan.md` § Order of work / Risks were corrected in place to
  describe the shipped, per-turn-scoped shape rather than the original
  shared-list draft.

### Nits

- The cold review's two remaining nits (a stale `IndexError`-vs-clean-message
  concern, and `spec.md`'s Interface section naming the closure/helpers
  slightly differently than the final code) are both resolved as a
  side effect of the Important fix above: `spec.md` § Interface now
  describes `_assert_single_ask_node` and `_make_capturing_dispatch`
  exactly as shipped, and the empty-list case now fails on a clear
  assertion message rather than a bare `IndexError`.

## Compliance pass detail

(from the cold review, re-verified where cited)

- **`plan.md` § Proof**: real run's stdout ending `ALL ASSERTIONS
  PASSED` with both turns' `plugin`/`entry`/`failed_node`/trace lines —
  discharged, pasted above (the post-fix run, not the pre-fix one the cold
  review saw). `make verify` ending `VERIFY OK` — discharged, pasted
  above. Both pasted into this file's `## Evidence` — discharged by this
  file existing.
- **`spec.md` § Acceptance criteria**: all eight items checked individually
  by the cold review against the diff and confirmed satisfied (capturing
  wrapper before each of the two `take_turn_and_reconcile()` calls;
  printed plugin identity + full trace; the four structural assertions per
  turn; `tests/fixtures/plugins/plugin-c` untouched — confirmed via `git
  status --porcelain`; no `src/` change — confirmed via `git diff --stat`;
  not wired into `make test`/`scripts/run_tests.sh`/any pytest file —
  confirmed via grep; `make verify` green; a real run pasted into this
  file). The capturing-wrapper and assertion criteria were re-checked
  after the Important fix and still hold, now against the corrected
  per-turn shape.
- **`spec.md` § Rejected alternatives drift check**: confirmed clean — no
  `Task`/`run_task`/`save_result` (no eval-Task drift), exactly one file
  edited rather than a new script added, no config key or disk persistence
  (no `moa_trace.py`-shaped drift). Unaffected by the Important fix, which
  only changed how the existing script's own local state is scoped.
- **Files touched vs `plan.md` § Files that change**: confirmed via `git
  diff --stat` — exactly `scripts/prove_plugin_dispatch_e2e.py`, the one
  file the plan named. `spec.md` and `plan.md` themselves were also
  corrected in this pass (see Important finding) — expected and normal for
  a design doc caught wrong by review, not a deviation from what "files
  that change" means for the code diff itself.
- **Five design principles**: reference-corpus-first (spec.md's
  `moa_trace.py` research stands, unaffected); reduce the number of bets
  (still true — no new eval Task, no new registration surface); more
  plugins not more core (still true — zero `src/` changes); least
  step-cost (still true — one file, in place, no new script); minimise
  mutable state — the cold review specifically flagged that the original
  shared `captured` list was *more* state than necessary, and correctly
  connected that to the masking bug above. The fix makes this stronger,
  not just fixes the bug: each turn's capture list is now scoped to that
  turn alone, the minimum state actually needed, rather than one
  ever-growing list read by position.

## Decision

Approved by Adam, 2026-09-07, with the Important finding (shared capture
list masking a turn-2 dispatch failure) fixed in this branch before merge,
and `spec.md`/`plan.md` corrected in place to match the shipped design.
