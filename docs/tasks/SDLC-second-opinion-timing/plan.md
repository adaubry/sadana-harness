# Plan: Automated code-quality feedback moves from the deploy stage to the build stage (from intent.md 2026-09-04)

## Context

`spec.md` is approved and already names the exact edits, in its own
"Exact edits" subsection — this plan sequences them and states what could
go wrong. No reference-corpus lookup was done for this plan: `spec.md`'s
Design section already established that `../hermes-agent` has no analog
for a documented multi-stage engineering process, so there is nothing in
`docs/reference/hermes_core_blocks_kind.csv` to consult for a prose-only
change to this project's own methodology files.

## Files that change

- `.claude/skills/build-skill/SKILL.md` (existing) — new self-check step
  in `## Phase two — implement`, plus one new `## Do not` line.
- `.claude/skills/deploy-skill/SKILL.md` (existing) — delete `## 6.
  Second opinion — the review commands` and its `### Triaging what comes
  back` subsection; renumber `## 7`–`## 10` down to `## 6`–`## 9`; trim
  the Signal-density paragraph; repurpose the `review.md` template's
  `Second opinion:` header line; remove one `## Do not` bullet.

No `src/`, no `tests/`, no new files. This work item's own artifact chain
(`intent.md`, `spec.md`, `plan.md`, `review.md`) is not itself a "file
that changes" in this sense — it's the record of this change, not part
of it.

## Order of work

1. **Edit `build-skill/SKILL.md` first** — purely additive (one new step,
   one new `## Do not` line), nothing deleted or renumbered, so it's the
   lower-risk half. Insert the self-check step between the existing "Work
   the numbered steps in order..." paragraph and "When the code is done,
   the Test stage takes over," using the three-bucket wording (`spec.md`
   §Design, "Sequencing inside Phase two"). Add the closing `## Do not`
   line about reporting work done without the self-check and `make
   verify` both having run.
2. **Edit `deploy-skill/SKILL.md`: delete, then renumber.** Remove `## 6.
   Second opinion — the review commands` and `### Triaging what comes
   back` in full. Renumber every following `## N` heading down by one
   (`## 7. Severity...` → `## 6.`, `## 8. Write review.md` → `## 7.`,
   `## 9. Stop` → `## 8.`, `## 10. After the decision` → `## 9.`).
3. **Trim the now-renumbered Severity section's Signal-density
   paragraph** — remove the clause about a second opinion in "step 6"
   raising review risk; the rest of that paragraph (about nits hiding
   real findings) stays, since it's still true of the passes that remain.
4. **Repurpose the `review.md` template's header line** inside the
   renumbered "Write review.md" section: replace
   `Second opinion: /ponytail-review, /simplify — both ran | <name> unavailable`
   with `Second opinion: none — ran during build (self-check), not
   repeated here by design.`
5. **Remove the stale `## Do not` bullet** at the end of `deploy-skill`
   ("Run `/ponytail-review` or `/simplify` before your own passes are
   written down...") — the rule it states no longer has a step to apply
   to.
6. **Re-read both files top to bottom and grep for leftover references**
   — `second opinion`, `ponytail-review`, `simplify`, `step 6`, and the
   literal old header-line text — to confirm nothing outside the edited
   spots still assumes the old shape. This is the step that catches a
   missed renumber or a dangling cross-reference; see Risks.
7. **`scripts/artifact.py check`** for this work item, and `make verify`
   as a sanity check that nothing tracked outside the two edited files
   moved (expected to still pass unchanged — this is a docs-only diff).

## Risks

**What could this change break?** Nothing at runtime — no code or tests
are touched, so `make verify`'s own suite can't be affected by this diff.
What *could* break is a future invocation of `build-skill` or
`deploy-skill` reading malformed or self-contradictory instructions: a
stale `## 8` heading no `## 7` precedes, a `## Do not` bullet pointing at
a step that no longer exists, or the `review.md` template block left
internally inconsistent. Checked: no other work item is currently
mid-build or mid-deploy (`scripts/artifact.py status` shows only this
work item active), so no concurrent use of either skill could be
disrupted mid-flight. Already-written `review.md` files (`C1` through
`C9`) are historical records of what was true when they were written —
this project's own rule against patching a closed work item's artifacts
means they are not touched, and nothing about changing the *live template*
retroactively changes what those files say.

**Which step is riskiest?** Steps 2–5, the `deploy-skill` deletion and
renumbering, taken together — this is where a heading could be missed,
a cross-reference left stale, or the Signal-density trim could remove
too much or too little. `build-skill`'s edit (step 1) is purely additive
and structurally can't leave a dangling reference, so it's landed first,
before the riskier half. Step 6 exists specifically to catch what steps
2–5 might get wrong, and runs immediately after them rather than being
folded into step 7's mechanical check, since `scripts/artifact.py check`
validates this work item's own artifacts, not the prose correctness of
the two skill files it edits.

**Which options did `spec.md` reject, and is this plan drifting back
toward one?** Checked against `spec.md` §Rejected alternatives directly:
(a) no hook-enforced gate is introduced anywhere in this plan — the new
`build-skill` step is prose, matching the existing narrow-check
precedent; (b) no new `review.md` section is added — step 4 repurposes
the existing header line in place; (c) `build-skill`'s new step uses the
three-bucket triage from `spec.md` §Design, not `deploy-skill`'s old
principle-citation/five-nit-cap apparatus; (d) the self-check is
sequenced once, at the end of Phase two (step 1's placement), not after
every numbered implementation step. None of the four re-enter this plan.

## Proof

- `git diff -- .claude/skills/build-skill/SKILL.md
  .claude/skills/deploy-skill/SKILL.md`, pasted in full, showing exactly
  the edits named above and nothing else.
- Grep evidence from step 6: no remaining match for `Second opinion`,
  `Triaging what comes back`, or `step 6` in `deploy-skill/SKILL.md`
  outside the new repurposed header line and Signal-density paragraph
  (which legitimately still say "second opinion" in their new, changed
  sense) and no duplicate or skipped `## N` heading number in either
  file.
- `scripts/artifact.py check` output for `SDLC-second-opinion-timing`,
  ending "all present artifacts valid."
- `make verify` output, ending `VERIFY OK`, pasted as confirmation this
  docs-only change left the rest of the repository exactly as it was.
