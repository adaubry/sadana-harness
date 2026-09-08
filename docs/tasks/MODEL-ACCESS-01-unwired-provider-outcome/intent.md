# Intent: Asking for a service that isn't there fails the same way everything else does

Author: Adam Aubry (maintainer). Status: draft.

## Problem

Whether someone gets told plainly that they asked the agent to use an
outside service that doesn't exist, or one that exists but isn't
actually connected yet, depends entirely on whether that particular
caller happened to write its own check for it. Nothing underneath
guarantees it. One caller already had to write that check itself, by
hand, to avoid crashing outright. Another has no such check at all and
would simply crash with a raw internal error instead of a clear reason.

## Proposed outcome

No matter what starts a conversation and says which outside service to
use, asking for one that doesn't exist, or one that isn't actually
connected, is treated the same way every other reason a request can
fail already is treated: a clear, specific reason, never a raw crash —
and nothing that wants this has to write its own check to get it.

## Affected users and systems

Everything that starts a conversation and names which outside service
to use. Today that's the interactive command already built, and the
evaluation harness, which currently has no protection against this at
all. Nothing about how a working request is actually sent, retried, or
behaves changes.

## Constraints

- The one place already proven, by its own existing tests, to fail
  immediately and plainly the moment a request is attempted keeps doing
  exactly that, unchanged. This work item does not touch or weaken
  anything already relied on there.
- Nothing here decides between several outside services, or falls back
  from a broken one to a working one automatically. It only makes "the
  one asked for doesn't work" a clean, expected outcome instead of a
  crash.
- The interactive command that already grew its own hand-written check
  for this, to protect itself before anything else existed, gives that
  check up once the real thing is in place underneath it. One place
  decides this, not two.

## Changed during planning

The interview changed three things. First, whether this should split
into two work items (one place removing a hand-written check, added by
an earlier, already-closed work item) was asked directly and answered
no — giving up that check is part of the same story as the fix that
makes it unnecessary, not a separate later decision. Second, confirmed
that check should actually be removed once this lands, not kept
alongside the real fix as a belt-and-suspenders duplicate. Third, and
found only by checking the existing test suite rather than just the
source: the lowest-level function involved is itself already
deliberately proven, by tests already in place, to fail exactly the way
it fails today — which rules out changing that specific function before
the design stage even starts, and narrows where the real fix can
honestly live to something built on top of it instead.
