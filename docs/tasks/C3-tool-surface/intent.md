# Intent: A fixed, honest list of actions a model can be told about

Author: Adam Aubry (maintainer). Status: draft.

## Problem

MODEL-ACCESS already accepts a set of tool definitions in every request it
sends, but nothing in sadana-harness can build that set. There is no way to
hand a model a fixed list of actions it is allowed to take, with real
descriptions — every request that will ever need to offer a model a choice
of actions is stuck with an empty list until this exists.

And when one action's description needs to mention another action by name —
"use this together with the export tool" — nothing today keeps that
description honest if the other one is later renamed. This is not a
hypothetical: it is exactly the kind of staleness hermes-agent's own history
shows, and it is why a rule against it already exists in this project's own
standing instructions, unimplemented until now.

## Proposed outcome

A fixed list of actions can be built once, from whatever set of actions is
available at that moment, and handed to a model in the shape it expects. If
one action's description needs to mention another action's name, it always
gets the name that action actually has right now, in that same list — never
a name written down once and left to go stale.

That list, once built, cannot change out from under anyone holding it: a
caller who only needs some of the actions can get a smaller version without
the whole thing being rebuilt, and everyone can tell, from a single fixed
value, exactly which actions and which descriptions a model was actually
offered.

## Affected users and systems

Only the maintainer, for now — sadana-harness has no other users yet. But
this is a direct dependency of the part of the conversation turn loop that
sends the first real request to a model with actions attached, and of
whatever later work registers this project's own first real actions. Both
inherit whatever this work item gets right or wrong.

## Constraints

- No real action exists in this project yet, and none is registered as part
  of this work item — this must be provable with made-up, test-only actions
  only, the same way the conversation history work proved itself with
  made-up messages.
- Actually carrying out an action is not part of this work item. Building
  the list and describing what is on it is; calling anything is explicitly
  someone else's job, later.
- There is no notion yet of a running conversation that this list belongs
  to, so nothing here can depend on one existing, and nothing here decides
  what happens when the list needs to change after a conversation has
  already started.
- Nothing here reads user-configurable settings; wherever a default value
  would come from configuration, this work item only leaves room for that
  default, it does not resolve one.

## Changed during planning

Originally drafted with a wider Proposed outcome that included actually
calling an action, mirroring blueprint language that names both alongside
each other. Narrowed after the interview to building and describing the
list only — carrying out an action reuses none of this work item's proof
and belongs with the part of the project that runs a full turn, later.
