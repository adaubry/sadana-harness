# Intent: Actually talking to the agent

Author: Adam Aubry (maintainer). Status: draft.

## Problem

Every piece of the agent that has been built so far — remembering what
was said, keeping its own record straight, deciding what it's allowed
to do and checking before it does it — has only ever been proven by a
script a maintainer runs by hand, or by a test nobody else sees. There
is still no way for a person to actually sit down and talk to it.

## Proposed outcome

Running one real command starts a live back-and-forth: type something,
get a real reply, keep going for as long as wanted, then stop. What
kind of assistant it is comes from a plain, editable description that
already has a sensible starting point — editing that description shapes
every conversation started after the edit; one already under way keeps
the description it started with, the same way changing what it's
allowed to do never reaches back into a conversation already running.
Before anything it does
reaches outside on its own initiative, it asks first, plainly, and only
goes ahead on a clear yes. Everything said, in both directions, is
written down as it happens, so it survives the running process ending
and can be picked back up later by the name it was given, or a fresh
one can be started instead. Which outside service actually answers is
read from a setting made once elsewhere, not chosen fresh every time,
though it can be overridden for a single run.

## Affected users and systems

For now, this is for a person sitting at an actual terminal — the
maintainer, or the team, reached however a running instance is reached
directly. It is not for someone reaching the agent any other way; that
is a different, not-yet-built path this doesn't touch. Everything this
depends on already exists and keeps working the same as before —
nothing about how a conversation is recorded, or how something already
built decides what it's allowed to do, changes because of this.

## Constraints

- Deciding which outside service actually answers is not this work
  item's job. It reads a choice already made elsewhere by default, and
  allows overriding that choice for a single run, but never invents a
  way to pick automatically among several.
- Finding a previously saved conversation by anything less than its
  exact known name is not built here — that already exists elsewhere.
  This only adds the ability to continue one once its exact name is
  already known, or start over fresh.
- Nothing here is reachable except by sitting at a terminal directly.
  No messaging app, no email, no other way in.
- What kind of assistant this is comes from exactly one plain,
  editable description with a sensible default — not several,
  not a mood or personality that changes on its own, not anything
  resembling a customizable character beyond that one description.

## Changed during planning

The interview changed five things from the source framing, and the
subsequent design pass corrected one of them and resolved the one open
question. First, given how much more this item leaves open than the
three before it, whether it should split into a smaller first slice was
asked directly and answered no — this lands as the whole interactive
command in one piece, not a proven-turn slice followed by a separate one
for the rest. Second, where the assistant's description comes from was
confirmed as the reference material's own approach: a sensible built-in
default, kept in one plain file a person can edit — checking the
reference material's actual behavior, not just a comment describing it,
during design found its real code builds that description once per
session and deliberately keeps it stable for cache-hit reasons, contrary
to a comment elsewhere claiming it reloads on every message; this
document's own proposed outcome above was corrected to match what the
reference actually does, not what one comment claimed. Third, which
outside service answers was confirmed as a setting read from somewhere
already decided, not something typed out in full every single time,
though a single run can still override it. Fourth, asking before
reaching outside was confirmed to happen plainly and by name every time
the question comes up, not silently, and not folded into a more detailed
explanation than necessary — the design pass then found this is already
exactly how the real, already-built terminal prompt this work item
reuses behaves (asks every time, remembers nothing), resolving this
document's original open question about repeated approvals without
deciding anything new. Fifth, continuing a previously saved conversation
by its already-known exact name was confirmed as in scope, while
explicitly declining to build any new way to find that name here, since
one already exists from earlier work.
