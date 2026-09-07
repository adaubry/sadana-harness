# Intent: a real plugin run, actually checked

Author: Adam Aubry (project owner). Status: draft.

## Problem

The only thing that has ever proven a plugin's whole procedure works —
being asked before an outward step, actually running it, and using what
came back — is a script whose own notes admit it fakes the very thing it's
supposed to prove: it writes the steps out by hand in Python instead of
describing them the way a real plugin author would and letting them run
for real. And even that script's result is just a paragraph of text
afterward. Nothing checks it; a person has to read it and decide for
themselves whether it did the right things.

## Proposed outcome

Real, on-disk plugins — described the way any plugin author would
describe one, not hand-written inside a script — run their whole
procedures for real, including being asked before an outward step
happens, that step actually happening, and something structured coming
back from it. A check exists afterward that can look at the structured
record of a run and say, on its own, whether it actually did the right
things — not by reading a paragraph and guessing, and not by asking
another model for its opinion. And the planning document that has been
tracking this whole effort can honestly say its own work is done: every
piece it called for exists and runs, including the one case it names by
name — a real plugin's run, going through the actual approval question,
start to finish.

## Affected users and systems

Only the project owner — there is no other plugin author yet, and no
marketplace.

## Constraints

- Every plugin today is first-party, written by the project owner — true
  regardless of design, not a choice being made here.
- Whatever exercises the approval question here has to answer it honestly
  and the same way every time, not bypass the question or fake an answer
  that was never really asked — the point is proving the question gets
  asked and answered, not skipping it to make the run go faster.
- Declines a second model judging whether a run did the right thing —
  matches this project's own already-settled position that a check reads
  a plain, structured record and decides mechanically, never by asking
  anyone, human or model, for an opinion.
- Declines building for a future population of many plugins or many
  checks. One real run, checked once, in a way that can be run again — not
  a general system for grading runs.

## Changed during planning

Two scope questions came up during planning that weren't obvious from the
opening framing and were put directly to the project owner rather than
assumed: whether the ported plugin's outward step should stay a stand-in
or become a genuine outward-reaching, approval-gated step (decided: made
genuine, since a stand-in would never actually exercise the approval
question this item is supposed to prove happens), and whether one or both
of the two originally hand-written plugins should be ported (decided:
both). A third question — whether combining what the tracking document
calls two separate pieces of remaining work into one item still counts as
one coherent piece of work — was raised internally rather than assumed;
the project owner had already decided this explicitly and repeated the
decision when asked to confirm it, so it stands as given rather than
re-litigated here.
