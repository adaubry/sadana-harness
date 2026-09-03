# Intent: A negative time allotment is refused, not silently ignored

Author: Adam Aubry (maintainer). Status: draft.

## Problem

A person configuring how long a conversation's run may go can set the
wall-clock allotment to a negative number of seconds by mistake, and
nothing tells them — the run behaves as if no time limit were set at all,
silently, instead of refusing the bad value the way the sibling setting
(how many actions a run may take) already does.

## Proposed outcome

Setting the wall-clock allotment to a negative number of seconds is
refused outright, loudly, at the moment it would take effect — the same
way setting the action allotment to a negative number already is. A
person who makes this mistake finds out immediately, not by noticing,
later, that a run somehow never stopped when they expected it to.

## Affected users and systems

Only the maintainer, for now. The same person and the same later work
(whatever eventually runs a conversation's turns) that the original
allotment work affects.

## Constraints

- This only changes what happens for a negative value. Zero and unset
  keep meaning exactly what they already mean — no time limit.
- Nothing about this depends on anything not already built.

## Changed during planning

Nothing changed — the problem, its cause, and its fix were already fully
diagnosed in C4-budgets' own Deploy review before this work item started,
and the interview confirmed rather than altered any of it.
