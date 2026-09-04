# Intent: A conversation survives the process that was running it

Author: Adam Aubry (maintainer). Status: draft.

## Problem

Right now, a conversation and everything recorded in it — what was said,
what was already done, and how much of its allotment has been spent —
exists only in the memory of the process running it. If that process stops
for any reason (a crash, a deploy, a restart), the conversation is gone.
Nobody can pick it back up: not with its history intact, not with its
progress remembered, not even knowing how much of its budget was already
used. Worse, an action that was recorded as asked-for but never confirmed
finished is currently not durably recorded at all — the rule that is
supposed to guarantee nothing gets carried out before it is safely written
down exists in name only, because there is nowhere durable to write it down
to yet.

## Proposed outcome

A conversation, and everything appended to it as it runs, survives the
process that was running it ending. It can be saved as it goes and loaded
back afterward by its own name, with its full history, its progress, and
how much of its allotment remains, resuming from exactly where it left off.
Saving under a name that is already in use never silently overwrites what
was there — and asking to load a name nobody ever saved is a clear failure,
not a fresh empty conversation pretending to be a real one. A save either
lands completely or not at all; there is no such thing as a half-written
conversation left behind by an interruption.

The existing promise that nothing gets carried out on a conversation's
behalf before the record of it asking is safely written down stops being a
promise with nowhere to actually apply, and starts being true.

## Affected users and systems

Only the maintainer, for now — sadana-harness has no other users yet. But
every piece of the conversation machinery built so far — the history rules,
the conversation-as-one-continuing-thing behaviour, the turn-by-turn loop,
the ability to spawn a sub-conversation for a focused task — currently has
this exact gap left open on purpose, waiting on this work item. The
turn-by-turn loop in particular already has a place built in for "durably
record this before doing anything with it" that today does nothing at all,
by design, until this exists.

## Constraints

- Only a conversation's own running record — what was said, its progress,
  its remaining allotment — is made durable here. What a conversation is
  allowed to do, and the exact wording of its own instructions, is not
  saved by this work item; whoever resumes a saved conversation supplies
  that starting point again themselves, the same way they did the first
  time it was created.
- The remaining time allotment is made durable as an amount of time left,
  not as a specific clock reading, because the clock it is normally
  measured against is deliberately one that resets to zero every time the
  process restarts.
- Looking a conversation up happens by its exact own name only. There is no
  way yet to ask the store for a list of everything it holds, or to search
  it by any other property.
- A save is all-or-nothing: it either durably lands in full or the caller
  finds out it failed, never a partial record.
- This work item does not change how a single running conversation behaves
  while it runs. It only supplies the durable place for what already
  happens to actually land.

## Changed during planning

The interview narrowed the scope in five places from how the source
material first framed it. First, durability was confirmed to cover both a
conversation's history and its own identity/progress/allotment together,
as one store, rather than history alone. Second, the running allotment of
time was confirmed as in scope, but only as a portable amount rather than
an absolute deadline, once it came out that the clock it is normally
measured against cannot survive a restart at all. Third, what a
conversation is allowed to do and its own wording (its "starting point")
was explicitly ruled out of durability — a caller re-supplies that by hand
on resume, matching how nothing in this project yet discovers or loads a
starting point from disk on its own. Fourth, listing or searching stored
conversations was ruled out; only look-up by exact name is in scope.
Fifth, and largest: reading the code that already exists surfaced that the
turn-by-turn loop already has the exact seam this work item needs to fill
built in and waiting, doing nothing on purpose — so this work item turned
out to be "implement that seam for real," not "add a new mechanism to the
loop," which is a smaller and more precisely bounded piece of work than
the source material's framing suggested on its own.
