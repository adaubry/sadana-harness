# Intent: The proof script's own bookkeeping stops silently losing track of who it already spawned

Author: Adam Aubry (maintainer). Status: draft.

## Problem

The real-conversation proof script this project already produced has a
piece of its own internal bookkeeping that is wrong: it tries to remember,
across turns, how many focused helper tasks it has already spawned, so
that a second one never accidentally gets given the exact same name as an
earlier one. The way it currently tries to remember this does not
actually work — the count silently resets every time, invisibly, and the
one real run this script has ever produced never exposed the mistake by
sheer luck: it only ever spawned each of its two helpers once, under two
different names, so nothing ever collided. If it, or anything built the
same way later, ever spawned a second helper under a name already used,
that second helper would silently be given the exact same identity as the
first — the "each one is its own separate, addressable thing" guarantee
this whole area of the project exists to prove would quietly stop being
true, with nothing to say so.

Separately, and for a related reason: the design document that is
supposed to describe how this script actually works no longer does,
because a fix made partway through building it was never written back
into that document. That is exactly the mistake this project's own
process exists to catch, and it happened anyway, on the very last piece
of work before this one.

## Proposed outcome

The bookkeeping is fixed at its actual source, not merely avoided by
chance: spawning a second helper under a name already used in this run
gets it a genuinely different identity from the first, proven by a check
that deliberately does exactly that — something the original proof never
attempted, since it never had a reason to reuse a name. The corrected
mechanism is also written into this work's own design document accurately
enough that a future reader learns how the script actually works by
reading that document, not by separately reading a written explanation of
a bug that no longer exists. Nothing about the deeper reason this was
possible in the first place — that the piece of code responding to a
request to "run this helper" currently has no way to report anything back
to whoever asked, beyond a single line of text — is solved here; that
stays exactly as open as it already was, for whichever later piece of
work actually builds a system where that matters for real.

## Affected users and systems

Only the maintainer, for now. Only the one proof script this fixes —
nothing this project already
closed and recorded as decided is touched or reopened by this work. The
already-written explanation of the original bug is kept, not deleted,
once this work is done: it still correctly explains why the mistake was
possible, which remains true and worth knowing regardless of this fix.

## Constraints

- This work item does not edit anything belonging to the already-closed
  piece of work that produced the buggy script in the first place. What
  was decided and known at that time stays exactly as recorded; this is a
  new, separate piece of work, entered the same way any later discovery
  about closed work enters it.
- No real spend against a real model provider is needed to prove this
  fix. The mistake is a plain bookkeeping error in the script's own logic
  around an already-correct, already-proven mechanism; a scenario that
  spawns two helpers under the same name, run locally with no real
  network call, is enough to prove it either broken or fixed.
- Nothing already closed and proven correct is changed to make this work
  — the mechanism this script calls to actually spawn a helper and to run
  a turn is not touched; only how the script itself keeps track of what
  it already did is.
- The deeper reason this mistake was possible — one piece of code has no
  way to hand anything back to whoever is calling it, beyond a single
  line of text — is not solved here and is not pretended to be solved
  here. The existing written explanation of that gap is updated to say
  plainly that this fix closes the one concrete symptom it names, not the
  gap itself.

## Changed during planning

The interview settled one real fork: whether to fix only the bookkeeping
mistake itself, or to also correct the design document's now-inaccurate
description of how the script works, in the same piece of work. The
maintainer chose to do both together, on the reasoning already recorded
when the original mistake was accepted as a known, deferred limitation —
that document would need rewriting again regardless once a fix landed, so
doing it separately later would just be doing the same work twice.
