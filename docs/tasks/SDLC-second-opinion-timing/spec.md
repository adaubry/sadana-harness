# Spec: Automated code-quality feedback moves from the deploy stage to the build stage

Intent: docs/tasks/SDLC-second-opinion-timing/intent.md

## Requirements

1. `build-skill`'s Phase two gains a step, run once every numbered
   implementation step is complete and before the work is reported done:
   run `/ponytail-review` and `/simplify` against the finished diff, act
   on what's worth taking now, then run `make verify`. (Intent §Proposed
   outcome.)
2. The judgment calls that decided what a candidate suggestion was worth
   in `deploy-skill`'s old step 6 — take it now, defer it to a future
   `intent.md`, or note that `spec.md` already settled it — move with the
   check, not just the two command invocations. (Intent §Constraints:
   "only the automated second-opinion mechanism moves," not just its
   trigger.)
3. `deploy-skill` loses step 6 (`Second opinion — the review commands`)
   and its `### Triaging what comes back` subsection entirely, and every
   place that assumed it existed — the `## Do not` bullet forbidding an
   early second opinion, the `review.md` template's `Second opinion:`
   header field, the `Signal density` remark about the second opinion
   raising review risk. (Intent §Proposed outcome, §Constraints.)
4. `deploy-skill`'s cold review, its findings-only/no-approval rule, and
   its Bugs/Security/Compliance passes are untouched. (Intent
   §Constraints, explicit.)
5. A reviewer reading a `review.md` produced after this change can tell,
   without asking anyone, that the missing second-opinion section is
   deliberate — not a step someone forgot to run. (Intent §Constraints,
   the legibility requirement.)
6. Nothing about an already-closed work item's artifacts changes. (Intent
   §Affected users and systems, explicit; also this project's standing
   rule against patching a closed work item.)
7. The design stage resolves both of `intent.md`'s open questions rather
   than leaving them for later: whether the build-stage check is a hard
   gate or discipline-level guidance, and exactly how the deploy record
   states the trade-off.

## Design

