# Intent: A check on the agent's actual behaviour can be written once and run again

Author: Adam Aubry (maintainer). Status: draft.

## Problem

Nothing built so far can find out, after a change, whether the agent still
behaves the way it is supposed to. Passing the automated checks already in
place only shows that the code still runs; it says nothing about whether the
agent, talking to a real model, still does the right thing. The one time
that was actually checked, it was checked by hand: a person wrote a
one-off script, watched it run once against a real model, and read the
output themselves. That script cannot be run again to check a different
change, and nothing about how it worked was captured in a reusable form.
Every capability added after that one check is trusted on faith, not
verified, and there is currently no way to change that without repeating
the same one-off, by-hand exercise every time.

## Proposed outcome

A single check of the agent's real behaviour — what is asked of it, and
what a correct response actually looks like — can be written once, as a
plain, reusable description, separate from any particular script. That
description can be run for real, against a real model, and produces a
plain record of what happened and whether the response was judged correct,
in a form that can be looked at again later. Judging "was this correct" is
done by a straightforward, repeatable check against what the agent
actually did — not by asking another model to render an opinion on it.

What the first real checks should actually look for is explicitly not
decided here — this work item proves the mechanism holds together for
exactly one, deliberately unimportant, real check written to exercise it,
in the same way the very first proof of a real round trip in this project
proved a mechanism and not a feature.

## Affected users and systems

Only the maintainer, for now. This is the first piece of a new concern for
the project — whether the agent behaves — sitting alongside, not replacing,
the existing automated checks that only prove the code runs. It is also
the first thing other than the one-off proof script to call the
conversation-starting and turn-taking machinery already built, for real,
as a repeatable mechanism rather than a hand-run demonstration.

## Constraints

- Producing one real check's result requires spending real money against a
  real model, the same as every other real proof this project has produced
  so far. This work item proves the mechanism with exactly one
  deliberately minimal, throwaway check — not a curated set of checks
  worth keeping. Choosing what the project should actually check for is a
  separate, later piece of work.
- Judging a response is a plain, repeatable check against what the agent
  actually did — never a second model asked to render a judgement. Nothing
  audited from the reference project does this either.
- Whether two runs can be compared against each other, and what happens
  when a comparison shows the agent got worse, is explicitly not this work
  item's job. This produces one run's own record; comparing records is
  later work.
- This does not become part of the checks that already run automatically.
  It costs real money and its answer can vary between two identical runs,
  which is exactly why it is being kept separate rather than folded into
  what already runs on every change.
- Nothing about how the agent actually holds a conversation, spawns a
  focused helper, or is limited in what it can do is changed by this work
  item. It only becomes a caller of all of that, for a new reason.

## Changed during planning

Two real questions were resolved rather than assumed. First, whether this
work item's own proof should cost real money at all, given the actual
checks worth keeping are explicitly a separate, later piece of work — the
maintainer chose to still require one real, throwaway run, matching the
bar every other piece of this project has already had to clear, rather
than letting this be the first exception. Second, an entire separate,
independent piece of work — wiring the project's existing automated checks
into a system that runs them without a person remembering to — was started
first, then explicitly set aside mid-interview once a real gap surfaced in
what it would actually require (nothing today records what the project's
own checks depend on, so a fresh copy of the project cannot yet run them
at all); the maintainer chose to come back to that separately and do this
work first instead.
