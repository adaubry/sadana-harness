# Intent: letting an approved step actually run

Author: Adam Aubry (project owner). Status: draft.

## Problem

A plugin's declared procedure can include a step meant to reach outside the
process — call a real API, say — and, since the last work item, a person
can even be asked and say yes to running it. Saying yes does nothing: the
step still doesn't run. Separately, this project already decided, narrowly,
what "reaching outside" is even allowed to mean today — one real outward
web request, made from inside this project's own process, only for a step
the project owner wrote themselves. That decision was never connected to
the step that's supposed to use it. A plugin author can write the step, a
person can approve it, and it still goes nowhere.

## Proposed outcome

A plugin author can write a step that makes one real outward web request
and have what came back become the input to whatever step comes next in
that plugin's own procedure — for a step that has already been approved,
and only doing what was already decided to be allowed (one request, from
this project's own process, for a step the project owner wrote). Nothing
about what a step is allowed to reach is widened here; the only change is
that an approved step finally does something instead of stopping right
after being approved.

## Affected users and systems

Only the project owner — there is no other plugin author yet, and no
marketplace. The same reader who already found out an approved step
declined the same generic message either way finds out, now, whether it
actually ran.

## Constraints

- Every plugin installed today is first-party, written by the project
  owner — true regardless of design, not a choice being made here.
- Nothing here reopens what "reaching outside" is allowed to mean. That was
  already decided, narrowly, in an earlier work item, and this item does
  not widen it, add a second way to reach outside, or add sandboxing.
- Declines what a step hands back beyond the plain value the next step
  receives — anything that isn't that (a file, a link, something meant to
  be looked at rather than fed to the next step) is a separate, later
  concern.
- Declines turning today's one hand-written proof script into a real
  plugin — that's a separate, later concern, once a step can actually run
  something to prove.

## Changed during planning

The opening framing bundled four separate pieces of the same larger effort
into one ask — letting a step run, what a step hands back beyond its value,
turning the hand-written proof into real plugins, and grading a run by what
it actually did rather than by prose. Split into four work items along the
same lines this project already draws between them elsewhere, this being
the first and the other three explicitly out of scope here. The problem and
outcome were also redrafted once: the first pass posed them as open
questions for a person to answer; redirected to state them as already-
decided facts, since an earlier decision already settled what "reaching
outside" may mean and this item's only job is connecting a step that's
allowed to run to the mechanism that already lets it.