This work item edits two files' prose — `.claude/skills/build-skill/SKILL.md`
and `.claude/skills/deploy-skill/SKILL.md` — and nothing else. No `src/`,
no `tests/`, no runtime behavior. `reference-lookup` guideline 1 ("learn
from the reference before proposing") doesn't apply in the usual sense:
`../hermes-agent` is a code reference corpus for the CONVERSATION block
and its siblings, not a documented multi-stage engineering process —
there is no hermes analog for "when does automated review feedback run
relative to verification." The only prior art worth learning from is this
project's own existing text, which is why requirement 2 exists: port the
triage judgment calls that already work, rather than re-deriving them.

### Where the check moves to, and why it's leaner there

`deploy-skill`'s old step 6 did two jobs at once: decide what a candidate
was worth, and decide how to *write it up* for a human reading `review.md`
(a Compliance finding citing one of deploy-skill's five numbered design
principles, a Nit inside a cap of five, or a note under "Raised, not
findings"). Only the first job — decide what a candidate is worth — moves
to `build-skill`. The write-up apparatus doesn't, because `build-skill`
isn't producing a document for someone else to read a decision off of; it
can just act. So the three real buckets become:

- **Worth taking now** — it removes a bet, drops mutable state, or moves
  a step off the common path (the same substance `deploy-skill` graded as
  Compliance-worthy, without needing to cite which of its five numbered
  principles by number). Apply it in this same diff.
- **A future work item's problem** — it would change behavior. Note it in
  a sentence in the build-stage summary reported to the requester; don't
  act on it now, and don't let it block or expand this diff.
- **Already settled** — it contradicts something `spec.md` §Rejected
  alternatives already decided. One line noting the spec held, move on.

Dropped entirely: the "Nit, capped at five" category and its paired
"cap them or a review with thirty nits hides the two real findings"
reasoning. That reasoning is about *reading fatigue in a document* — it
doesn't transfer to a builder deciding what to fix in its own diff. The
existing project-wide discipline against unrequested scope (no
unrequested abstractions, no drive-by cleanup beyond what the task needs)
already covers the same ground for this context, so no new cap needed.

### Sequencing inside Phase two

The new step lands after `build-skill`'s existing numbered steps and
their per-step narrow checks, and before the plain-prose "run
`make verify`, paste the result" expectation this project already holds
every work item to (CLAUDE.md: "Run `make verify` before reporting any
task complete"). Concretely, in `build-skill`'s own wording style:

> Once every numbered step is done: run `/ponytail-review` and
> `/simplify` against your own diff. Sort what comes back — worth taking
> now, a future work item's problem, or something `spec.md` already
> settled — and act on the first bucket in this same diff. Then run
> `make verify` and report both: what the self-check found and did, and
> the verify output.

This makes the self-check strictly *before* verification in every case,
which is requirement 1 and directly answers `intent.md`'s Problem: a
revision the self-check prompts is baked in before `make verify` ever
runs, so `make verify` never needs to be re-run because of it.

### Resolving open question 1: hard gate, or discipline?

**Discipline-level, matching the existing narrow-check precedent** —
not a new hook-enforced gate. Two reasons, not one:

1. **No clean, honest place to enforce it.** This project's chain gate
   enforces artifact *existence and section content* (`scripts/artifact.py`
   checks that `plan.md` has a non-empty `## Proof`, etc.), and `plan.md`
   is written and approved *before* Phase two's implementation — including
   before this new step ever runs. There's nothing to check a box against
   until after the fact, and `plan.md` is not a living document any
   existing work item re-opens post-implementation (checked: `C9`'s
   `plan.md` was written once, in Phase one, and never touched again).
   Inventing a mechanism to re-validate it afterward would be new
   supporting infrastructure with exactly one caller — the failure mode
   guideline 2 already warns against.
2. **The precedent already made this call.** `build-skill`'s existing
   "run the narrow check for what you touched" is the same shape of
   instruction — a required step with no mechanical enforcement, trusted
   the same way the rest of Phase two is. Adding a hook for only the new
   step, while its immediate neighbor stays unenforced, would be an
   inconsistent, arbitrary line to draw.

### Resolving open question 2: how the deploy record shows the trade-off

**Repurpose the existing header line rather than add a new section.**
`review.md`'s header currently carries a
`Second opinion: /ponytail-review, /simplify — both ran | <name> unavailable`
line. It keeps occupying the same slot, with new text:

> `Second opinion: none — ran during build (self-check), not repeated here by design.`

A reader who has seen this project's review records before will notice
the line changed shape, in the exact place they'd look for it, without
needing new structure to explain what happened. Considered and rejected:
a new dedicated section (e.g. `## Build-stage self-check`) — see
Rejected alternatives.

### Exact edits

`.claude/skills/build-skill/SKILL.md`:
- `## Phase two — implement`: insert the new step (above) between the
  existing "Work the numbered steps..." paragraph and "When the code is
  done, the Test stage takes over."
- `## Do not`: add one line — "Report the work done without having run
  the self-check and `make verify`, in that order."

`.claude/skills/deploy-skill/SKILL.md`:
- Delete `## 6. Second opinion — the review commands` and its
  `### Triaging what comes back` subsection in full. Renumber `## 7`
  through `## 10` down by one (`## 6` Severity, `## 7` Write review.md,
  `## 8` Stop, `## 9` After the decision).
- `## 7. Severity...` (renumbered `## 6`), the "Signal density" paragraph:
  drop the clause "and adding a second opinion in step 6 raises that
  risk, not lowers it, unless its output is triaged as hard as your own"
  — there is no step 6 anymore for it to reference.
- `## 8. Write review.md` (renumbered `## 7`): change the template's
  `Second opinion:` header line to the repurposed wording above.
- `## Do not` (final section): remove "Run `/ponytail-review` or
  `/simplify` before your own passes are written down..." — the rule is
  moot once the commands aren't run in this stage at all.

## Interface

None — no functions, types, or config keys. The "interface" this work
item changes is the two files' text and, indirectly, every future work
item's `plan.md`/`review.md` shape.

## Acceptance criteria

- [ ] `build-skill`'s `## Phase two — implement` names the self-check as
      an explicit step, sequenced after the numbered implementation steps
      and their narrow checks, and before `make verify`.
- [ ] `build-skill`'s self-check step states the three-bucket triage
      (worth taking now / future work item / already settled) and that
      only the first bucket gets applied in the same diff.
- [ ] `build-skill`'s `## Do not` list gains the line closing off
      reporting work done without the self-check and `make verify` having
      both run.
- [ ] `deploy-skill` no longer contains a step numbered or titled
      "Second opinion," nor a "Triaging what comes back" subsection.
- [ ] `deploy-skill`'s remaining numbered steps (old 7-10) are renumbered
      to 6-9 with no gap and no duplicate number.
- [ ] `deploy-skill`'s `review.md` template shows the repurposed
      `Second opinion: none — ran during build...` line, not the old
      `both ran | <name> unavailable` wording.
- [ ] `deploy-skill`'s `## Do not` list no longer contains the
      "run before your own passes" bullet.
- [ ] `deploy-skill`'s Signal-density paragraph no longer references a
      "step 6" or a second opinion raising review risk.
- [ ] Every other section of both files — cold review, evidence-gathering,
      scope, the three review passes, the compliance pass, severity
      rules, the write/stop/decision procedure — is byte-identical to
      today except for the renumbering above.
- [ ] `scripts/artifact.py check` passes for this work item's own
      artifact chain (this is a documentation change; there is no `make
      verify`-style suite for `.claude/skills/*.md` prose itself).

## Non-goals

- A hook or script that mechanically verifies the self-check ran —
  considered and rejected under Requirement 7 / open question 1.
- Any change to `plan-skill`, `design-skill`, or `CLAUDE.md`. Only the
  two files named above.
- Re-reviewing or re-opening any closed work item (`C2` through `C9`)
  under the new process. This is forward-looking only.
- A cap-style limit on how many self-check findings `build-skill` may act
  on in one diff, mirroring deploy-skill's old "cap nits at five" — see
  Design, "Dropped entirely."

## Open questions

None. Both of `intent.md`'s open questions are resolved above (discipline-
level, not a hard gate; the repurposed header line, not a new section).

## Rejected alternatives

- **A new `## Build-stage self-check` section in `review.md`.** Rejected
  in favor of repurposing the existing `Second opinion:` header line —
  same information, no new structure, and a returning reader notices the
  change in the exact place they already look.
- **A hook-enforced gate requiring the self-check before `plan.md`
  validates, or before some new post-implementation file validates.**
  Rejected: no artifact in this project's chain is written after Phase
  two completes for a hook to check against, and inventing one for a
  single caller repeats the mistake guideline 2 warns about. See Design.
- **Keeping deploy-skill's full write-up apparatus (principle citations,
  the five-nit cap) and just relocating it verbatim.** Rejected: that
  apparatus exists to keep a document readable for a human who didn't
  write the code. `build-skill`'s Phase two has no such document — it's
  the same session deciding what to fix in its own diff — so porting the
  write-up machinery would be carrying weight with no reader it serves.
- **Running the self-check on every numbered step, not once at the end.**
  Not raised in the interview and not adopted: `/ponytail-review` and
  `/simplify` review a diff's shape as a whole; running them mid-
  implementation, before the diff is finished, would flag things later
  steps were always going to change, which is noise, not signal.

## Concerns

- **This is the biggest, and only, real risk `intent.md` already named:**
  once `deploy-skill` no longer runs an independent check, a weak
  self-check (an agent in a hurry, or one that under-triages its own
  candidates) has no downstream backstop at all. `intent.md`'s own
  Constraints accepted this trade explicitly; this spec doesn't reopen
  it, but a future work item revisiting this decision should know the
  backstop was removed on purpose, not restored as an afterthought.
- **Discipline-level enforcement (open question 1's resolution) means
  this can be skipped exactly as easily as the existing narrow-check step
  already can be.** That's consistent, not a new gap — but it means this
  spec is knowingly choosing consistency with an existing soft spot over
  closing it. Worth naming plainly rather than implying the new step is
  somehow more binding than its neighbor.
- No `project-structure`, `reference-lookup`, `testing-conventions`,
  security, brand, or UX policy skill applies: this work item touches no
  code and no tests, only two process-documentation files, and confirmed
  above that the reference corpus itself has no analog for a documented
  engineering process to learn from.
