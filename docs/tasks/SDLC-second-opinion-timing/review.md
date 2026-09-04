# Review: Automated code-quality feedback moves from deploy to build (from plan.md 2026-09-04)

Reviewed: HEAD (working tree, uncommitted) — 2 files, +37/-78
Reviewer context: fresh session — this is a cold review with no memory of writing the change, per this project's own deploy-skill instruction; delegation to a subagent was not additionally used.
Second opinion: none — ran during build (self-check), not repeated here by design.

## Evidence

```
$ export PATH="$PWD/.venv/bin:$PATH" && make verify
docs/tasks/SDLC-second-opinion-timing: all present artifacts valid
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
Success: no issues found in 4 source files
TYPES OK
▶ hermetic run: TZ=UTC LANG=C.UTF-8 PYTHONHASHSEED=0, empty env
........................................................................ [ 42%]
........................................................................ [ 84%]
..........................                                               [100%]
170 passed in 1.08s
TESTS OK
VERIFY OK
```

```
$ export PATH="$PWD/.venv/bin:$PATH" && python scripts/artifact.py check
docs/tasks/SDLC-second-opinion-timing: all present artifacts valid
```

`make verify` was unaffected by this diff, as expected for a docs-only change
touching no `src/` or `tests/` — confirmed by `git diff --stat` below showing
only the two skill files touched.

## Scope

```
$ git diff --stat HEAD -- .claude/skills/build-skill/SKILL.md .claude/skills/deploy-skill/SKILL.md
 .claude/skills/build-skill/SKILL.md  | 18 +++++++
 .claude/skills/deploy-skill/SKILL.md | 97 +++++++-----------------------------
 2 files changed, 37 insertions(+), 78 deletions(-)
```

Matches `plan.md` § Files that change exactly: the same two files, nothing
else. No file the plan named went untouched, and no file outside the plan's
list was touched.

## Findings

Three passes run: Bugs, Security, Compliance (against `intent.md`, `spec.md`,
`plan.md`). No second opinion this stage, by design — see header. Bugs and
Security passes found nothing to report: this is a prose-only change to two
methodology files with no code, no tests, no secrets, no network or
subprocess surface, and no logic beyond the internal consistency of the text
itself, which the Compliance pass below covers directly.

### Important

