# Intent: A conversation becomes one continuing thing, not one call

Author: Adam Aubry (maintainer). Status: draft.

## Problem

Right now, asking a question more than once as part of the same
conversation does not work as "the same conversation" at all — it works
as one isolated call, repeated by hand. The caller has to already be
holding, and correctly pass back, the exact prompt that was used, a
fingerprint of it, the list of what a model is allowed to do, and how
much of the run's allotment is left. Nothing remembers any of that
between one question and the next, and nothing stops the prompt
underneath a running conversation from silently drifting between
questions — which is exactly the promise this project already made never
to break quietly.

Separately, a change that is only supposed to apply to a conversation
that hasn't started yet — a newly available way for the model to act, a
config value someone edited — has nowhere to be recorded. There is no way
to say "not for the one running now, but the next one should see it," and
have that actually hold true later.

## Proposed outcome

A conversation becomes one addressable, continuing thing. It can be
started once and asked a question more than once, and in between those
questions it remembers exactly what was said, what it is currently
allowed to do, and how much of its allotment remains — while never
letting its prompt change out from under a question already in flight.

A change that should not affect a conversation already underway can be
recorded against it, and it is provable that the conversation in progress
never sees it. A new conversation started from the same starting point —
the same identity, the same shape of prompt, the same available actions —
provably does see it.

## Affected users and systems

Only the maintainer, for now — this project has no other users in this
phase. This is the first thing that makes a conversation survive across
more than one question at all: the message history, the list of what a
model may do, the running allotments, the actual call to a model, and the
turn-by-turn mechanics that were each built separately now get used
together, repeatedly, by the same long-lived thing, for the first time.
It is also what an eventual real end-to-end round trip, and anything that
later runs more than one such conversation at once, will be built on.

## Constraints

- A real way to find, load, or run an outside procedure (what this
  project calls a plugin) is not being built here. A caller handing over,
  by hand, the handful of facts about one — its name, its purpose, which
  action starts it — is enough to prove the recorded-catalog behavior
  works; there is still no mechanism that discovers or runs one.
- Shrinking an overly long conversation down once it grows too big for a
  model to answer is still not built. The exact place that plugs in,
  already established by earlier work, is not touched here.
- Saving a conversation somewhere durable, so it survives the process
  ending, is not built here. Everything this work item makes lives only
  in memory for as long as the process runs.
- Nothing here lets one conversation spawn another, and nothing here
  decides what happens when more than one conversation — or the same
  conversation from two places — runs at the same time.
- The care this project already takes — a prompt that never silently
  changes, an allotment that is never quietly topped back up, a model's
  own claims about what it did that are always checked rather than
  trusted — holds here too, now under repeated use instead of a single
  call.

## Changed during planning

The interview surfaced two forks the source material had left open
rather than decided. First: whether the prompt should actually be built
up from its parts now, given that the mechanism for discovering an
outside procedure does not exist yet — the maintainer chose to build the
composition now rather than defer it, so the shape is proven even before
anything real feeds it. Second: whether a change recorded against a
conversation should really reach the next conversation started from the
same beginning, which means this work item has to introduce a "starting
point" for a conversation as a real concept, something no earlier work
item has needed — the maintainer chose to build that too rather than
leave it a stable, unfilled hole. Both choices were checked explicitly
against the one-work-item-per-commit rule before being kept together
rather than split.
