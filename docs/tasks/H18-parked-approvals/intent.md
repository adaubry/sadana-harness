# Intent: parked approvals

Author: adam (engineer). Status: approved.

## Problem

Right now, asking a person for permission and asking a person to answer a
question are two different, half-built things, and neither one is durable.

A wait for an answer already gets written down somewhere so it can be found
later. But a wait for a yes/no permission does not: it blocks the running
process on a keyboard, synchronously, waiting for someone sitting right there
to type. That is fine for one person at a terminal. It is not fine the
moment anything else is asking — a scheduled job, an incoming webhook, a
background run — because there is no one at that keyboard, and the whole
process sits frozen until it is killed.

Both problems are one problem: a person's decision, whether it is "may this
proceed" or "what should the answer be," is being treated as something that
happens in the moment or not at all, instead of something that gets written
down, can be found, can be answered from anywhere, and eventually expires if
nobody answers.

## Proposed outcome

Every time the system needs a person's answer before it can continue —
whether that is permission to proceed or a free-form answer to a question —
the need for that answer is written down before anyone is asked, in one
place, in the same shape regardless of which kind of question it is.

That written-down request can be found and answered from outside the
process that created it — including by someone who was not there when it was
asked. Answering it lets the paused work continue (or stop, if declined).
If nobody answers in time, the request expires on its own, and the work it
was blocking is treated as declined, not left waiting forever.

Nothing about how a person at a keyboard experiences this today changes:
someone running the system interactively still gets asked right there and
gets an immediate answer. What changes is that non-interactive callers —
things running unattended, on a schedule, or in response to an outside
event — never have to sit frozen on a keyboard nobody is at, because the same
underlying request is available to be answered by someone else, later,
through the system's normal way of being asked things from outside.

## Affected users and systems

- The person operating the system day to day, who currently answers
  permission questions at a terminal — unaffected in how that feels; they
  still get asked and answer right there.
- Anything that triggers work without a person typing at that moment — a
  scheduled job, an incoming webhook, or an automated caller — which today
  would freeze indefinitely waiting on a keyboard that isn't there, and after
  this work item instead leaves a findable, answerable request behind.
- Whatever surface lets someone review and answer outstanding requests from
  outside the process that created them — it needs a way to list what's
  waiting, and a way to answer each kind of request.

## Constraints

- A request for permission and a request for an answer are recorded before
  anyone is asked — never only in memory, never only as a return value.
- If nobody answers a request in a bounded amount of time, it does not wait
  forever — it expires, and the work it was blocking treats that the same as
  an explicit refusal, not as a green light.
- The interactive, at-a-keyboard experience is not touched — this is about
  giving non-interactive callers a real option, not replacing the existing
  one.
- This work item does not decide what a declined or expired request means
  for any specific kind of automated work beyond "the work does not
  proceed" — only that outcome, not new policy about retries, notifications,
  or escalation.
- Another, unrelated piece of work is touching adjacent, shared parts of the
  system at the same time. This work item's own changes must not collide
  with that — additions over edits wherever the two might otherwise touch
  the same file.

## Changed during planning

The work arrived fully specified — state machine, timeout, expiry behavior,
affected callers, and the shared-file coordination rule were all already
decided before this interview started. The interview confirmed the framing
(one root cause, not two problems) and the affected-users boundary (operator
and non-interactive callers, not plugin authors as a separate audience)
rather than changing scope. Recording that honestly: this is the case where
the interview mostly validated an already-complete answer, not one where the
model failed to push hard enough — the technical decisions it would
otherwise have had to extract were already made and stated up front.
