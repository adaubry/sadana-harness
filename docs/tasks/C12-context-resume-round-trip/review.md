# Review: context state survives a conversation resume (from plan.md 2026-09-10)

Reviewed: working tree HEAD — 4 files, +147/-22
(`src/sadana/conversation_store.py`, `src/sadana/context.py`,
`tests/unit/test_conversation_store.py`, `CLAUDE.md`)
Reviewer context: fresh subagent, no context beyond the diff and
`intent.md`/`spec.md`/`plan.md` — genuinely cold, not the session that
wrote the code.
Second opinion: build-stage self-check (`/ponytail-review` + `/simplify`,
4 parallel angle-reviewers) already ran during build and is not repeated
here by design; this pass is independent of that one.

## Evidence

```
$ make verify
docs/tasks/C12-context-resume-round-trip: all present artifacts valid
CHAIN OK
trim trailing whitespace.................................................Passed
fix end of files.........................................................Passed
mixed line ending........................................................Passed
check for case conflicts.................................................Passed
check yaml...............................................................Passed
check toml...............................................................Passed
check json...............................................................Passed
check for merge conflicts.................................................Passed
check for added large files...............................................Passed
check that scripts with shebangs are executable...........................Passed
check that executables have shebangs.......................................Passed
detect private key..........................................................Passed
ruff.........................................................................Passed
ruff-format...................................................................Passed
shellcheck....................................................................Passed
Detect secrets................................................................Passed
docs/reference/ citations resolve to tracked files............................Passed
LINT OK
Success: no issues found in 25 source files
TYPES OK
▶ hermetic run: TZ=UTC LANG=C.UTF-8 PYTHONHASHSEED=0, empty env
........................................................................ [ 16%]
........................................................................ [ 33%]
........................................................................ [ 49%]
........................................................................ [ 66%]
........................................................................ [ 82%]
........................................................................ [ 99%]
..                                                                       [100%]
434 passed in 4.26s
TESTS OK
VERIFY OK
```

`docs/tasks/C12-context-resume-round-trip/` was `git add -N`'d before this
run (the known CONV-08 gap: `make verify`'s pre-commit `--all-files` only
lints files git already knows about) — this run genuinely covers the new
artifact files, not just the code.

## Findings

Three passes run by a fresh, cold subagent (Bugs, Security, Compliance
against `intent.md`/`spec.md`/`plan.md`), adversarially targeting the
three riskiest points in the diff by name before checking anything else.
Bugs and Security both came back with nothing to report; Compliance found
one real gap, fixed below.

### Important

- **[Compliance] `plan.md` § Files that change omitted `CLAUDE.md`,
  which the diff modifies.** Cold review caught this directly: `CLAUDE.md`
  gains one new "Please do" bullet (the persisted-column migration
  pattern this work item's design established), but `plan.md` as drafted
  named only `conversation_store.py`, `test_conversation_store.py`, and
  (added during self-check) `context.py`. The edit itself is legitimate —
  it was proposed during the design stage and approved by the user
  alongside `spec.md`, before `plan.md` was even drafted — the gap is
  purely that `plan.md`'s own Files-that-change section never caught up to
  a change that had already landed in the working tree by the time it was
  written. This is the same class of gap this project's own memory already
  names (`feedback_backfill_plan_after_self_check.md`) and asks to be
  caught before a cold review has to. **Fixed**: `plan.md` amended in
  place to list `CLAUDE.md` and explain when and why it changed, in the
  same diff as this review.

No other Important findings. Bugs and Security passes came back clean —
the reviewer specifically tried to break the three riskiest points (a
legitimately-zero `stable_prompt_len` vs. a legacy `NULL` row;
`_conversation_row()`'s positional ordering against `_CONVERSATION_COLUMNS`;
`_migrate_columns()`'s explicit `write_txn` transaction against
`open_store()`'s autocommit connection setup) and confirmed each is
handled correctly by reading the actual code, not just the diff. All other
`spec.md` Acceptance criteria and `plan.md` Proof items were checked
individually and are satisfied; no drift back toward any of `spec.md`'s
four Rejected alternatives was found.

### Nits

None. (Not padding this section — the reviewer's report had none to
report, and testing-conventions' own bar for what's worth flagging left
nothing below Important.)

## Decision

Approved by adam aubry, 2026-09-10, with the Important compliance finding
(`plan.md` missing `CLAUDE.md` from Files that change) fixed in this branch
before merge.
