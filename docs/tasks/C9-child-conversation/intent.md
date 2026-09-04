# Intent: A closed, single-skill sub-task can be spawned and reports back cleanly

Author: Adam Aubry (maintainer). Status: draft.

## Problem

A plugin's procedure is built from small steps, and some of those steps are
deliberately closed, short agentic tasks — for example, "run a web search and
report back" — rather than a fixed action. Today there is no way to run a
step like that: hand it a single well-defined skill, give it only the tools
it needs, put a budget on it, and get back its result, without letting it see
or touch anything else the surrounding conversation is doing. Whoever
assembles such a step — a plugin author later, and for now this project's own
test scripts — has no primitive to reach for, and would otherwise have to let
the step share the parent's full toolset and budget, or hand-roll a one-off
mechanism per plugin.

## Proposed outcome

A closed, single-purpose sub-task can be started fresh: it runs one named
skill against one input, using only the tools its caller hands it, spending
only the budget its caller hands it (or a sensible default when none is
given), and optionally a different model than the one running the parent. It
reports back exactly what it produced — nothing more is inferred, validated,
or retried on its behalf; if what comes back is wrong, that is a fault in how
the step was set up, not something this mechanism corrects. Nothing it did is
folded into the surrounding conversation automatically, but a full record of
what it did is kept and can be pulled up by name afterwards if anyone wants
to see it.

## Affected users and systems

Right now the only caller is this project itself: the standalone script that
proves the wider turn-loop work, and the test suite that exercises it.
Nobody outside the repo depends on this yet. Looking forward, this is the
primitive a future plugin-orchestration engine will call every time a
plugin's procedure needs to run a bounded agentic step — so it has to hold up
under that use even though that caller doesn't exist yet.

What it touches: the conversation machinery that already runs one turn
end-to-end, the mechanism that limits how many turns and how much time a run
gets, the piece that decides which tools a run can see, and whatever calls
the underlying model — plus a new piece needed for the first time here:
reading a skill's instructions off disk before handing them to a fresh run.

## Constraints

- One sub-task runs exactly one skill — this is not a mechanism for a
  multi-skill or open-ended agent.
- The caller chooses the tool list, the budget, and the model for each
  sub-task; none of these silently fall back to "whatever the parent has."
- A sub-task's budget is not additionally capped by some outer ceiling beyond
  what the caller asks for — the budget itself is already a resource that
  runs out, so a second limit on top of it would guard against nothing real.
- Nothing a sub-task did is automatically shown to, or included in, the
  parent's own record or result — a full record still exists and can be
  looked up by name on request.
- A bad or unexpected result from a sub-task is not this mechanism's problem
  to catch — no scoring, retrying, or validating what comes back.
- Building the orchestration engine that will eventually call this is
  explicitly out of scope; only the thing it will call.

## Open questions

- How a caller points at "this skill, in this plugin" resolves to actual
  instructions on disk is not settled — there is no plugin catalog yet to
  look it up in. Whoever designs the next stage should propose a convention,
  and it should be confirmed before it becomes load-bearing for a real
  plugin.

## Changed during planning

Dropped an assumed safety rail: a config ceiling on top of the caller's
chosen budget for a sub-task. The budget mechanism already returns nothing
once it is exhausted, so a second, outer cap would have guarded against a
problem the budget already solves — this was caught only by pushing on it in
interview, not proposed by the requester. Also confirmed, against the source
material's own default suggestion, that budget and model — not just the tool
list — are chosen per sub-task rather than falling back to configuration; the
source material had only recommended per-task choice for the model.
