# Review: A conversation survives running out of room, and a bulky tool result stops crowding it out (from plan.md 2026-09-06)

Reviewed: HEAD..working tree — 10 files, +1001/-127 (includes 2 new
files, `src/sadana/result_spill.py` and `scripts/prove_context_completion.py`,
which don't show in a tracked-file diff stat the same way).

Reviewer context: same session as build. **Limitation, stated per this
skill's own instruction rather than silently skipped**: delegated to a
fresh subagent given only the diff, `intent.md`, `spec.md`, `plan.md`,
`docs/tasks/B2-cycle-contract/spec.md`, and C10's own `review.md` Decision
— no other session context — matching this project's own C10 precedent.
Its findings are reproduced below, and the two Important findings were
independently verified against the actual code (not accepted on the
reviewer's word) before being fixed.

## Evidence

```
$ make verify
docs/tasks/C11-context-completion: all present artifacts valid
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
Success: no issues found in 8 source files
TYPES OK
▶ hermetic run: TZ=UTC LANG=C.UTF-8 PYTHONHASHSEED=0, empty env
........................................................................ [ 28%]
........................................................................ [ 57%]
........................................................................ [ 86%]
.................................                                        [100%]
249 passed in 2.68s
TESTS OK
VERIFY OK
```

**Live proof, `scripts/prove_context_completion.py` against OpenRouter**
(`deepseek/deepseek-v4-flash-0731`). This is a **hybrid proof**, disclosed
plainly rather than presented as fully organic: six real, paid API calls
across two OpenRouter models (`tencent/hy-mt2-1.8b`, listed 8192-token
window; `openai/gpt-3.5-turbo-0613`, listed 4095-token window), with
OpenRouter's own `transforms` request flag explicitly disabled, all
returned `200 OK` on inputs up to 6MB with implausibly low reported
`prompt_tokens` (e.g. 559 tokens for 62,065 characters of genuinely varied
random-word content) — evidence that a request gets silently truncated
before it reaches real billing or inference, regardless of model or
provider route. A genuine provider-side "too large" rejection was not
affordably reproducible. This was surfaced to the user directly, mid-session,
before any further spend; the user chose the hybrid explicitly over
continuing to spend on more real attempts: `model_access.send` is
monkeypatched to return a simulated `NeedsContextCompression` for exactly
the first attempt in two of three phases below — every call after each
simulated trigger (the real summarization call, the real retried
completion) is genuine and unmocked. Phase 3 has no simulation anywhere.

```
=== phase 1: a simulated overflow triggers real compaction ===
exit_reason=ExitReason.COMPLETED final_text=' Hello!'
[ok] real compaction recovered the turn via a real second model call
    summary (fresh framing): 'The user asked for a short one-sentence greeting; the assistant has not yet replied. The pending task is to respond with such a greeting, following the instruction exactly.'

=== phase 2: a second simulated overflow refines the existing summary ===
exit_reason=ExitReason.COMPLETED final_text='I'm ready for your next request.'
[ok] the real second summarization call was asked to refine, not resummarize from scratch
    summary (refined): 'The earlier pending greeting was completed with "Hello!"; the current pending task is to reply to the user's follow-up request with a different short sentence.'

=== phase 3: a real oversized tool result spills to a real file ===
exit_reason=ExitReason.COMPLETED final_text='done'
[ok] real oversized result spilled to a real file: /home/adam/.local/state/sadana/tool_results/call_a607314951c4404598e5360c.txt

ALL ASSERTIONS PASSED
```

## Findings

**Compliance verification performed:** every `plan.md § Proof` item traced
to what discharges it; every `spec.md § Acceptance criteria` item matched
to the file/test that satisfies it, with the hybrid-proof question
(below) given a real, skeptical answer rather than a pass-through of the
build session's own framing; `spec.md § Rejected alternatives` checked
against the diff for drift (none found on any of the six entries);
`plan.md § Files that change` cross-checked against the actual diff (two
omissions found and fixed — see Nits); the five design principles checked
explicitly (summarized at the end of this section); C10's own approved
Decision condition checked against the actual code, not just its mention
in a docstring. Delegated to a fresh subagent per this stage's own
instruction; both Important findings were independently reproduced
against the real code before being trusted, then fixed in this same pass.

### Important

- **[Bugs] `result_spill.write_and_reference` could still raise, breaking
  its own "never raises" contract.** `_spill_path()` (and the directory-
  creating `mkdir` inside it) ran *before* the function's `try:` block —
  an `OSError` from creating `tool_results/` itself (permission denied, a
  stray non-directory file already occupying that path) would propagate
  uncaught straight through `context.after_tool_result` and `run_turn`'s
  TOOL_ROUND, crashing the whole turn instead of falling back to the
  caller's own truncation as spec.md requires. The existing test only
  simulated a `write_text` failure, never a `mkdir` failure, so this had
  no coverage. **Fixed**: the whole path computation now runs inside the
  guarded region; a new test (`test_write_and_reference_falls_back_to_content_when_the_directory_cant_be_made`)
  proves the `mkdir`-failure path falls back the same way the write-failure
  path already did.
- **[Bugs] `model_access.resolve()` silently weakened mid-retry
  cancellation.** The original refactor wrapped the *entire* bounded
  retry sequence in one `asyncio.to_thread` call, instead of one call per
  attempt (which is what `complete()`'s own pre-existing loop did). Since
  a thread already dispatched to a worker can't be stopped once running,
  a cancellation arriving between attempt 1 and attempt 2 of up to
  `SADANA_MODEL_ACCESS_MAX_RETRIES` (default 3) real network round trips
  would previously have been unobservable until the *whole* sequence
  finished — contradicting CLAUDE.md's own "say exactly what each async
  operation survives" principle, and not caught by the existing suite
  because every test mocks `send` synchronously with no real inter-attempt
  delay to expose the difference. **Fixed**: `resolve()` is now `async def`
  and awaits `asyncio.to_thread(send, ...)` once per attempt, restoring
  the original cancellation-observability property while keeping the
  retry loop centralized in MODEL-ACCESS (both callers now `await
  model_access.resolve(request)` directly). A new test
  (`test_resolve_is_cancellable_between_attempts`) proves no further
  attempt is ever dispatched after cancellation, not just that the
  coroutine eventually raises `CancelledError` — a weaker property a
  single giant `to_thread` call would also have satisfied without
  actually stopping.

### Nits

- **[Compliance] `plan.md`'s "Files that change" omitted `CLAUDE.md` and
  `scripts/prove_context_completion.py`** — the same class of omission
  C10's own review flagged, recurring rather than a first occurrence.
  Fixed in this same pass.
- **[Security] The spilled-file reference note embeds an absolute local
  path** (containing the OS username, e.g. `/home/<user>/...`) that later
  rides into a live prompt sent to a third-party model provider, on any
  later compaction or completion that includes it. This is the design as
  spec'd (the reference has to name a real path to be useful), not an
  oversight — but worth a second look if this project ever runs anywhere
  but a single trusted developer's own machine, since it's a small,
  real environment-detail leak to an external service. Accepted, not
  fixed, for a single-developer, phase-1 project.
- **[Bugs] `find_compaction_boundary` doesn't guard a negative
  `keep_tail_count`** (e.g. a misconfigured `SADANA_CONTEXT_COMPACTION_TAIL_MESSAGES`):
  `index = max(0, len(messages) - keep_tail_count)` can then exceed
  `len(messages)`, and the bounds guard skips the backward-walk entirely,
  returning an out-of-range index. It doesn't crash today only because
  Python's slice semantics tolerate an out-of-range start, not because
  the function actually guards against it. Accepted, not fixed — no
  config validation exists elsewhere in this codebase for a value that
  can only be wrong through direct misconfiguration, and the failure mode
  if it ever happened is "compacts everything" (fail-safe direction),
  not data loss or a crash.

Bugs pass otherwise clean; Security pass otherwise clean (the
`_spill_path` sanitization — an allow-list of `[A-Za-z0-9_.-]`, falling
back to a fixed name when emptied out — was checked directly for path
traversal and cannot produce `..` or an absolute-path escape, since every
character outside the allow-list is replaced, never passed through).

### Compliance checklist

- Proof item "`make test` after step 1 (`test_model_access.py` green in
  isolation)": discharged by the full green suite; `test_resolve_*` and
  the index-0 fix tests are part of it, plus the two new tests added
  during this review's own fix pass.
- Proof item "`make test` after step 2 (`test_result_spill.py` green)":
  discharged, including the new `mkdir`-failure regression test.
- Proof item "`make test` after step 3 (`find_compaction_boundary`/`compact`
  tests)": discharged — present and green.
- Proof item "`make test` after step 4 (`complete()`'s existing tests
  unchanged)": discharged — no hunk touches those tests; they pass
  unmodified.
