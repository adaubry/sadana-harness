# Intent: A conversation's history that cannot become malformed

Author: Adam Aubry (maintainer). Status: draft.

## Problem

When a conversation is interrupted partway through — a crash, a dropped
connection, a bug — while the assistant was waiting on the results of one or
more actions it had asked for, the record of that conversation can be left in
a broken state: an action recorded as asked-for with no recorded result, or a
new message added on top of that gap as if nothing were wrong. A model
provider will flatly refuse to continue a conversation whose history is
broken this way, so the person having that conversation loses it, and there
is currently no defined way to find out, let alone fix it, after the fact.

Every part of sadana-harness that will read or write a conversation's history
— the part that runs a full back-and-forth turn, the part that carries out
the actions the assistant asks for, anything that saves a conversation for
later — depends on that history always being well-formed. Nothing in this
project can currently guarantee that, detect when it has been violated, or
recover from a violation automatically.

## Proposed outcome

A conversation's history can always be appended to, but never made
malformed. An addition that would break the required shape — a result
recorded for an action nobody asked for, or a new question added while a
prior action's result is still missing — is refused outright rather than
silently accepted. And after an interruption that left a gap, there is
exactly one automatic, defined step that closes the gap — recording that the
missing result never arrived — before anything else about that conversation
is allowed to proceed.

Each message and each conversation is addressed by a stable, readable name
that a person could recognise on sight, not an internal reference that only
makes sense to the code that created it.

## Affected users and systems

Only the maintainer, for now — sadana-harness has no other users yet. But
every later piece of the conversation turn loop being built after this one
(the part that runs a turn end to end, the part that dispatches an
assistant's requested actions, anything that later saves a conversation
durably) is a caller of what this work item produces, and inherits whatever
guarantee — or gap — this work item leaves behind. Eventually, the person
having a conversation is who actually benefits: they stop losing a
conversation to a corrupted history they can't see.

## Constraints

- This work item keeps the history in memory only, for one running process.
  Whether a conversation survives a process restart or crash is explicitly
  not answered here — that is later work, once this shape is proven.
- It must be provable on its own, without the turn loop, action-dispatching,
  or any model-provider code existing yet — nothing here may require them to
  be tested.
- Every conversation and every message is identified by a natural,
  human-readable name, never by a database-assigned or in-memory-only
  pointer.
- This work item does not decide what counts as a well-formed history in
  general — only the one shape already settled on: an action's result must
  be recorded, for every action, before anything else is added on top.

## Changed during planning

Originally scoped as "the conversation loop" as a whole — effectively the
entire CONVERSATION block from hermes (348 files, 808 inbound edges there).
During the interview it became clear a prior document already exists
(`docs/reference/conversation_block_blueprint.md`) that splits CONVERSATION
into nine dependency-ordered work items, each its own commit, of which this
is only the first (history plus its identity and its one recovery step). The
work item was renamed from C2-conversation-loop to C2-keys-transcript to
match that narrower scope; the turn loop itself becomes a later work item in
this same C-series, built once this one exists to depend on.
