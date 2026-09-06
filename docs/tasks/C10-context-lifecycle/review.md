# Review: A conversation can compact its history and mark what's safe to reuse (from plan.md 2026-09-06)

Reviewed: HEAD..working tree — 12 files, +406/-61 (includes 2 new files,
`src/sadana/context.py` at 120 lines and `tests/unit/test_context.py` at
104 lines, which don't show in a tracked-file diff).

Reviewer context: same session as build. **Limitation, stated per this
skill's own instruction rather than silently skipped**: the pass below was
not run by a fresh reviewer with no memory of writing the code. To buy back
the separation this stage exists for, the three-pass review (Bugs,
Security, Compliance) was delegated to a fresh subagent given only the
diff, `intent.md`, `spec.md`, `plan.md`, and `docs/tasks/B2-cycle-contract/spec.md`
— no other session context. Its findings are below, verified independently
before being trusted (the Important finding was reproduced against the
actual file, then fixed and re-verified with a real run, not just accepted
on the reviewer's word).

Second opinion: the delegated cold review above stands in as the "fresh
eyes" this stage asks for; no separate second opinion was sought beyond it.

## Evidence

```
$ make verify
docs/tasks/C10-context-lifecycle: all present artifacts valid
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
Success: no issues found in 7 source files
TYPES OK
▶ hermetic run: TZ=UTC LANG=C.UTF-8 PYTHONHASHSEED=0, empty env
........................................................................ [ 33%]
........................................................................ [ 67%]
.....................................................................    [100%]
213 passed in 2.07s
TESTS OK
VERIFY OK
```

**Live round trip, `scripts/prove_conversation_e2e.py` against OpenRouter**
(`deepseek/deepseek-v4-flash-0731`) — this item's own acceptance criterion
(a real request body carrying `cache_control`) required extending this
script with a small, permanent instrumentation addition (a `model_access.send`
wrap capturing the real request for turn 1 only), since the script's
pre-existing assertions predate CONTEXT and never checked this:

```
initial prompt_sha256=b61311c5dc915d9649f8833e2e5e9da709c1cd893ddd29a5104290962a1149db

=== turn 1: plain exchange, no plugin ===
exit_reason=ExitReason.COMPLETED final_text='Hello!'
[ok] turn 1 completed; prompt_sha256 unchanged
    turn 1 real request system message content: [{'type': 'text', 'text': "You are a plainly-behaved assistant used only by sadana-harness's own CONV-09 proof script. Follow instructions exactly and literally.", 'cache_control': {'type': 'ephemeral'}}, {'type': 'text', 'text': "\n\nThis is a proof-of-concept conversation for sadana-harness's CONV-09 evidence.\n\nplugin-a: Runs a small three-step flow via a focused helper. (start with plugin_a_entry)\nplugin-b: The simplest possible flow: one helper, one report. (start with plugin_b_entry)"}]
[ok] turn 1's real request body carries a cache_control marker on the system message

=== turn 2: plugin-a ===
    [plugin-a] child key=conv09-proof/run-1/child/plugin_a_child/0 exit=ExitReason.COMPLETED final_text='The incoming webhook payload contains a ping event, indicating a connectivity check. ACKNOWLEDGED'
exit_reason=ExitReason.COMPLETED final_text='plugin_a_entry ran successfully: the webhook payload was a ping event (connectivity check) and it was acknowledged.'
[ok] turn 2 completed; prompt_sha256 unchanged; child isolation verified above

=== turn 3: plugin-b ===
    [plugin-b] child key=conv09-proof/run-1/child/plugin_b_child/1 exit=ExitReason.COMPLETED final_text='Hello — acknowledged from turn 3.'
exit_reason=ExitReason.COMPLETED final_text='plugin_b_entry ran successfully with the note "hello from turn 3", which was acknowledged.'
[ok] turn 3 completed; prompt_sha256 unchanged
iteration_budget after turn 3: 5/5

=== turn 4: forced budget exhaustion ===
exit_reason=ExitReason.BUDGET_EXHAUSTED detail='iteration budget exhausted (5)'
[ok] turn 4 exhausted the budget as intended
[ok] final transcript is well-formed: no dangling tool calls

ALL ASSERTIONS PASSED
```

Note the real, correct behavior visible above: the system message splits
into two content parts exactly at the template's `stable_prompt` boundary
— the marked part is the fixed persona text alone, and the per-conversation
context tier (this conversation's own `system_message` plus the rendered
plugin catalog) rides unmarked, exactly as designed (a different
conversation built from the same template would still hit the same cached
prefix).

**Live re-run of `scripts/eval/tasks/plugin_dispatch.py`** (EVAL-02's own
real task), after the Important finding below was fixed:

```
task_id=plugin_dispatch
exit_reason=ExitReason.COMPLETED
final_text='The plugin_a_entry tool ran successfully.\n\n**Result:** The incoming webhook payload indicated a ping event, and the flow acknowledged it — returning `ACKNOWLEDGED`.'
score=1.0
[ok] plugin dispatch verified end to end
[ok] result saved to /home/adam/.local/state/sadana/eval/results/plugin_dispatch__0.json

ALL ASSERTIONS PASSED
```

## Findings

**Compliance verification performed:** every `plan.md § Proof` item traced
to what discharges it (a specific test, or the pasted live-run evidence
above); every `spec.md § Acceptance criteria` item matched to the file and
test that satisfies it; `spec.md § Rejected alternatives` checked against
the actual diff for drift (none found on any of the six entries); the
diff's file list cross-checked against `plan.md § Files that change`
(two omissions found and fixed, see Nits); the five design principles
checked explicitly (summarized at the end of this section). Delegated to
a fresh subagent with no context beyond the diff and the three artifacts,
per this stage's own instruction to buy back the self-review gap; its
findings are reproduced and were independently verified below, not merely
relayed.

### Important

- **[Bugs/Compliance] `scripts/eval/tasks/plugin_dispatch.py` still called
  `run_child(..., compress=_compress_noop, ...)`** — EVAL-02's own real,
  keep-forever eval task (closed in commit `a698311`) was never migrated
  off the removed `compress` parameter. Since `run_child()`'s signature no
  longer accepts it, running this script would have raised `TypeError:
  run_child() got an unexpected keyword argument 'compress'` before any
  model call — a silent break in a closed work item's own real code path,
  invisible to `make verify` (`mypy` runs on `src/` only, not `scripts/`,
  and this file has no pytest coverage — it's a manual,
  `OPENROUTER_API_KEY`-gated script). Root cause: the original
  caller-migration search during build was scoped to `eval_harness.py`,
  `prove_conversation_e2e.py`, and the two test files — it never searched
  `scripts/eval/tasks/`, which calls `run_child()` directly rather than
  through `eval_harness.run_task()`. **Fixed in this same pass**: dropped
  `_compress_noop` and the `compress=` argument, matching every other
  caller; re-verified with a real run against OpenRouter (`exit_reason=
  COMPLETED score=1.0`, pasted above), not just a mocked test. A
  repo-wide grep afterward (`compress=`, and every `run_child(`/`run_turn(`/
  `take_turn(` call site) confirmed no other caller was missed.

### Nits

- **[Compliance] `plan.md`'s "Files that change" section was stale** —
  it still described a `BeforeSendResult` wrapper and a `before_send(...,
  context_window_size=...)` signature, both superseded by two in-build
  corrections that spec.md documents but plan.md never picked up. Fixed in
  this same pass (plan.md now matches the actual code and cites spec.md's
  own correction notes).
- **[Compliance] `CLAUDE.md` was touched by this diff but absent from
  plan.md's file list** — justified by spec.md's own Concerns section
  (flagged there as a candidate rule needing approval before build), but
  the omission meant a reader following only plan.md would miss it. Added
  in this same pass.
- **[Bugs] `after_tool_result`'s call site had no dedicated regression
  test** — unlike `turn_complete` (proven twice: the unmocked
  `CONTEXT_OVERFLOW_UNHANDLED` test and the monkeypatched retry test), a
  silent revert of the TOOL_ROUND append back to a plain `Message(...)`
  construction would not have failed any existing test. Fixed in this same
  pass: `test_run_turn_calls_after_tool_result_before_appending` in
  `tests/unit/test_conversation.py` monkeypatches `context.after_tool_result`
  and asserts its return value is what actually gets appended.
- **[Bugs] `mark_cache_boundary`/`_eligible_trailing_indexes` both assume
  the system message is always at index 0** — true today because
  `complete()` always prepends it there, but nothing in `model_access.py`
  checks or enforces that assumption; a system message elsewhere in the
  list would silently get no stable-prefix marker, and its slot would
  stay eligible for a trailing mark instead. **Accepted, not fixed** —
  the one real caller (`complete()`) already guarantees the invariant, and
  adding a defensive check for a shape that structurally cannot occur
  given today's single caller would be validating a scenario that can't
  happen (CLAUDE.md's own "do not" on this). Worth a look if a second
  caller of `mark_cache_boundary` ever builds its own message list.

Bugs and Security passes otherwise came back clean (delegated review's
full write-up, including the full compliance checklist against every
`plan.md` Proof item, every `spec.md` Acceptance criterion, and every
`spec.md` Rejected alternative, is preserved in this session's transcript;
summarized above rather than reproduced in full here since none of it
surfaced anything beyond what's listed).

Design principles: all five checked and honored — reference corpus read
substantively before designing (spec.md's own "What the reference corpus
showed"); number of bets reduced twice via in-place correction rather than
carrying a wrong design forward (dropped `context_window_size`, collapsed
`BeforeSendResult`); no premature plugin/strategy abstraction added
(`SADANA_CONTEXT_CACHE_TRAILING_MARKS` is the entire adjustable surface);
the guideline-2-vs-guideline-3 tension (wide diff across four already-merged
blocks vs. a narrower repoint-only alternative) is explicitly argued in
spec.md's own Concerns, and the missed `plugin_dispatch.py` caller above is
a real, if minor, cost of that wide-diff choice landing incompletely on
the first pass — now closed; mutable state minimized throughout
(`ContextState`/`CacheHint` are frozen, threaded by `replace()`,
`mark_cache_boundary` never mutates its input, tested directly).

## Decision

Approved by Adam, 2026-09-06, with one condition: the accepted Nit above
(`mark_cache_boundary`/`_eligible_trailing_indexes` assuming the system
message is always at index 0) must be fixed in whichever future work item
closes the CONTEXT block — not deferred indefinitely. Marked in the code
itself with a `# ponytail:` comment at its exact location
(`src/sadana/model_access.py`, in `mark_cache_boundary`, right before the
`system = result[0]` line) naming the concrete failure mode (a silent
caching regression: the stable-prefix marker never lands, and the real
system message becomes eligible for a trailing mark instead) and the
upgrade path (locate the system message by role, not index, once a second
caller exists) — so this obligation survives independently of this
review.md being read again.
