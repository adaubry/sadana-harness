# Intent: A conversation survives running out of room, and a bulky tool result stops crowding it out

Author: Adam Aubry (maintainer). Status: draft.

## Problem

Right now, a conversation that grows too large for the model to accept
simply fails — there is no way forward, and nothing is ever built to
recover. The only reason that failure happens at all is that someone kept
talking; the system has no answer for that beyond giving up.

Separately, when a tool call comes back with something bulky, the only
defense today is to cut it off at a fixed number of characters and throw
the rest away, whether or not it mattered — and keeping the full copy
instead would just grow the conversation the exact way that first failure
already can't recover from, so the truncation stays permanent by
necessity, not by choice.

A third, smaller problem sits underneath both of these. Something in this
project already decides which part of what's sent to a model is safe to
treat as unchanged from last time — but it makes that decision by
assuming where a particular kind of message sits, rather than checking.
It's correct today only because of how one caller happens to build
things, and if that ever stopped being true, nothing would say so; the
decision would just quietly become wrong.

## Proposed outcome

A conversation that grows too large for the model to accept gets shorter
automatically instead of failing outright — a genuine second look at the
growing history, not a guess, that produces a shorter version keeping
what matters and the conversation continues.

A tool result too bulky to keep in the conversation is saved in full
somewhere it can still be found, with only a short stand-in left in the
conversation's own record — so the conversation doesn't carry its full
weight forward on every remaining turn, but what happened isn't silently
lost the way a truncated result is today.

Whatever marks the boundary between what's safe to treat as unchanged and
what's new keeps working correctly even after a conversation has been
shortened — today it would quietly point at the wrong place the moment
shortening becomes real, and after this it can't.

And the one place that boundary-marking decision assumes something about
where a particular kind of message sits, instead of checking, stops
assuming it.

## Affected users and systems

Only this project, in its current single-developer, phase-1 state —
there is no second user yet.

Systems: the part of the codebase that runs one exchange with a model
turn by turn — today it has exactly one dead end this closes (an
unrecoverable failure once the model refuses a request as too large) and
one permanent-truncation rule this adds a real alternative to. The part
that decides which requests can share cost with earlier ones — today it
carries an unchecked assumption this closes, and gains a rule for staying
correct after a conversation is shortened.

## Constraints

- A real second look at the growing history means a second live call to
  a model provider, on top of whatever call already needed one — this is
  not free, and proving it for real costs real money, the same as every
  other real round trip this project has already run.
- Whatever an oversized result gets saved to only needs to be findable
  again within the same run — it does not need to answer this project's
  own broader question of where things are stored for the long term,
  which nothing has answered yet.
- Nothing here can be designed around what a proper long-term storage
  answer eventually looks like — only enough to stop losing data today.
- Copying how a reference codebase solved either of these is only a
  starting point — it has to be checked against what this project has
  already decided, not carried over as-is.

## Open questions

Whether a shortened conversation's boundary-marking decision should be
recomputed narrowly (matching the original, template-level stable part)
or simply treated as "everything, for now" the way an already-resumed
conversation is — that's a design question, not a planning one, and
belongs to the next stage.

## Changed during planning

The interview surfaced a real tension not visible from the initial
framing: the project's own governing contract describes what sound like
two different checkpoints for shortening a conversation — one at the
moment just before a request goes out, another at the end of a turn —
while the one real retry this project has today already runs through
only the second of those, inherited unchanged from before either
existed. Confirmed, rather than silently assumed, that the existing
wiring stays where it is, rather than being moved to match the other
reading. The scope was also deliberately kept as one work item after
being offered a split — shortening a conversation and saving an oversized
result are shaped quite differently, one a model call and a rewrite, the
other a storage decision — a choice the user made after seeing the
alternative named plainly, not one arrived at by default.
