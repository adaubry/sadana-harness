# Intent: Asking a model for a real response never requires trusting an unkept promise

Author: Adam Aubry (maintainer). Status: draft.

## Problem

Earlier work already wrote code that assumes a tool call arriving from a
model always has a real id and already-usable arguments — but nothing in
this project actually guarantees that yet. The part that talks to a real
provider hands back whatever the API sent, arguments still as raw text,
sometimes with no id at all, and stops after exactly one try even when the
failure was just a hiccup worth retrying. Whoever builds the part that
runs a full turn next would either have to fix all of that themselves,
in the middle of running a turn, or trust a promise nothing has kept yet.

## Proposed outcome

Asking a model for a response becomes one thing a caller can do without
having to already trust anything about what a real provider sends back.
Every action the model asks for always arrives with a usable name, a real
id, and arguments already in a usable shape — never raw text a caller has
to parse or guess at. A hiccup worth retrying is retried before the
caller ever sees it. And only two things can ever go wrong from the
caller's point of view — the request was too big, or the attempt failed
outright — instead of the several different shapes a result can currently
take.

## Affected users and systems

Only the maintainer, for now. The direct beneficiary is whoever next
builds the part of this project that runs a full conversation turn — it
gets to assume everything this work item promises rather than guarding
against everything it doesn't. This also closes a gap two already-closed
work items left open: both wrote code that already assumed this promise
was kept.

## Constraints

- Provable entirely on its own — no live conversation, turn loop, or
  history exists yet, and none of this needs any of them to be tested.
- Does not change how a request actually reaches a provider, or any
  provider's own code — only what happens to what comes back.
- Switching to a different provider mid-attempt, streaming a response as
  it arrives, and anything about resuming an interrupted attempt are all
  declined — none of them exist anywhere in this project yet, and none of
  this work item's job description calls for them.
- Whatever a caller eventually needs to say about how big or how long an
  individual attempt is allowed to be is not decided here — this work
  item does not invent that shape, only leaves room for it.

## Changed during planning

The interview surfaced that two already-closed work items had already
written code assuming this work item's promise — a real id, already-usable
arguments — was already kept. It wasn't; this narrowed the Problem from a
generic "make talking to a model easier" framing into specifically closing
that gap. The interview also settled, deliberately against this project's
usual default of deferring anything without a current reader, that the
robustness machinery for malformed arguments and inconsistent ids should
be built in full now rather than only when a misbehaving provider is
actually wired — a considered exception, not an oversight.
