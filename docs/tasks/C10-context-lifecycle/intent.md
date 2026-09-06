# Intent: A conversation can compact its history and mark what's safe to reuse

Author: Adam Aubry (maintainer). Status: draft.

## Problem

Right now, nothing in sadana-harness owns "what happens to a
conversation's history as it grows." The turn-taking loop was built with
a place for that to plug in — a hook that runs when the model says a
request is too big — but the hook has never been given a real answer:
every real caller today hands it a function that always replies "don't
shrink anything," with a comment admitting the real behavior was left for
later. Three other moments where the loop could ask a bigger question
right now — before it sends anything to the model, after a result comes
back, after a tool finishes — have no place to ask at all.

The cost of leaving this open keeps growing rather than staying flat.
Every piece of the loop that already exists (and every one still to come)
builds its request to the model straight from the raw conversation,
unaware that marking the boundary between what's stable and what's still
changing is exactly the kind of information a model provider can use to
charge less and answer faster for a repeated prefix. Adding that awareness
after the fact means finding and touching every place that already
assembles a request, instead of the one place that should have owned it
from the start.

## Proposed outcome

A conversation can be asked, at four specific moments as it runs, what
should happen to its own history and to what the model is about to see —
and something answers, instead of nothing existing to ask. Two of those
four moments get a real answer now: what changed since the model was last
billed for a request, and which part of what's about to be sent is safe
to treat as unchanged from last time versus which part is new — carried
all the way through to an actual request a real provider receives, so a
repeat prefix is marked as reusable rather than just computed and thrown
away. The other two moments — deciding a conversation's history has grown
too large and needs to be shortened, and holding onto an oversized result
without keeping the whole thing in the live conversation — get a place to
be asked, but no real answer yet; whatever asks them today keeps getting
the same "no" it gets now, honestly, not silently.

## Affected users and systems

Only this project, in its current single-developer, phase-1 state — there
is no second user yet.

Systems: the part of the codebase that runs one exchange with a model
turn by turn (today the only real caller of the missing hook); the part
that actually talks to a model provider (today it has no notion of "this
part was sent before" at all, and has to gain one for any of this to be
externally observable, even though that part was previously considered
finished); the two places that currently stand in for real use of this —
the one-real-turn behavioral check and the end-to-end proof script — both
of which currently paper over the gap with a stub that always answers
"no."

## Constraints

- Deciding a conversation has grown too large and needs shortening, and
  holding an oversized result outside the live conversation, are both out
  of scope here — named as follow-up work, not built now.
- Nothing here can be designed around what runs a tool or what stores a
  conversation across restarts; both are somebody else's concern, later.
- Whatever this introduces to track a conversation's own running facts
  (what's been billed, what's been marked reusable) must not survive
  longer than the conversation itself, and must not be written to disk —
  that's a separate concern for later.
- Only one model provider is actually wired into this project today; this
  cannot invent a shape that already promises to work for providers that
  aren't connected yet.
- Copying how a reference codebase solved this is only a starting point —
  it has to be checked against what this project has already decided, not
  carried over as-is.

## Open questions

- What "safe to treat as unchanged" actually looks like as a piece of
  data — a position, a marker, something else — isn't settled. That's a
  design question, not a planning one, but it isn't answered anywhere
  yet.
- Whether the thing that tracks a conversation's running facts needs to
  be visible to anything outside this new piece (a future logging or
  observability effort, for instance) is unknown; nobody has asked for
  that yet.

## Changed during planning

Every section above changed shape at least once during this interview.
The initial framing named a diagnosis of a reference codebase's own
internal wiring, and a general worry about retrofitting a cache boundary
late, rather than a problem in this project — the interview reframed it
around a concrete, checkable gap already sitting in this codebase: a
real, injected hook that every real caller already stubs out on purpose,
on the record, with its own comment saying so. The scope also narrowed
twice: first from "all four moments, fully real" down to two real and two
honestly stubbed, and then the real cache-aware piece was confirmed, after
checking the actual code, to require touching an already-closed, already-
merged part of the system (nothing in it currently carries or forwards
this kind of information at all) rather than staying contained to one new
file — a deliberate, discussed choice to reopen that part, not scope
creep discovered later.