- [Compliance] `plan.md` § Proof, item 1, promises the diff shows "exactly
  the edits named above and nothing else" (i.e., exactly plan.md's five
  enumerated `deploy-skill` edits: delete §6, renumber, trim Signal density,
  repurpose the header line, remove one `## Do not` bullet). The actual diff
  goes further: it also rewrites the two example findings inside the
  `review.md` template (deploy-skill/SKILL.md:185, :193 — the Compliance and
  "Raised, not findings" examples, which used to cite `/simplify` and
  `/ponytail-review` as the source of a candidate), trims the explanatory
  paragraphs around them (:201, :204, :208 — "a candidate that came from a
  command," "a command produced," "what the two commands returned"), and
  removes a *second* `## Do not` bullet (:250's neighbor, now gone —
  "Paste a command's raw output into `## Findings`...") that spec.md's
  "Exact edits" section never named for removal (spec.md only named "Run
  `/ponytail-review` or `/simplify` before your own passes are written
  down..." for removal). None of this is a defect — leaving those examples
  and that second bullet in place would have left `deploy-skill/SKILL.md`
  citing commands ("the two commands", "a command's raw output") that, per
  this very file, no longer run in this stage at all, which is precisely
  the "self-contradictory instructions" failure mode `plan.md` § Risks names
  as the reason step 6 (the leftover-reference grep) exists. Confirmed by
  grep: post-change, `deploy-skill/SKILL.md` contains exactly one match for
  "second opinion" (the repurposed header line) and zero matches for
  "command" anywhere else in the file — so the extra edits are internally
  consistent and correct. But `spec.md` acceptance criterion 9 ("Every other
  section of both files... is byte-identical to today except for the
  renumbering above") and `plan.md`'s own Proof item 1 both promise something
  narrower than what the diff actually does. This is exactly the kind of gap
  the deploy stage exists to surface rather than silently wave through — the
  fix looks right, but the artifact chain no longer describes the code
  without a human noting that criterion 9 was written too narrowly for what
  "no dangling reference" actually required.

### Nits

(none — the one deviation found is Important by deploy-skill's own rule that
size doesn't determine severity, so it is not downgraded here just because
the underlying text changes are small)

## Compliance pass detail

**`plan.md` § Proof, item by item:**
- Item 1 (diff pasted, showing exactly the named edits) — discharged with the
  Important finding above: the diff is pasted and matches in substance, but
  exceeds the literal enumeration.
- Item 2 (grep evidence: no remaining match for "Second opinion", "Triaging
  what comes back", or "step 6" outside the repurposed line and the
  Signal-density paragraph; no duplicate/skipped `## N`) — discharged. My own
  grep confirms: heading sequence in `deploy-skill/SKILL.md` runs `## 1`
  through `## 9` with no gap or duplicate; the only remaining "second
  opinion" text anywhere in the file is the repurposed header line at line
  158 (the Signal-density paragraph no longer contains the phrase at all,
  since the whole clause containing it was cut — a stricter outcome than
  `plan.md`'s Proof description anticipated, not a laxer one, so not itself a
  finding). Also confirmed separately: no remaining match for
  `ponytail-review` or `/simplify` anywhere in `deploy-skill/SKILL.md`.
- Item 3 (`scripts/artifact.py check` ending "all present artifacts valid")
  — discharged, pasted above.
- Item 4 (`make verify` ending `VERIFY OK`) — discharged, pasted above.

**`spec.md` § Acceptance criteria, item by item:**
1. Self-check named as an explicit step in `## Phase two — implement`,
   sequenced after the numbered steps/narrow checks and before `make
   verify` — satisfied: build-skill/SKILL.md:141-155, landing right after
   the "Work the numbered steps..." / "If the plan turns out to be wrong"
   paragraphs and right before "When the code is done, the Test stage takes
   over."
2. Three-bucket triage stated, only first bucket applied in the same diff —
   satisfied: build-skill/SKILL.md:142-152, wording matches spec.md § Design
   "Sequencing inside Phase two" closely (worth taking now / future work
   item's problem / already settled).
3. `## Do not` gains the closing line — satisfied: build-skill/SKILL.md:167-168,
   "Report the work done without having run the self-check and `make
   verify`, in that order," matching spec.md verbatim.
4. No step titled/numbered "Second opinion," no "Triaging what comes back"
   subsection — satisfied, confirmed by heading grep and full-text grep.
5. Old `## 7`-`## 10` renumbered to `## 6`-`## 9`, no gap or duplicate —
   satisfied, confirmed by heading grep (`## 1` .. `## 9` sequential).
6. `review.md` template shows the repurposed `Second opinion: none — ran
   during build (self-check), not repeated here by design.` line — satisfied:
   deploy-skill/SKILL.md:158, exact wording match to spec.md's proposed text.
7. `## Do not` no longer contains the "run before your own passes" bullet —
   satisfied (and, per the Important finding above, a second, related
   bullet was also removed beyond what spec.md named).
8. Signal-density paragraph no longer references "step 6" or a second
   opinion raising review risk — satisfied: deploy-skill/SKILL.md, the
   paragraph now reads only "Signal density is the whole product of this
   stage. A review nobody reads carefully is review theatre with extra
   steps." with no trailing clause.
9. Every other section byte-identical except renumbering — **not fully
   satisfied**, per the Important finding above: several passages beyond the
   renumbering changed (the two `review.md` template examples, three
   explanatory sentences around them, and a second `## Do not` bullet). All
   are consequential, correct fixes for dangling references to the removed
   mechanism, not unrelated edits, but criterion 9 as literally written does
   not hold.
10. `scripts/artifact.py check` passes for this work item's own artifact
    chain — satisfied, pasted above ("all present artifacts valid").

**`spec.md` § Rejected alternatives — checked against the diff directly, none
reintroduced:**
- No hook-enforced gate: build-skill's new step is prose only, matching the
  existing narrow-check precedent (build-skill/SKILL.md:141-155 — no
  `scripts/` change, no hook config touched).
- No new `review.md` section: the repurposed header line occupies the
  existing `Second opinion:` slot (deploy-skill/SKILL.md:158); no new `##`
  heading was added to the template.
- build-skill's new step does not carry deploy-skill's old heavier write-up
  apparatus: no design-principle citations, no five-nit cap language appear
  in build-skill/SKILL.md:141-155.
- The self-check runs once, after all numbered steps are done
  (build-skill/SKILL.md:141, "Once every numbered step is done"), not per
  numbered step.

**Frontmatter description** (flagged in the task briefing as a gap the build
stage found mid-implementation, not something spec.md's Acceptance criteria
enumerated): deploy-skill/SKILL.md:3 dropped "take a second opinion from the
review commands" from the skill description. Checked for consistency — the
rest of the description ("gather the verification evidence, review the
change across three passes, reconcile it against intent, spec and plan,
write review.md, and stop for a human decision") accurately describes the
file as it now reads; no other stale clause remains in the frontmatter.

**Design principles** (design-skill's five, applied to this diff):
1. Learn from the reference first — not applicable and correctly not
   attempted: spec.md § Design already established `../hermes-agent` has no
   analog for a documented multi-stage engineering process; plan.md § Context
   states the same and does no reference-corpus lookup. Nothing in this diff
   contradicts prior art because there is none to contradict.
2. Reduce the number of bets — this diff is a pure text relocation with no
   new mechanism (no hook, no script, no new file format); the underlying
   trade (dropping the independent second opinion) was already made and
   accepted in intent.md/spec.md, not newly introduced here. Clean.
3. More plugins, not more core — not applicable; no plugin infrastructure
   is touched by prose-only skill-file edits.
4. Catch the scenario at the least step-cost — satisfied: the self-check
   runs once, after all numbered steps, not per-step (matches spec.md §
   Design "Sequencing inside Phase two" and § Rejected alternatives' explicit
   rejection of a per-step run).
5. Minimise mutable state — not applicable; no state of any kind in a prose
   change to two markdown files.

## Decision

Approved by Adam, 2026-09-04 (pre-authorized in conversation: "act as if I
approved every stage, go on," given before this review ran). The Important
finding is accepted as a documented gap, not fixed: `spec.md` criterion 9
and `plan.md` Proof item 1 are left exactly as written rather than edited
to match what the diff actually did, per this project's own rule against
editing a work item's artifacts to match what the code became — the finding
itself, recorded above, is the correct place for that gap to live. The
extra edits it describes (the frontmatter description, the two stale
example findings, the extra `## Do not` bullet) are confirmed correct and
required, not scope creep.
