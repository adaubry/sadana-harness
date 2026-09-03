# Intent: A conversation's run knows when it has used up its allotted actions or time

Author: Adam Aubry (maintainer). Status: draft.

## Problem

Nothing today stops a conversation from going on forever — asking a model,
carrying out an action, asking the model again — for as long as a bug or a
runaway procedure keeps it going. Before anything can be built to stop
that, something has to exist that can be asked, honestly, "has this run
used up what it was allowed?"

This has already bitten the reference project this codebase learns its
lessons from: a cap meant to stop a run at 500 iterations was, in
practice, unlimited — the number actually enforced and the number the
documentation claimed had silently drifted apart. Nobody wants to be the
person who discovers, in the middle of an incident, that a limit they
believed was in place never actually was.

## Proposed outcome

A conversation's run can be asked, at any point, whether it has any of its
allotted actions or its allotted time left. Using one up says plainly when
there is none left, so whatever eventually runs a conversation's turns can
stop cleanly on its own instead of running forever or being cut off
mid-action. Wherever that limit's number comes from, it is resolved in
exactly one obvious place, so the number a person configures and the
number actually enforced can never quietly disagree the way it did in the
reference project.

## Affected users and systems

Only the maintainer, for now — sadana-harness has no other users yet. The
direct beneficiary is whoever next builds the part of this project that
actually runs a conversation's turns one after another — it inherits this
as the one place that answers "should I stop." Whoever configures how long
or how far a conversation's run is allowed to go is the other party this
touches.

## Constraints

- Nothing about a running conversation, a turn, or a second conversation
  competing for the same run exists yet, so this must be provable entirely
  on its own — no dependency on any of those.
- Only the specific numbers this work item's own mechanism actually reads
  are introduced. Other numbers a person might eventually configure for a
  conversation's run — anything to do with a child conversation, how much
  of a single action's result is kept, or how long a single step is
  allowed to take — are declined here; each is someone else's job, once
  something exists that actually reads it.
- No allowance is given back once used. The reference project has a
  mechanism for that, but only for one specific kind of action this
  project does not have; carrying the allowance-giving-back behavior
  forward with nothing to use it would be keeping a door open to
  nowhere.
- Nothing here assumes more than one part of the code will ever need to
  ask "has this run used up its allotment" about the same run at the same
  time.

## Changed during planning

Originally scoped to match the reference blueprint's config material
literally — one bundled block of settings covering this run's own
allotments together with a child run's allotments, a single action's
result-size limits, and step timeouts. Narrowed during the interview to
only the two numbers this work item's own mechanism reads; the rest get
introduced by whichever later work item first has something that actually
reads them, matching how this project has already treated other
not-yet-needed pieces before.
