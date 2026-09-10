# Intent: Scheduled and resumable triggers

Author: Adam Aubry (product owner). Status: draft.

## Problem

Right now, something only happens because a person sat down and asked for it.
Nobody can set up a capability that starts itself when a moment in time
arrives — nothing greets a Monday morning on its own. And once a task is
running, if it needs an answer from something slow — a report being
generated, a job running elsewhere, any outside system that doesn't reply
instantly — the only options are to sit there burning time waiting for it, or
to give up and lose all the work done so far. A capability that genuinely
depends on either of those two things — a moment arriving, or a slow answer
coming back — cannot be built at all today.

## Proposed outcome

Two things become possible that aren't today:

1. A capability can be set to start on its own at a chosen time or on a
   recurring schedule, with nobody present to ask for it.
2. A capability that is partway through its work can hand off to something
   slow, stop actively working while it waits, and pick back up automatically
   — with everything it had already done still intact — once that slow thing
   finally answers.

Both are observable the same way: something happens that nobody in the room
triggered by typing a message.

## Affected users and systems

Anyone building or using a capability that depends on either recurring timing
or a slow outside answer — a plugin author designing such a capability, and
the people who rely on it actually finishing rather than timing out or losing
its progress. This is deliberately general, not tied to one named plugin: the
same underlying need — "start me later" and "pause me, then resume me" — is
what any such capability requires, regardless of which one is built first.

What notices this changed: the long-running process that already listens for
outside events finds a second and third reason to act, beyond the one it has
today; whatever already keeps track of a conversation's history has to keep
track of one more thing — a task that is not finished, not abandoned, just
paused.

## Constraints

- A missed scheduled moment (the process was not running when it was due) is
  not caught up after the fact — it simply does not fire. No backfill.
- An answer arriving for a paused task that no longer exists (already expired
  or the process restarted and lost track of it) is discarded, not treated as
  an error that needs handling.
- A paused task waits for as long as it takes — there is no built-in point at
  which waiting itself is treated as a failure.
- The two mechanisms — starting on a schedule, and pausing/resuming an
  in-progress task — are built as two separate, independent pieces. They are
  not the same kind of thing wearing one shared interface, even though both
  answer to "triggers."
- A resumed task does not gain a new communication channel to hear its answer
  through — it reuses whatever the system can already receive from the
  outside world.
- Nothing about approving or gating what a resumed task is allowed to do is
  part of this — that mechanism does not exist yet, and this work does not
  invent one ahead of it.

## Changed during planning

The opening framing bundled these two mechanisms as one thing needing one
shared abstraction, named after the hermes-agent block being used for
reference. The interview surfaced that the two are mechanically different —
one starts something new, the other resumes something already
in-progress — and that a shared interface over both would abstract over that
difference rather than expressing it, so they were split into two
independent pieces within the same work item rather than one seam. The
resume mechanism was also initially open-ended ("wait for something to come
back") until grounded in a concrete scenario — a slow external system
answering later — with an existing communication path doing the actual
resuming, rather than a new one being built for it. Failure-mode behavior
(missed schedules, stale resumes, indefinite waits) was undecided going in
and pinned down explicitly rather than left implicit. The affected-users
answer also moved from an implicit "just the reference block" framing to an
explicit acknowledgment that this is the project's first work item built for
people beyond its own builder, not hypothetically but by direct statement.
