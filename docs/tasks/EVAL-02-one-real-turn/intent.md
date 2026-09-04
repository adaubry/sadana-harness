# Intent: One real turn proves the whole pipeline is actually right, not just running

Author: Adam Aubry (maintainer). Status: draft.

## Problem

A great deal has been built — a way to hold a conversation with a real
model, a way to let it call on a focused helper, and, most recently, a way
to write a check on its behaviour once and run it again — but nothing yet
proves any of it is actually *right*, only that it runs. Every proof so
far either had a person watch a script execute once and read the output
themselves, or checked something with no real stakes behind it, deliberately.
Everything built after this point rests on the unverified assumption that
this all still works for something that matters, checked automatically
rather than by a person watching. If the first real, meaningful check
turns out wrong, or the machinery that is supposed to catch a wrong answer
can't actually tell a right one from a wrong one, every later check
inherits that same blind spot without anyone noticing.

## Proposed outcome

One real, single exchange — a genuine prompt sent to a real model,
exercising the actual choice this project's own recent work was built
around: whether the model correctly makes use of a focused helper it has
been told about, when asked to. The response is graded by a real,
programmatic check of what the model actually did, not read and judged by
a person. This becomes the first real, properly designed check recorded
in the form the previous work item established — not a script with
inline checks, a reusable description that can be run again — and its own
real run is the proof, readable by a person afterward, that the prompt
going in, the real model being called, the helper actually being used,
and the grading of what happened are all correct, end to end, in one
piece, not just individually.

## Affected users and systems

Only the maintainer, for now. This is the first real use of the
mechanism the previous work item built — everything that mechanism has
run so far was a deliberately trivial, structurally uninteresting check,
proving the machinery compiles rather than that it can actually tell a
real right answer from a real wrong one. It is also the first time the
two small stand-in add-on procedures built for an earlier one-off proof
script get reused as a repeatable, gradable check instead of living only
inside that script's own inline assertions.

## Constraints

- Exactly one exchange with the model — not the several-exchange
  sequence an earlier one-off script used to prove the same underlying
  capability by hand. "One real turn, end to end" is the whole scope of
  what this checks; anything needing more than one exchange back and
  forth is out of scope here.
- Real spend against a real model is required to produce this proof, the
  same as every other real proof this project has produced so far.
- Judging what happened is a plain, programmatic check against the
  model's actual response and actions — never a second model asked for
  its opinion, the rule the previous work item already established and
  this one does not get to quietly set aside.
- This does not become part of the checks that already run automatically,
  for the same reason the previous work item's own proof didn't: real
  money, and an answer that can vary between two otherwise identical
  runs.
- Whatever the checking mechanism built previously cannot yet do, but
  needs to be able to do to make this a genuine check rather than another
  trivial one, is extended as part of this same work item — not worked
  around, and not deferred to a second, still-not-real attempt wearing a
  more convincing name.

## Changed during planning

The interview resolved which real behaviour this first genuine check
should verify. The maintainer chose the more consequential of two
options put to them: whether the model correctly makes use of a mounted
add-on procedure, reusing the exact scenario already proven by hand in
two earlier work items, rather than a simpler check of a plain, direct
answer with no such choice involved. That choice was made explicitly
because it is the actual capability the most recent block of work was
built around, not because it was the easier of the two to check.
