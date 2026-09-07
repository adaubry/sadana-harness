# Intent: EXECUTION — what a call step may do

Author: Adam Aubry (project owner). Status: draft.

## Problem

A plugin author cannot write a step in a plugin's procedure that reaches
outside sadana's own process. Every step today either reads and decides
inside sadana, or hands judgement to a model — nothing can touch a real
external system, so any capability that needs one (a real web request today,
eventually a marketplace author's own code) is blocked, and the second half
of the plugin system's own build cannot start.

## Proposed outcome

A plugin's outward-reaching step can run code that actually touches the
outside world, and what it is allowed to touch is bounded and inspectable
rather than arbitrary. Given a plugin whose steps are all first-party
(written by the person building this project, not installed from a source
they did not write), one of those steps can genuinely call out — hit a real
web endpoint, for instance — and the run still hands back a real, structured
answer describing what happened.

## Affected users and systems

Only the project owner — there is no other plugin author yet, and no
marketplace. This unblocks the plugin system's own execution half and,
later, a safety review's approval gate on effectful steps; neither exists
until this does.

## Constraints

- Every currently installed plugin is first-party — true today regardless
  of design, not a choice being made here.
- Declines sandboxing for untrusted or marketplace-installed plugin code:
  no marketplace exists and nothing is installed from a source the project
  owner did not write, so that problem doesn't exist yet.
- Declines the reference project's own catalogue of many outward-reaching
  backends — the backends themselves, not only the sandboxing question they
  would need.
- Does not build the outward-reaching plugin step itself, nor the later
  safety approval gate — this item is only the underlying mechanism those
  two build on.

## Changed during planning

The opening framing named a design decision directly and stated the outcome
in pattern language rather than an observable result — both moved out of
intent and into the design stage's territory. Scope narrowed twice: to
first-party, same-process execution only (sandboxing untrusted code
deferred, since no untrusted source exists), and to the underlying execution
mechanism alone, not the plugin step or the safety approval gate that will
later consume it.
