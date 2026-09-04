# Intent: One real conversation proves every promise this backbone made

Author: Adam Aubry (maintainer). Status: draft.

## Problem

Nothing built so far actually proves this project's central claim — that a
real, ongoing back-and-forth with a real model behaves the way every piece
built for it says it will. Each guarantee has only ever been checked on its
own, in isolation, against a stand-in that behaves exactly as scripted: that
the instructions given to the model never quietly change partway through a
conversation, that a focused helper task spawned partway through gets its
own separate record and its own separate allowance and genuinely cannot do
more than the narrow slice of ability it was handed, and that running out of
allowed actions stops the conversation cleanly rather than leaving it broken.
Nobody deciding whether this backbone is actually ready has one piece of
evidence that ties all of that together, and nothing has ever run more than
one of these pieces at once against an actual model instead of a scripted
stand-in.

## Proposed outcome

One continuing exchange with a real model, produced by a standalone proof —
not woven into the automated, no-network test suite, matching this project's
own rule that a first real external round trip is proven this way and its
output pasted where a human deciding can see it, the same way the very first
such proof for this project already works. The exchange has several
back-and-forth turns. Two different add-on procedures come into play across
those turns: one of them itself calls in a focused helper for one narrow
task, while the other does the same kind of job with the least mechanism
possible. The instructions given to the model are shown to be identical,
byte for byte, from the first exchange to the last. The helper task spawned
partway through is shown to have its own separate record and its own
separate allowance, and is shown provably unable to do anything beyond the
narrow slice of ability it was handed, even though the main conversation
could do more. Finally, the exchange is deliberately pushed past how many
actions it is allowed to take, and is shown to stop itself cleanly rather
than break down, leaving behind a record where every asked-for action has
exactly one answer and none are left dangling. All of it lands as one piece
of output a reviewing person can read start to finish.

## Affected users and systems

Only the maintainer, for now. But this is the first proof this project has
that everything built for it so far — its message-history rules, its
available-actions handling, its allowance handling, its turn-by-turn
mechanics, its continuing-conversation behaviour, and its nested-helper-task
behaviour — is still true with a real model on the other end, not a scripted
stand-in. Every later piece of work on this block, and the decision to call
this block itself finished, depends on this proof holding.

## Constraints

- No real add-on-procedure system exists yet. The two add-on procedures this
  proof uses are hand-written stand-ins that satisfy only the exact shape
  the existing helper-task-spawning mechanism already expects — nothing that
  discovers, installs, or runs a real one is built here.
- Real spend against a real provider is required to produce this proof; it
  cannot be produced any other way, and stays out of the automated,
  no-network test suite on purpose. The maintainer has confirmed readiness
  to spend it.
- The model is deepseek/deepseek-v4-flash-0731 on OpenRouter — the only
  provider this project's real hookup currently supports, and the same
  model already used to prove the model-calling piece for real. Chosen for
  being fast and cheap, not for capability. If it cannot reliably do what
  the proof asks of it (for instance, reliably choose to use an available
  action when the scenario calls for it), that is itself worth recording
  plainly, not silently worked around by changing the proof until it
  passes.
- This proof does not run automatically as part of any check. Its output is
  captured once and pasted by hand where a person deciding can see it.

## Changed during planning

The interview surfaced two real forks rather than confirming a fully
pre-decided plan: whether the maintainer was actually ready to spend real
API credit right now (confirmed yes, rather than assumed), and which exact
model to pin the proof to. The model choice was not a free pick — this
project's only earlier real-provider proof, produced for an earlier work
item, already established deepseek/deepseek-v4-flash-0731 as this project's
real-round-trip model; the maintainer's own answer matched that precedent
once the three currently-available DeepSeek V4 Flash variants on OpenRouter
were looked up and named exactly, rather than left as a vague "deepseek v4
flash" that could have resolved to a different, unpinned release later.