- Proof item "`make test` after step 5 (`test_context.py` green)":
  discharged.
- Proof item "`make verify` after step 6 (full suite)": discharged —
  `VERIFY OK`, 249 passed (247 at first green, +2 from this review's own
  fixes).
- Proof item "Self-check output reported verbatim": discharged in this
  session's own record (the `/ponytail-review` + 4-angle `/simplify` pass
  that caught the `_append_invalid_results` spilling gap during build);
  summarized rather than reproduced verbatim here, matching this
  project's own precedent for a large diff's self-check trace.
- Proof item "Live round trip": discharged, with the hybrid caveat below.
- Acceptance criterion "A live round trip shows a real `ContextOverflow`
  recovered": **satisfied with a real, disclosed caveat, not the literal
  wording**. The recovery mechanics are genuinely proven end to end (a
  real second model call, a real retry, exactly one `is_summary=True`
  message). The trigger itself — the actual `ContextOverflow` — is
  simulated, not a genuine provider rejection. The user's sign-off
  legitimizes *how* the evidence was gathered given a real, checked
  affordability constraint; it does not make spec.md's own words ("a real
  ContextOverflow") literally true. Recorded here so the gap is visible
  to whoever reads this later, not smoothed over.
- Acceptance criterion "A second, later compaction... refines the prior
  summary": satisfied for the refine mechanics themselves (the real
  second prompt provably contains the first summary's own text and
  explicit refine framing) — same simulated-trigger caveat applies to how
  the second overflow is reached, not to the refine behavior once there.
