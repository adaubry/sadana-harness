# Review: Approval and ownership in the work-item chain (from plan.md 2026-09-14)

Reviewed: c02b6d5..working tree — 12 files, +1434/-28
Reviewer context: cold review delegated to a fresh agent with no session
context, given only CLAUDE.md, the deploy-skill policy, the three artifacts and
the diff. It ran the hook and `cmd_gate` against throwaway trees to confirm
every behavioural claim rather than reading them off the source.
Second opinion: the build-stage self-check ran first (`/ponytail-review`, then
`/simplify`'s four angles). Its findings and the cold review's are both below,
tagged, because they found different classes of thing.

## Evidence

```
docs/tasks/H15-chain-approval-ownership-plan: all present artifacts valid
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
docs/reference/ citations resolve to tracked files.......................Passed
LINT OK
Success: no issues found in 54 source files
TYPES OK
▶ hermetic run: TZ=UTC LANG=C.UTF-8 PYTHONHASHSEED=0, empty env
........................................................................ [  6%]
........................................................................ [ 13%]
........................................................................ [ 20%]
........................................................................ [ 27%]
........................................................................ [ 33%]
........................................................................ [ 40%]
........................................................................ [ 47%]
........................................................................ [ 54%]
........................................................................ [ 61%]
........................................................................ [ 67%]
........................................................................ [ 74%]
........................................................................ [ 81%]
........................................................................ [ 88%]
........................................................................ [ 95%]
.....................................................                    [100%]
1061 passed in 36.01s
TESTS OK
VERIFY OK
```

Manual proof of the three refusals, run in a throwaway tree outside the
repository (a real work item would have repointed `.claude/active-task` away
from H15 and broken `make chain`):

```
=== A. plan.md is draft ===
$ artifact.py gate write src/sadana/x.py
Blocked: …/T9-demo/plan.md is not approved.
  Status is 'draft'; the user writes 'approved' after reading it.
  Do not write that word yourself.
exit=2

=== B. plan.md approved, the path is not named ===
$ artifact.py gate write src/sadana/x.py
Blocked: …/T9-demo/plan.md does not name src/sadana/x.py under '## Files that change'.
Add it there and tell the user you did — the approval was for the old list. Or stop.
exit=2

=== C. plan.md approved, the path is named ===
$ artifact.py gate write src/sadana/listed.py
(allowed, no output)
exit=0
```

`wc -l docs/reference/console_fit_plan.md` → 266. `git diff --stat` on
`cli_shell_blueprint.md` → one hunk, §7 question 6 only.

## Findings

Eight Important, five nits. Three of the Important are security: the shell
half of the gate had two live bypasses and the Python half allowed three
path spellings it should have refused. All eight are fixed in this branch
with a regression test each; what each one was and what fixed it is below.

### Important

- **[Security] The `tests/` alternation left `tests/` writable from a shell
  command — the exact hole requirement 7 exists to close.** Adding `tests/` to
  the hook's `pathre` also widened an *unanchored* fallback that takes the
  first path-shaped token anywhere in a command whenever it sees `python` and a
  `.write(` call. That produced false positives on reads (one of the
  build-stage review agents was blocked by it mid-review), so the fallback was
  narrowed back to two prefixes — which then let a python one-liner opening a
  `tests/…` path in write mode through, while the identical command aimed at
  `src/` was refused. **Fixed:** the fallback is now destination-anchored on
  `open(…, 'w'|'a')` and carries the full prefix list, so the read case and the
  write case are distinguished by what the command actually does. Regression
  tests: `tests/integration/test_gate_hook.py` covers both directions.
- **[Security] Only the *last* write destination in a Bash command was
  gated.** `grep -oE … | tail -1` collapsed every redirect to one target, so an
  agent that put a plan-listed path last could write arbitrarily many unlisted
  files under `src/` or `tests/` in a single tool call and get exit 0. Confirmed
  by the cold reviewer against the live hook, in both orderings. **Fixed:**
  `tail -1` became `sort -u` feeding a loop that gates every destination, in
  this shell (a here-string, so `exit` still exits). Regression test:
  `test_every_destination_is_gated_not_only_the_last`.
- **[Security] Three path spellings fell through to `return 0` — allowed, not
  refused.** `./src/sadana/x.py` and an absolute path both missed
  `re.match(r"^(src|tests)/")` and reached the final `return 0`. `spec.md
  § Design` promised a leading `./` strip that only ever existed on the
  plan-entry side. The absolute case is not hypothetical: the hook hands over
  `${path#"$PWD/"}`, which stays absolute whenever `$PWD` and `getcwd()` differ
  by a symlink, and this repository has worktree symlinks. **Fixed:** `rel`
  strips `./`, and an absolute path inside the working tree is re-rooted before
  matching. This does not reopen `spec.md`'s rejection of resolving the target —
  that rejection was about a *relative* traversal turning a refusal into an
  allow; this converts an allow into a refusal. `src/../etc/passwd` is still
  refused. Regression test: `test_an_oddly_spelled_guarded_path_is_still_refused`.
- **[Bugs] The new "waiting on the user" line was unreachable through the whole
  normal chain.** It printed only in the `elif` where nothing else was
  outstanding, so with `review.md` not yet written it never appeared — and
  `next: deploy` told the agent to produce a file the gate was about to refuse.
  That is the same contradiction the `cmd_status` amendment existed to remove,
  and the first test for it pre-created `review.md`, the one arrangement where
  the bug does not fire. **Fixed:** the waiting line prints alongside `next:`.
  Regression test: `test_status_names_the_missing_approval_while_a_stage_is_still_unwritten`.
- **[Compliance] `artifact.py status` reported an unapproved chain as "all
  stages complete. Ready to commit."** Found by the build-stage self-check. The
  one command CLAUDE.md advertises as saying what is missing was structurally
  unable to name the new missing thing. **Fixed**, and `plan.md` amended: it had
  said `cmd_status` would not change.
- **[Compliance] `plan.md` told an agent to write the word `CLAUDE.md`'s new
  rule forbids.** Two lines describing what the skill templates must contain
  read `Status: approved.` where the code (correctly) writes `Status: draft.`
  The cause is worth recording: the approval gesture was a blanket
  `sed 's/Status: draft./Status: approved./'`, which rewrote *every* occurrence
  in the file, not the header line. The mechanism this work item introduces
  corrupted its own plan on first use. **Fixed** in `plan.md`; see Raised below
  for the follow-on.
- **[Compliance] `plan.md` step 8 mandated `git add -A`.**
  `docs/audits/2026-09-10/findings.md` is untracked, predates this work item and
  is not gitignored, so `-A` would have swept it into a commit the approval
  never covered — in the one place the guarded set does not reach. **Fixed:**
  staged by name; `plan.md` amended to say so.
- **[Compliance] Acceptance criterion 1 had no test.** "`spec.md` and `plan.md`
  without an `Author: … Status: …` line fail `artifact.py check`" was proven
  only by this item's own artifacts happening to carry the line. **Fixed:**
  `test_an_artifact_without_an_author_line_does_not_validate`, parametrised over
  both artifacts.

### Nits

- **[Compliance] `s.key != "deploy"` put a stage rule outside `STAGES`,** against
  requirement 12. Fixed: `Stage.needs_approval`, set `False` on deploy, read in
  both the status listing and the commit branch.
- **[Bugs] A one-segment directory claim (`src/`, `tests/`) parsed to nothing,**
  so a lane owning a top-level directory owned no files. The obvious fix was
  tried and reverted: accepting one segment made every plan that merely
  *mentions* `src/` in prose own the whole tree, which is far worse. The
  two-segment glob `src/*` already works and is now the documented form.
- **[Compliance] `console_fit_plan.md` §2 said "each is quoted verbatim",** but
  §2.4 quotes its blueprint question as it was *asked* — the same commit
  resolved it in place. Fixed with one clause, so the next reader does not chase
  a mismatch.
- **[Compliance] `plan.md` said "Eight files" over a list of nine.** Fixed.
- **[Simplification] `target.replace("\\", "/")` was computed three times in
  `cmd_gate`,** and `AUTHOR_RULE` duplicated a regex already inline in the plan
  stage while `STATUS_RE` stated the same line format a third way. Both from the
  self-check. Fixed: `rel` is hoisted once; `AUTHOR_RULE` *is* `STATUS_RE.pattern`,
  used by all three stages, so the rule that admits a header line is the
  expression that parses it.

### Raised, not findings

- **The approval `sed` is a footgun and the chain has no defence against it.**
  It corrupted this plan (above) and nothing would have caught it but a human
  reading the diff. Worth a later work item: either `artifact.py approve` that
  edits only the header line, or a check that no artifact body contains the word
  outside line 3. Not done here — it is behaviour change, and this work item's
  own spec settled that approval is a word in a file with no tooling around it.
- **The over-permissive parse fires on this very item.** `planned_paths` on
  H15's own `plan.md` yields `tests/unit/test_check_reference_citations.py` and
  `docs/tasks/*` from prose alone, so this work item is licensed to rewrite a
  test it has no business touching. Accepted in `spec.md § Concerns` as the
  deliberate trade against false refusals; recorded because it is larger in
  practice than the spec's wording suggests.
- **`.claude/active-task` is one ungated global file**, so the three-parallel-
  lanes premise in `intent.md` is not actually supported by this repository
  today — one machine, one active task. The console's worktree-per-lane plan is
  where that gets solved; nothing here depends on it yet.
- **Measured, and deliberately not optimised:** `plan.md` is read three times per
  gated write and `fnmatch` compiles cold on every invocation. The efficiency
  pass measured the whole hook at ~30 ms wall, of which `cmd_gate`'s in-process
  work is ~0.57 ms. Optimising 2% is not worth the indirection.
- **`scripts/` and the hook are outside the guarded set,** so a determined agent
  repairs or removes the gate freely, and `[ -f scripts/artifact.py ] || exit 0`
  fails open. Both are named as accepted ceilings in `intent.md § Open questions`
  and `spec.md § Concerns`. The honest framing `intent.md` records stands: this
  is a speed bump against drift, not a control against intent.

### Checked and found clean

Every `spec.md § Rejected alternatives` item was re-read against the diff and
none was reinstated: no approval fingerprint, no second approval artifact, no
per-session state, a plain `from scripts.artifact import …` rather than
`importlib`, a token scan rather than a layout parser, and the target still not
`resolve()`d for relative traversal. All 15 acceptance criteria map to a named
test or a named line of the evidence above. Every file in the diff is named in
`plan.md § Files that change`, and every file that section names is touched.
`CLAUDE.md` gained one bullet, not two. `console_fit_plan.md` carries §1–§6, the
eleven promises, the eight lettered decisions and both step tables; its 20 cited
commit shas were each resolved with `git cat-file`. The hook's nested quoting
expands correctly and `$tgt` can only begin with a guarded prefix, so no
leading-dash argument injection is reachable.

## Decision

Approved by Adam, 2026-09-14, with all eight Important findings already
fixed in this branch and a regression test behind each. The two follow-ons
recorded under `## Raised, not findings` stay open and unscheduled: an
`artifact.py approve` that edits only the header line, and the
over-permissive parse of `## Files that change`.
