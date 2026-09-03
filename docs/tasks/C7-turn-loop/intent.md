# Intent: One turn of a conversation actually runs, start to finish

Author: Adam Aubry (maintainer). Status: draft.

## Problem

Everything needed to run a conversation now exists in isolation — a way
to remember what was said, a way to describe what actions a model may
take, a way to limit how long a run is allowed to go, a way to ask a
model a question and trust what comes back — but nothing actually runs a
turn. Nothing takes a question, asks it, carries out whatever the model
asked for, asks again if it needs to, and reports what happened. Every
piece works alone; nothing ties them together into something a person
could actually watch happen.

Worse, several ways a turn can go wrong partway through — running out of
its allotted actions, a question too large for the model to answer, being
interrupted, a save that silently didn't take — have no defined way to
end the turn cleanly. Today, each of those would either crash
uncontrolled or hang forever, because nothing has ever run far enough to
hit them.

## Proposed outcome

One thing exists that runs a full turn: given a conversation so far and a
new question, it asks the model, carries out whatever actions the model
asked for, asks again if it needs to, and always ends in one of a small,
fixed number of clearly named ways — whether it finished normally or
something specific stopped it partway through. Nothing about how a turn
ends is a surprise, a crash, or a silent hang, and every one of those
named endings can actually be made to happen and observed, not just
listed as a possibility.

## Affected users and systems

Only the maintainer, for now. This is the first work item with a real
caller for everything built so far — the conversation history, the tool
list, the run's allotments, and the connection to a real model all get
used together for the first time, by the same thing. It is also what
whatever comes after this — running more than one turn, a real
conversation object, proving this project actually works end to end —
will need in order to exist at all.

## Constraints

- The parts of this project that don't exist yet — the part that
  actually carries out an action a model asked for, the part that
  shrinks an overly long conversation down, the part that saves a
  conversation somewhere durable — are not built here. This work item
  only creates the exact place each of them will plug in later; whatever
  stands in for them right now does nothing at all.
- Nothing runs, asks the model again, or carries out an action without
  something in this work item's own design calling for it — no hidden
  retries, no silent extra work beyond what's already described.
- Nothing here decides what happens when more than one conversation, or
  more than one turn, is running at the same time.
- The care this project has already taken with the model's own promises —
  never trusting raw output, checking things thoroughly rather than
  assuming they're fine — does not get relaxed here just because this
  work item is the one tying everything else together.

## Changed during planning

The interview surfaced that two more not-yet-built parts of this project
— carrying out an action, and saving a conversation durably — need the
exact same "not built, but a stable place to plug in later" treatment
already planned for shrinking an overly long conversation. Without that,
several of the named ways a turn can end would have been unreachable and
untestable. It also surfaced and resolved a real, previously unremarked
disagreement between two existing decisions about whose job it is to make
sure a carried-out action's result is safe to record — resolved in favor
of the more careful of the two.
