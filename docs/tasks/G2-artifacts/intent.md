# Intent: handing back a link as itself, not as prose

Author: Adam Aubry (project owner). Status: draft.

## Problem

A plugin step can already reach outside the process and get back something
worth handing to a person — an address for something real that now exists
out there. But a run has no way to say so. Everything a run hands back is
squeezed into one block of text, and a genuine reference to something out
there looks exactly like a sentence describing one. A person reading the
result, or anything built to act on it later, cannot tell "here is the
thing itself" from "here is a description of it."

## Proposed outcome

When a plugin step already has a link in hand from reaching outside, a run
can hand that link back as its own labeled thing, separate from the text a
person reads — not buried in a sentence, not something that has to be
parsed back out of prose to be used again.

## Affected users and systems

Only the project owner — there is no other plugin author yet, and no
marketplace. Anything that reads a run's result today reads only its text;
this is the first work item that gives it something else to look at.

## Constraints

- Every plugin installed today is first-party, written by the project
  owner — true regardless of design, not a choice being made here.
- Declines writing a file anywhere. That needs a place for a run's own
  output to live, which does not exist in this project yet, and creating
  that place is a separate, later concern with its own real cost — this
  item hands back only a link to something that already exists somewhere
  else, nothing this project stores itself.

## Changed during planning

The opening framing treated "a file lands somewhere" and "a link comes
back" as one item, because the reference material describes them together.
Splitting them apart surfaced that they cost very different amounts: a
link needs nothing this project doesn't already have after the last work
item, while a file needs a genuinely new concept — a place for a run's own
output to live — that nothing here has decided yet. Narrowed to links only;
file-writing is left for its own later work item rather than bundled in
here for looking similar on paper.
