# Review: A conversation's history that cannot become malformed (from plan.md 2026-09-03)

Reviewed: f81d52b..HEAD (working tree) — 6 files, +751/-0
Reviewer context: same session as build — cold-read requirement satisfied by
delegating the actual review passes (steps 1-5 below) to a fresh subagent
with no prior context beyond intent.md/spec.md/plan.md and the diff; this
session performed the file-list scoping, the second-opinion commands, and
the write-up.
Second opinion: /ponytail-review and /simplify — both run, after the cold
review's findings were already written down.

## Evidence

```
docs/tasks/C2-keys-transcript/intent.md: note — reads like design, not intent — module names and code belong in spec.md
docs/tasks/C2-keys-transcript: all present artifacts valid
CHAIN OK
trim trailing whitespace.................................................Passed
fix end of files.........................................................Passed
mixed line ending.........................................................Passed
check for case conflicts..................................................Passed
check yaml.................................................................Passed
check toml.................................................................Passed
check json.................................................................Passed
check for merge conflicts..................................................Passed
check for added large files................................................Passed
check that scripts with shebangs are executable............................Passed
check that executables have shebangs........................................Passed
detect private key..........................................................Passed
ruff.........................................................................Passed
ruff-format...................................................................Passed
shellcheck.....................................................................Passed
Detect secrets.................................................................Passed
LINT OK
Success: no issues found in 4 source files
TYPES OK
▶ hermetic run: TZ=UTC LANG=C.UTF-8 PYTHONHASHSEED=0, empty env
...................................................................      [100%]
67 passed in 0.24s
TESTS OK
VERIFY OK
```

## Findings

Three Important findings, all Compliance — a plan.md/CLAUDE.md file-list gap,
one undischarged Proof case, and one Proof promise (identity-checked
mutation tests) delivered as value-equality instead. One Nit, confirmed by
both second-opinion commands. Everything else — the bugs pass, the security
pass, the Rejected-alternatives re-check, and the five design principles —
was checked and found clean, detailed below.

### Important

- [Compliance] `CLAUDE.md` was modified (the "conversation.append/repair"
  amendment made during Design) but `plan.md` § Files that change lists
  only `src/sadana/conversation.py` and `tests/unit/test_conversation.py`.
  The change is defensible on its merits — it documents the invariant this
  work item establishes, and was approved in-conversation at Design time —
  but `plan.md` cannot be trusted as a complete file list for this commit
  as written, and the artifact chain has no record of *why* it's missing.
- [Compliance] `plan.md` § Proof promises `append` raises for an assistant
  message appended while pending, "with or without new tool_calls."
  `tests/unit/test_conversation.py::test_append_raises_for_assistant_message_while_pending`
  only exercises the "without" case (`tool_calls` defaults to `()`). The
  code path is generic enough that the "with" case almost certainly also
  raises (the check at `conversation.py:99` is `message.role != "tool"`,
  independent of `tool_calls`), but nothing in the diff proves it.
- [Compliance] `plan.md` § Proof promises append/repair's no-mutation
  property is "asserted by identity/length on the original argument," not
  by re-deriving it.
  `test_append_does_not_mutate_input_on_success_or_raise` and
  `test_repair_does_not_mutate_input`
  (`tests/unit/test_conversation.py:118-136`) instead assert `messages ==`
  a freshly reconstructed value. `test_repair_does_not_mutate_input` in
  particular snapshots `original = messages` and later asserts
  `messages == original` — since `messages` is never reassigned in that
  test, this compares the object to itself and proves nothing; the only
  real assertion in it is `repaired is not messages`. Low practical risk
  (tuples and frozen dataclasses are language-level immutable, so an actual
  mutation bug is not plausible here), but it is a specific promise
  `plan.md` made and the delivered tests don't match it.

### Nits

- [Simplification/Efficiency, confirmed by both `/ponytail-review` and
  `/simplify`] `pending_tool_call_ids` (`conversation.py:71-76`) and
  `repair` (`conversation.py:118-123`) each inline the same reverse scan
  for "the last assistant message with non-empty `tool_calls`." One
  `_last_call_message(messages)` helper called from both would remove the
  duplication — roughly 6 lines net. Not applied here; listed for the
  decision below.

### Checked and found clean

- **Bugs pass**: every scenario named for this kind of scanning logic —
  empty `tool_calls=()` tuples, falsy/empty-string ids, multiple
  assistant-with-tool_calls messages in history, an id reused across two
  separate tool rounds (exercised directly by
  `test_two_sequential_tool_rounds_reusing_id_are_paired_independently`),
  and "assistant-with-tool_calls appended while already pending"
  (structurally unreachable through `append`'s own public API) — checked
  and clean.
- **Duplicate ids within one assistant message's `tool_calls`** collapse in
  the set-based `pending`/`declared` computation (`conversation.py:80`),
  which could in principle let a second identically-id'd call go unanswered
  while `append` treats the round as closed. Not a finding: `spec.md`
  ("What this module trusts and does not re-check," lines 90-97)
  explicitly assigns id-uniqueness-within-one-message to MODEL-ACCESS's
  boundary responsibility, not this module's. Confirmed the code matches
  that stated assumption rather than silently drifting from it.
- **Security pass**: pure in-memory dataclasses and set/tuple arithmetic —
  no filesystem, network, subprocess, `eval`/`exec`, or dynamic import.
  `TranscriptInvariantError` messages never include `message.content`, so
  no content/secret leakage through error text. Clean.
- **`spec.md` § Rejected alternatives re-check**: none of the four
  (9-file package, second `ToolCall` type, stored `msg_seq` counter,
  stateful `Transcript` protocol/class) crept back into the diff — checked
  against the actual code, not just re-read from the spec.
- **Design principles**: reference-corpus learning (the code matches what
  `spec.md` documented it audited and built, not a port of hermes's
  `close_interrupted_tool_sequence`), reducing bets / minimizing mutable
  state (no module-level mutable state beyond the `_REPAIR_MARKER`
  constant; every function is pure), reject-at-append over a separate
  validator (the only validation path is inside `append`, no sanitizer
  function exists elsewhere) — all clean. More-plugins-not-more-core is
  N/A; no plugin surface exists yet.
- **`spec.md` § Acceptance criteria**: all 9 checkboxes are discharged by
  name-matched tests in `tests/unit/test_conversation.py` (the pairing,
  role-alternation, repair-no-op, repair-appends, and no-I/O criteria) —
  the criterion-7 wording gap is the Important finding above, not a
  missing test.

## Decision

Approved by Adam, 2026-09-03, as-is. The three Important findings are
process/documentation gaps — plan.md undersold its own promised test
coverage (one branch of the pending-assistant case untested, mutation
safety asserted by value-equality instead of the promised identity check)
and CLAUDE.md's amendment was never added to plan.md's file list — not
correctness defects; `make verify` stayed green throughout. Ship, no fixes
requested.
