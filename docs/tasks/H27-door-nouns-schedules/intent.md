# Intent: Cron schedules replace interval-only triggers

Author: adam aubry (operator). Status: approved.

## Problem

Right now, whenever anyone wants the box to do something on a recurring
basis, the only tool is a fixed interval — "every N seconds" — created by
hand and impossible to see, pause, or change from outside the process that
created it. Nobody using the console can express "every weekday morning" or
"the first of the month," can't tell whether a recurring job is still active
or has been silently paused, and can't stop one temporarily without deleting
it and losing it for good. When a recurring job stops firing, there's no way
to tell whether that was deliberate or a bug.

## Proposed outcome

An account using the console can create a schedule stated the way people
actually think about recurring time — "every Monday at 9am," not only "every
300 seconds" — can see when it last ran, what happened, and when it will run
next, can pause it without losing it, and can resume it later. Each account
sees and controls only its own schedules. A schedule that's been paused for a
while and is later resumed picks up from the next real occurrence, not by
replaying everything it missed while it was off.

## Affected users and systems

Any account using the console who wants recurring automated behavior from
the box — not only the person operating it solo. Whatever already relies on
the box's existing fixed-interval recurring jobs keeps working exactly as
before through the switch; nothing currently scheduled is silently dropped.

## Constraints

- A schedule can express minute/hour/day-of-month/month/day-of-week
  recurrence with the ordinary wildcard, list, range and step forms; it
  cannot express named weekdays/months or the rarer extensions (last-day,
  nearest-weekday, nth-weekday) some cron dialects support.
- A schedule that missed its fire while the box was offline does not fire
  retroactively when the box comes back — it re-arms for the next real
  occurrence, the same posture the box already takes for its existing
  interval-based recurring jobs.
- A paused schedule never fires, no matter how much time passes while it's
  paused.
- Every change to a schedule (created, paused, resumed, updated, removed) is
  recorded, not just applied silently.
- Firing a schedule never blocks any other conversation the box is holding
  at the same moment.

## Changed during planning

Two things were pinned down that the initial description left ambiguous at
the intent level (the design-level detail underneath was already dictated in
full): who this is for broadened from "the one operator running the box" to
"any console account," meaning schedules must be scoped per-account rather
than global; and the missed-fire behavior was confirmed to follow the
existing interval-trigger precedent (skip forward, no catch-up) rather than
firing once on recovery. Everything else in the request arrived already
fully specified down to the storage shape — unusually well-formed for an
intent, because the requester had already done the design thinking before
dictating it; the design and build stages carry that detail, not this file.
