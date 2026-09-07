# Intent: asking before a step reaches outside

Author: Adam Aubry (project owner). Status: draft.

## Problem

A person running a plugin has no chance to stop it before one of its steps
reaches outside the process. Today, once an agent decides to run a plugin,
every step in that plugin's procedure — including one that calls out to a
real external system — runs to completion without ever asking first. The
person only finds out what happened after it already happened.

## Proposed outcome

Before a step that reaches outside the process runs, the person is asked
whether it may proceed. It runs only if they say yes. If they decline, the
run stops there and reports that it was declined, rather than continuing or
silently skipping ahead. A step with no outside effect is never interrupted
this way — asking before something that changes nothing would just teach the
person to say yes without looking, and the check would stop meaning anything
the day it matters.

## Affected users and systems

Only the project owner — there is no other plugin author yet, no
marketplace, and no other person present to be the one asked. Whatever
surfaces a run's outcome to a person today is what also has to carry this
check.

## Constraints

- Every plugin installed today is first-party, written by the project
  owner — true regardless of design, not a choice being made here.
- The check must be answerable in the same moment the run is happening —
  there is nowhere for a run to pause and be resumed later, so the person
  has to be reachable synchronously when the question comes.
- Declines building any way to remember a past decision — every
  outside-reaching step asks again, every time.
- Declines building any rule or policy that decides automatically on the
  person's behalf.
- Declines a specific interface for asking — only that asking happens and
  blocks until answered.
- Declines the finer question of which outside-reaching steps deserve
  asking and which don't; every one of them is asked, because there is only
  one author today and no evidence yet that some need it and others don't.

## Open questions

- What does the person see, and does "every step asks" still hold, once
  there is more than one plugin author and the check needs to mean
  something different depending on who wrote the step? Not answerable
  today — there is no second author to learn from yet.
- How does this survive a future step that can outlive one run (an
  external event a plugin waits on)? The check as built here depends on the
  person being reachable synchronously; whoever builds that later has to
  also rebuild this.

## Changed during planning

The opening framing led with a comparison to another codebase's
implementation and stated a design decision — attaching at exactly one
point, before an outside-reaching step — as if already settled; both moved
out, since a comparison to prior art isn't a problem in the world and the
attachment point is a design answer, not an intent. The interview also
surfaced a live fork the framing had skipped past: whether "declared
effects" meant every outside-reaching step or a finer per-step exemption.
Picked, deliberately, against the more scalable-sounding option: every such
step is asked about, full stop, because a per-step exemption is a
classification system built for a population of one plugin author, and this
project's own rule declines building a seam for a second member that
doesn't exist yet. Also made explicit that no allowlist, no auto-approval
policy, and no particular interface belong to this item — none were stated
up front and a reasonable reader would assume at least one of them existed.
