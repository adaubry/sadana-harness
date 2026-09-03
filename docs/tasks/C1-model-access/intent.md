# Intent: MODEL-ACCESS proves one provider before it names the rest

Author: Adam Aubry (maintainer). Status: draft.

## Problem

Hermes already solved talking to a model provider — dozens of times over,
once per provider, and every one of those implementations already works in
production today. Whoever builds MODEL-ACCESS next in sadana-harness faces
that same solved problem, but only one caller anywhere in this project
actually needs a model to answer right now — the conversation cycle that B2
already specified the shape of, still unbuilt. Porting every provider hermes
supports before that first caller has a proven, real answer coming back
means spending time re-doing work hermes already finished, for providers
nothing in this project calls yet, instead of spending that time proving
the one path that is actually needed reaches a model and gets a real answer
back.

## Proposed outcome

MODEL-ACCESS sends a request to a model and gets back a real response,
proven by an actual round trip to one working provider — not a stub, not a
mock. Every kind of outcome that one provider's path can produce — a normal
response, a retryable failure, a failure that needs different credentials
or a different provider, a request too large for the model's window, a
response with no usable content, and a failure not worth retrying — is real
and demonstrated on that path, not assumed to work because hermes's version
does.

The rest of the providers hermes already built are not thrown away or
re-litigated: their registrations are preserved, and there is one place any
of them can be registered and picked up later, so that adding the next one
is a registration, not a redesign. But none of those other providers'
request-handling code is ported or made to actually run in this pass — that
stays future work, taken on when a real caller needs it, not on the
strength of "hermes already has it."

## Affected users and systems

The maintainer, building alone. Every later piece of sadana-harness that
needs a model to answer depends on this — starting with whatever builds the
conversation cycle B2 already specified, which cannot be built or tested
against a real response, only a stub, until MODEL-ACCESS actually works.

## Constraints

This work item honors the interface B2 already decided and does not reopen
it: one entry point, a closed set of outcomes, an owned context-window
fact. Nothing here proposes a different shape for that boundary.

Only one provider's request-handling actually runs by the end of this work
item. The other providers' registrations are kept and made pluggable to a
shared mechanism, but their request-handling code is not ported, and no
second provider's round trip is required to demonstrate anything here.

Streaming responses, credential rotation across multiple accounts on the
same provider, and the tool-call plug point B2 named are out of scope —
this work item proves a request and a response, not the whole conversation
loop.

## Open questions

Whether a loading mechanism shaped to fit thirty-nine providers' manifests,
built before a second provider is ever ported, actually fits what that
second provider needs won't be known until one gets ported. This work item
accepts that risk rather than proving the mechanism against a provider it
is not building yet.

## Changed during planning

The first framing put the cost of building breadth-first as debugging
risk — failures scattered across many untested paths, hard to localize.
The interview corrected that: the cost the user actually named was
duplicated effort re-porting providers hermes already finished and nothing
in this project has called yet, not correctness risk from untested code —
a waste framing, not a reliability framing. On the provider manifests, the
suggestion on the table was to keep them inert and wire nothing, to match
that same avoid-premature-building instinct; the user overrode this and
asked for a generic loading mechanism all thirty-nine could plug into now,
while still declining to port any provider's actual request-handling code
beyond the one proven this round — narrower than "build everything," wider
than the suggestion offered. Shape was raised as a possible split between
copying the provider manifests and proving the transport layer as two
separate work items; the user confirmed one work item covers both, so it
stayed one commit. Failure-outcome coverage was raised as a possible
deferral (happy path now, full outcome set later); the user chose to make
the whole closed set from B2 real for the one path now, not deferred.
