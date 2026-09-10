# Intent: A run leaves numbers behind, not just a transcript

Author: Adam Aubry (maintainer). Status: draft.

## Problem

When a conversation turn or a plugin run goes wrong, or just goes slower or
more expensive than expected, the only way to find out is to read logs by
eye and guess. Nobody — not the maintainer building this system today, and
not anyone else who runs or works on a sadana instance — can currently ask
"how many turns did that take, how long, what did it cost, how often does
this fail" and get a real answer. There is no number to check against, so
every judgment about whether a run behaved normally is a guess dressed up
as an observation.

This gets more expensive to fix the longer it waits. Every block built
between now and whenever this gets added is a place this will have to be
retrofitted into later, by hand, one block at a time. Adding it now, while
the number of places a "run" happens is still small, is cheap. Adding it
after the turn loop, the plugin graph, and everything built on top of them
already exist is not.

## Proposed outcome

After a conversation turn runs, or a plugin's graph runs, a record of that
run exists and can be looked up afterward: how long it took, how many
turns or steps it involved, what it cost, and whether it failed. Someone
can ask this question about a specific run and get a real, structured
answer back — not a story reconstructed from reading log lines.

The smallest version still worth having: every turn-loop run and every
plugin-dispatch run leaves behind a structured record with those numbers,
and there is a way to read that record back for a given run without
opening a database by hand. Deciding what counts as a "regression" —
thresholds, alerts, anything that watches these numbers and reacts to them
— is explicitly not part of this outcome. This work item only guarantees
the numbers exist, are structured consistently, and are queryable; acting
on them is a later work item's problem.

## Affected users and systems

- **The maintainer, today** — currently debugging turn-loop and plugin-dag
  runs by reading logs and guessing. This is the person who hits the
  problem most often right now, during phase-1 backbone construction.
- **Other people who run or work on a sadana instance** — this is not a
  single-user problem; anyone operating or maintaining an instance needs
  the same numbers when something looks wrong, and none of them have it
  either.
- **The turn loop and plugin dispatch** — the two places in the system
  where a "run" happens today. Both need to leave a record behind; nothing
  else is instrumented as part of this work item.
- **A later work item that adds regression detection** — whatever comes
  next to watch these numbers and flag anomalies depends on this record
  existing in a stable, queryable shape. This work item is that
  dependency, not that feature.

## Constraints

- No new external dependency or service. Recording stays local to the
  instance — this is not becoming a call out to a third-party monitoring
  product.
- Must not add latency or a new failure point to the turn or plugin run it
  is observing. Recording a run's numbers is not allowed to become a
  reason that run runs slower or fails differently than it would have
  without being observed.

## Open questions

- Exact schema of a run record (which fields, what "cost" means precisely
  for a run that touches multiple model calls) is a design decision for
  spec.md, not resolved here.
- What "regression detection" ends up looking like, and when that work
  item gets scheduled, is unresolved and intentionally out of scope for
  this one.

## Changed during planning

The initial framing bundled recording numbers together with "something can
watch for regressions" into one outcome — closer to hermes's monitoring +
policy split. Narrowed during the interview to recording only: the record
must be structured so a later work item can add thresholds/alerting on
top, but deciding what counts as a regression is explicitly deferred, not
built here. Emitters were also narrowed from "every layer, so nothing
needs retrofitting later" down to just the turn loop and plugin dispatch —
the two places a run actually happens today; model-access and CLI/gateway
entry points are left uninstrumented for now.
