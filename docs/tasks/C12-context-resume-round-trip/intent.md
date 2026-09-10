# Intent: context state survives a conversation resume

Author: adam aubry (developer/operator). Status: draft.

## Problem

Whoever resumes a conversation — by asking the command-line chat to pick up
where an earlier one left off, or by sending another message into a gateway
conversation that already exists —
gets worse behavior than if that conversation had simply kept running, and
nothing tells them so. Their prompt caching gets less effective than it was
before the resume, and the running total the system uses to track how much
context has been used quietly snaps back to zero, as if the conversation had
just started. Nothing in the visible output says either of these happened;
the operator only finds out later, if at all, through higher cost or a
context overflow that arrives sooner than it should have.

## Proposed outcome

A conversation resumed from storage behaves, for caching and usage-tracking
purposes, exactly as it would have if it had simply kept running — no reset,
no loss of precision, caused purely by the fact that it was saved and
reloaded in between. The guarantee this behavior is supposed to provide
should survive being written to storage and read back, not just survive
while the conversation stays in memory for its whole life.

## Affected users and systems

Only the person operating this harness today — there are no external users
in phase 1. Two things exercise this gap already, on every resume, not
hypothetically: resuming a conversation from the command line, and a gateway
conversation that continues after its first message. Both currently pay the
same cost on every resume.

## Constraints

- Conversations already saved to storage under today's format must keep
  loading afterward — no destructive migration, no crash on old rows.
- The fix stays inside what already exists — no new dependency, no new
  storage backend.

## Open questions

- Recovering the narrower, pre-resume caching boundary may require knowing
  more about how the conversation was originally built than gets saved
  today (its originating template, not just its resulting text) — whether
  that needs new information to be stored, or can be derived from what's
  already there, is a design question for the next stage, not resolved
  here.

## Changed during planning

Two things moved during the interview. First, the problem turned out not to
be hypothetical: grepping for real callers showed both the command-line
resume path and the gateway's message-continuation path already exercise
this gap on every resume, not just a proof script — this raised it from a
paper cut to a live one. Second, the interview surfaced that the gap is actually two
separable losses (usage-total reset, and cache-boundary precision loss) that
could have been split into two work items; the user chose to keep them as
one, since both come from the same root cause — the reload path never
captured what it would need to reconstruct either one.