- Acceptance criterion "`find_compaction_boundary` never orphans a
  tool-role message": satisfied at `tests/unit/test_conversation.py`
  (`test_find_compaction_boundary_never_orphans_a_tool_row`).
- Acceptance criterion "A live round trip shows a real oversized tool
  result spilled to an actual file": satisfied, fully real, no
  simulation anywhere in this path — verified byte-for-byte.
- Acceptance criterion "`complete()`'s existing test suite passes
  unchanged": satisfied.
- Acceptance criterion "The `mark_cache_boundary` index-0 `# ponytail:`
  comment is gone, replaced by locating the system message by role":
  satisfied at `src/sadana/model_access.py` (`_system_message_index`),
  used by both `mark_cache_boundary` and `_eligible_trailing_indexes` —
  also directly satisfies C10's own approved Decision condition.
- Acceptance criterion "`make verify` ends `VERIFY OK`": satisfied.
- Rejected alternatives (all six named in spec.md): no drift found on any
  — collapsing `turn_complete`'s return to history-only, deleting
  `rotate_prompt()`, a static non-LLM fallback summary, `turn_complete`
  constructing its own `Message`, content-sniffing for a summary marker,
  duplicating `complete()`'s retry loop.
- Files-changed match: discrepancy found and fixed (see Nits) —
  `CLAUDE.md` and `scripts/prove_context_completion.py` were in the diff
  but absent from `plan.md`'s list.
- Design principles: all five checked. Learn from reference first —
  honored, spec.md's own reference-corpus section cites specific hermes
  file/line ranges before any design decision. Reduce the number of bets
  — honored, multiple hermes mechanisms explicitly declined with the
  specific cost named. More plugins not more core, only where the seam
  exists — honored, `result_spill.py` fills an already-declared B2
  checkpoint rather than inventing new abstraction. Catch the scenario at
  the least step-cost — honored overall (the `_append_invalid_results`
  gap was caught and fixed within the same build pass rather than
  shipping it), though this review's own two Important findings show a
  self-check pass doesn't catch everything — the cold review existing as
  a second, independent look is exactly why. Minimize mutable state —
  honored, `TurnCompleteResult`/`ContextState` stay frozen, `compact()`/
  `find_compaction_boundary` are pure.

## Decision

Approved by Adam, 2026-09-07. The hybrid-proof gap this review raised
explicitly (spec.md's acceptance criteria said "a real `ContextOverflow`,"
the trigger was simulated) is resolved by amending those two criteria in
`spec.md` in place to record what was actually decided and why — the real
cost evidence that made a genuine provider-side rejection unaffordable,
and the disclosed hybrid the user chose instead — rather than leaving
wording that overclaims what was proven. Both Important findings (the
`result_spill` `mkdir`-outside-`try` gap, the `resolve()` cancellation
regression) were fixed in this same pass, each with its own regression
test, before this approval.
