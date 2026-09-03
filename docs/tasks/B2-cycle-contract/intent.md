# Intent: The conversation cycle — CONTEXT, MODEL-ACCESS, CONVERSATION as one contract

Author: Adam Aubry (maintainer). Status: draft.

## Problem

In hermes, CONVERSATION exchanges more with MODEL-ACCESS and CONTEXT than any
other pair of blocks in the system, and the exchange runs both directions
between all three. Whoever builds the first of these three blocks in
sadana-harness has to guess the shape of the other two before either exists
to check the guess against — there is no way to build, test, or reason about
any one of them alone. If that guess gets made under implementation
pressure, while writing code that needs an answer today, the resulting
design carries whatever quality that pressure allowed, and every later piece
of the cycle inherits that quality as debt, not just a redo.

## Proposed outcome

Before any of CONTEXT, MODEL-ACCESS, or CONVERSATION is built, there is one
written, decided description of the cycle connecting them: which one calls
which, what each hands to the next and gets back, and what happens when a
step in the cycle fails partway through — including the point where a tool
call has to run and the cycle waits on its result. Someone can pick any one
of the three blocks and start building it against the other two as stubs,
without inventing or guessing any part of the interface as they go.

## Affected users and systems

The maintainer, building alone today, and any future collaborator who picks
up CONTEXT, MODEL-ACCESS, or CONVERSATION later — they inherit this contract
instead of reconstructing it from whichever block happened to get built
first. Whether SESSION-STORE and LIFECYCLE also depend on this design, or
can be designed independently of it, is not known yet; that is an open
question below.

## Constraints

This work item is design only: it ends at the design-stage spec. No
build-stage plan, no code — deciding the cycle's shape is the entire
deliverable.

The full interface of EXECUTION — how a tool call actually runs — is not
being designed here. But the point in the cycle where control passes to it
and returns, and what crosses that boundary, has to be specified; the
cycle's shape is not real without it.

Failure handling for the cycle is in scope, not deferred. It should take
inspiration from how hermes handles it, without copying hermes's code or
its concrete design.

## Open questions

Whether SESSION-STORE (persistence) or LIFECYCLE (the outer agent loop that
drives this cycle repeatedly) depend on decisions made here, or can be
designed fully independently later, is unresolved. Whoever starts either of
those work items should check this contract first rather than assume either
way.

## Changed during planning

The first framing cited hermes's own edge counts between CONVERSATION,
MODEL-ACCESS and CONTEXT as the problem; the interview moved it to what
fails in sadana-harness instead — a guessed interface built under pressure
becomes debt for everyone who builds on it, not just rework for whoever
guessed wrong. The proposed outcome was pushed from "know which block calls
which" to a build-ready contract that also specifies what crosses each
boundary, after the narrower version was offered first and declined.
Failure-path behavior started as an open question and was pulled into
scope, on the condition that it take inspiration from hermes rather than
copy it. Whether tool execution needed a specified plug point in the cycle
was undecided going in; it was set to required, not opaque, once
EXECUTION's future dependency on this cycle was raised. Whether SESSION-STORE
or LIFECYCLE are affected by this design could not be resolved in this
interview and was left as an open question rather than guessed at. Shape was
confirmed as one work item covering all three blocks together, consistent
with the problem statement that none of them can be designed alone.
