# Intent: Make cited analysis documents part of the version-controlled record

Author: Adam Aubry (maintainer). Status: draft.

## Problem

Someone who clones this repository fresh, or who opens an already-decided
work item later to understand why a call was made, follows a citation from
that committed decision and finds nothing there. The document it points to
was either removed from version control on the belief that it was a
disposable, work-item-time note, or was never committed in the first place.
Either way, the reasoning behind an already-approved decision is only
readable from the original author's local, uncommitted files — it is not
part of the record the commit history is supposed to be.

This has already happened: one such document is cited by ten separate,
committed specs and plans as the design authority for a decision, and has
never existed in git history at all. Another, cited eleven times the same
way, was committed once and later deliberately removed, on the reasoning
that it was disposable — a reasoning that the documents citing it, all
still standing, disprove.

## Proposed outcome

Every citation inside a committed decision record resolves to something
also committed, for anyone who clones the repository fresh with nothing
local. A document that someone produced by hand while deciding something is
kept as part of the record of that decision. A document that is instead
mechanically regenerated from an external source is not — it is fine for
that one to live locally only, because regenerating it is always available
as an alternative to reading a stale committed copy.

That distinction, once drawn, does not quietly erode again the way it did
the first time: the next time a hand-authored document lands in the same
place a regenerated one lives, something notices before it gets cited by a
committed decision that will outlive it on disk.

## Affected users and systems

Only the maintainer works in this repository today — no other contributor
or fork depends on this yet. The system that discovers the problem is
whoever (today, only the maintainer) clones the repository fresh, or reads
an old work item's citations without the benefit of whatever happened to
still be sitting on their own machine. The pre-commit checks that already
gate every commit are the natural system to make the "no future erosion"
half of the outcome self-enforcing, the same way they already gate secrets
and formatting.

## Constraints

Content that is mechanically regenerated from the external reference corpus
must stay out of version control regardless of how this is solved — this
project's own contributor guidance already states that index is generated
and must never be hand-edited, and committing a copy of it would invite
someone to trust a
stale snapshot over regenerating it fresh.

Nothing currently tracked in git may become untracked as a side effect of
this change — the same discipline the original scoping work item held
itself to.

This is a repair of what is already broken, not a redesign of how or when
these hand-authored documents get produced during a work item — that
process itself is out of scope here.

## Open questions

One hand-authored document in the same location is not cited by anything
committed yet (it predates any work item that would use it). Whether it
gets brought into version control now, for consistency, or only once
something actually cites it, is left to the design stage.

## Changed during planning

Arrived already bundling three closures: this citation-tracking bug, and
retroactively closing two unrelated already-open work items (one stalled
after its design stage on purpose, one missing its review despite its code
already being built on by four later work items). Interview separated
them: this intent covers only the citation-tracking repair as a single
commit; the other two are being closed as their own, separate actions
directly in their own existing directories, not folded into this chain.
Also changed: initial framing was "fix the currently broken citations
only"; the user chose to widen the outcome to include the recurrence
half after being asked directly, rather than leaving the same class of
mistake free to happen again to a different document.
