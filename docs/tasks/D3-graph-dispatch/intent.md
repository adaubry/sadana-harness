# Intent: A plugin's graph actually runs

Author: Adam Aubry (project owner). Status: draft.

## Problem

Nobody working on sadana can point at an installed plugin and watch it
actually run. Two earlier pieces of work already made part of this trustworthy
— a plugin's own description of itself can be checked for honesty before
anything happens, and a run's result now has a real, structured shape instead
of a bare line of text — but nothing yet connects the two. A request naming
one of a plugin's advertised actions still has no real path to becoming that
plugin's declared steps actually happening.

The only place anything like this has ever run is inside a script one person
wrote by hand, where the routing between steps was typed out as code rather
than read from the plugin's own file. That proves the idea is soundly shaped.
It does not prove the actual mechanism works, because the actual mechanism
does not exist yet.

## Proposed outcome

Given a plugin whose own file has already passed its honesty check, and a
request naming one of that plugin's advertised actions, the system carries
that request through the plugin's declared steps for real — including a step
that consults a model for judgement and a step that decides which of several
paths to take next — and hands back a real, structured answer: what happened,
whether the run finished normally, and, if it didn't, exactly where it
stopped.

This covers any plugin whose steps only read and decide — none of them reach
outside sadana's own process to touch anything else in the world. A request
naming an action nobody has installed, or a misspelled one, gets a specific,
sensible answer back rather than being silently ignored or crashing the
request that made it.

A person can watch a plugin — one they wrote, or a small one built to prove
this — run for real, end to end, without anyone having typed its steps out
as a one-off script.

## Affected users and systems

Every future user of sadana, though none exists yet — nothing is in
production. This is the only path an agent has to any capability beyond
talking (even a capability that is nothing but a prompt is still reached this
way), so the moment this genuinely works, it is not one feature working — it
is the idea of "a capability" becoming real in this codebase for the first
time. The people who notice directly and immediately: whoever reviews this
work item, and whoever next builds a plugin step that does reach outside the
process, which needs this to already work under it.

## Constraints

- No declared step is allowed to reach outside sadana's own process — call an
  outside service, write something meant to last, touch anything a person
  outside this one run would see. That half of a plugin's step vocabulary
  waits on a separate, not-yet-built piece that decides what such a step may
  safely do.
- A plugin's file has already been checked for honesty by an earlier, separate
  piece of work. This item does not re-decide any of those checks — it trusts
  a result that already passed them and does nothing if one hasn't.
- Nothing here installs, fetches, or otherwise brings a plugin onto the
  machine. A plugin already being present is assumed.
- A plugin cannot loop over a list of things, or wait for something to happen
  later and pick back up. Both are known, named gaps, deliberately left for
  later — not solved here, and nothing here should make either harder to add.
- When a step fails partway through a run, the system always hands back a
  real, structured answer describing what happened and where — it never lets
  an unexplained error crash outward past the request that triggered it.
- A step's judgement or decision can only be given what the step immediately
  before it in the same run produced — never everything that happened earlier
  in that run. A node that needs more than that is a signal to reconsider the
  plugin's steps, not to widen what every node receives.
- Nothing here builds a command line, a chat window, or any other new way for
  a person to trigger this. Proving the mechanism works is enough; wiring it
  into something a person actually types at is separate, later work — none
  exists to wire it into yet.
- This item does not touch the already-finished hand-written proof script or
  the fixture plugins it depends on. Anything this item needs in order to
  prove itself is new.
- A reasonable person might expect this to also let one plugin's action call
  directly into a different plugin's action. It does not — that question is
  explicitly left open at the project level, and nothing here decides it.

## Changed during planning

The interview corrected one framing directly: asked who else is affected
besides the person doing this work, the first offered answer ("only me,
nothing else exists yet") was rejected outright — every future user of sadana
is affected, because this is the sole path from an agent to any capability at
all, not a feature alongside others. That is now the section's actual claim.

Three real forks got settled by direct decision rather than left for the next
stage to invent: the outcome includes a real, working request-to-result path
end to end (not three pieces proven only in isolation with wiring deferred);
a failed step always comes back as a structured answer, never an unhandled
error; and a step is only ever given its immediate predecessor's output, never
the whole run's history. A fourth apparent fork turned out to already be
settled by code that exists today, not by this interview: how a
several-paths-forward step picks which path to take was an open question in
the design material, but the data shape already built for it in an earlier,
merged work item already commits to one answer, so this item does not reopen
it.

Also confirmed, not assumed: no command line, chat surface, or other
production entry point exists anywhere in this codebase yet. So "the plugin
becomes visible" means visible to a test and to a small proof script — the
same posture the project's most recent finished block used before anything
called it for real — not visible inside some existing product surface, because
there isn't one.
