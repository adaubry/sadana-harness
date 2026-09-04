# Intent: Automated code-quality feedback moves from the deploy stage to the build stage

Author: Adam Aubry (maintainer). Status: draft.

## Problem

Someone building a change in this project only gets automated "here's what
could be simplified, reused, or done more efficiently" feedback near the
very end of the process — after the change has already been confirmed to
work. When that feedback turns up something worth changing, the earlier
confirmation is now stale and has to be repeated. There is no earlier,
cheap point where that kind of feedback runs while the change is still
being written, before the more expensive "does this actually work" check
ever fires.

## Proposed outcome

Automated code-quality feedback runs once, during the build stage, on the
finished change, before the work is ever verified — so any revision it
prompts happens before the "does this work" check, not after it. The
deploy stage's independent, human-facing review keeps checking
correctness, security, and compliance against what was planned and
specified, but no longer duplicates that automated feedback. Anyone
reading the deploy stage's output can tell, from the document itself,
that the automated feedback isn't there because it was deliberately
moved earlier — not because a step was skipped.

## Affected users and systems

Not just the current maintainer — this methodology is meant to be used by
other engineers and other AI sessions going forward, so the trade-off
here has to be legible to someone who wasn't in this conversation. Two
parts of the process change together, since this is one relocation split
across both: the instructions for building a change, and the instructions
for reviewing one before it ships. Every future work item's build record
and deploy record will look different once this ships — the build record
gains a new item proving the self-check ran; the deploy record no longer
carries a second-opinion section at all.

Nothing about already-closed work items is touched. This is forward-looking
only, consistent with this project's rule against patching a closed
work item's record to match what came later.

## Constraints

- This deliberately gives up an independent, tool-based second opinion at
  the deploy stage, in exchange for an earlier, cheaper, self-administered
  one during the build stage. Anyone relying on the deploy stage's review
  as a backstop beyond the human reviewer's own judgment needs to be able
  to see, from the review record itself, that this was a considered
  trade-off, not a dropped step.
- The deploy stage's other guarantees are unaffected: reviewing the change
  cold, the reviewer never approving its own work, findings-only with no
  autonomous revision, and the correctness/security/compliance passes all
  stay exactly as they are today. Only the automated second-opinion
  mechanism moves.
- Already-closed work items are not touched or retroactively re-reviewed.

## Open questions

- Whether the new build-stage self-check should be a hard, enforced gate
  — so the implementation genuinely cannot be called finished without it
  having run — or a followed-by-discipline instruction like the build
  stage's existing narrow-check guidance. Left for the design stage.
- Exactly how the deploy stage's review record communicates "no
  independent second opinion was run here, by design" — a line, a note,
  something else — is a wording decision for the design stage, not
  resolved here.

## Changed during planning

The interview started from a narrower question — should one step within
the deploy stage move earlier — and surfaced a bigger one: which stage
should own this check at all. The requester's own stated motivation also
shifted what I understood the problem to be: not avoiding bias in a human
reviewer's judgment (my first framing), but avoiding a wasted verification
cycle when review-driven revisions arrive after the code was already
confirmed working. Given three real options (self-check only, self-check
plus keep the independent one, or restructure the deploy stage in place),
the requester chose the most decisive one — dropping the independent
check entirely rather than running both — after being walked through what
each gives up. That trade-off carries more weight than it would have
under the original assumption that this methodology is a "just me" tool:
the requester confirmed other engineers and other AI sessions are expected
to use this workflow, which is why legibility of the trade-off (the
Constraints section above) became a real requirement rather than a
nice-to-have.
