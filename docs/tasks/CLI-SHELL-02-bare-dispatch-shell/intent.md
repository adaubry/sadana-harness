# Intent: Typing a command actually does something

Author: Adam Aubry (maintainer). Status: draft.

## Problem

Right now there is nothing to type. Every piece of the agent built so far
only runs if someone already knows it exists and writes a line of Python
to call it, or runs a hand-written proof script by hand. Someone who wants
to check that sadana is alive at all — before it does anything real — has
no command to run and nothing to point a terminal at.

## Proposed outcome

A person can run one real command from a terminal. Asking it its version
answers correctly. Asking for help gets a real answer instead of a crash.
Running it with nothing, or with something it doesn't recognize, gives a
clear message and the right kind of exit — success or failure told apart
consistently, not a stack trace and not silence. It does not do anything
else yet, on purpose: there is no real capability behind it at this point,
only the shell that a real capability will later be handed to.

## Affected users and systems

For now, only the maintainer exercises this, and only by way of a test —
the same as the last work item's search function. Nobody else has a
reason to run it yet, because nothing real is behind it. It is, though,
the first piece of a wider effort (recorded separately, since it names
things this document deliberately doesn't) aimed at a much larger
eventual audience — anyone reaching a running instance of the agent,
whether at a terminal over a remote session, or through whatever a real
capability wired up behind this shell ends up being. Nothing about that
wider audience is served by this work item directly; a later work item
decides what the first real thing behind this shell is and who reaches it.

## Constraints

- No command behind this shell does anything real yet. This work item
  proves the shell itself behaves correctly with nothing behind it; a
  specific, later work item is what wires the first real capability to it.
- Exactly one shape for "this went wrong" (a clear message and a
  consistent kind of failing exit) and one shape for "this is fine" is
  decided here, once, so nothing built on top of this later drifts into
  a second, different shape for either.
- Nothing about how settings are found or read changes. If anything this
  shell ever needs settings for later, it reads them the one way that
  already exists elsewhere, not a new one invented here.

## Changed during planning

The interview narrowed the source material in three places. First,
"prove the shell runs and exits correctly" was confirmed to mean proving
its top-level behavior only — version, help, and what happens with
nothing or an unrecognized command — explicitly declining to add even a
single throwaway command just to exercise the mechanism that dispatches
to a named command, since nothing real exists yet for that mechanism to
dispatch to. Second, the command's actual name was confirmed as the
project's own name rather than assumed from the reference material, which
uses a different product's name for an unrelated reason. Third, who this
serves was confirmed as "only the maintainer, by way of a test" for this
specific work item, matching the pattern already set by the prior one,
with the larger audience the wider effort describes left entirely to
later work items that actually put something real behind this shell.
