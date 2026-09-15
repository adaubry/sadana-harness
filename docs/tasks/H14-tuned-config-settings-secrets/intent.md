# Intent: Tuned — config, settings and secrets by reference

Author: Adam Aubry (project owner). Status: approved.

## Problem

Nobody outside this box can change how it behaves without shell access to
the machine it runs on. Which model it calls, its budgets, where its
webhook binds — all of it is an environment variable today, and the only
way to change one is to edit it on disk and restart the process. For a box
that is meant to be one of many in a fleet, dialed into a console over a
tether, that restart is not a minor inconvenience: it drops the tether,
kills whatever turn is in flight, and makes the box look offline to the
console for as long as it takes to come back up. A routine settings change
and an outage look identical from the console's side.

Credentials are worse than inconvenient — they are unreachable. There is no
channel by which the box can be handed a credential at all; the only way a
key gets onto the box is a person editing a file on that machine by hand.
That is not a rough edge, it is a missing capability: the console's own
settings-and-credentials surface (its own artifact A20) cannot be built at
all until the box can accept a credential over the wire, and its plugins
surface (artifact A23) cannot let a plugin's own settings be set the same
way. And once a credential is on the box, nobody can tell which one it is
without reading the file — there is no way to distinguish a stale key from
a current one before deciding whether to rotate it.

## Proposed outcome

A person or the console can change the box's behavior through the door, and
it takes effect on the next turn — no restart, no dropped tether, no
interruption to a turn already running. The environment continues to win
over whatever is on disk, exactly as it does today, so nothing that already
depends on an environment override (automated tests, CI) changes behavior.

A credential's value can be handed to the box exactly once, through a
channel built for that purpose, and from that moment on nothing that
reaches outside the process — a log line, an API response, an error
message — ever shows that value again. Anyone with the right access can
still tell which credential the box is holding: not its value, but enough
of a fingerprint to recognize it and decide whether it needs rotating.

## Affected users and systems

The console is the party actually blocked today, specifically its
settings-and-credentials surface (artifact A20) and its plugins surface
(artifact A23) — both need these shapes to exist before they can be built,
and their own acceptance tests are what will demonstrate this box got the
shapes right.

Everything already reading one of these values today — the call to the
model provider, the gateway and webhook commands, a plugin reading its own
setting — keeps working unchanged; only where the value comes from moves.
Anything that sets one of these today through the real process environment,
including CI and the test suite, is unaffected, because the environment
still wins.

## Constraints

- The process environment always wins over whatever is on disk — nothing
  that depends on an environment override today may start behaving
  differently.
- No path the box exposes for reading these values — no API response, no
  log line — ever carries a secret's actual value; at most a fingerprint of
  it.
- The credential file that already exists on disk remains the credential
  store; this does not introduce a different place credentials live.
- Every command-line flag that already sets one of these values keeps
  working exactly as it does today.
- No interactive wizard for creating the behavior-settings file — it comes
  into being by having a value written to it, not by a guided setup flow.
- No enforced rotation schedule for a credential — only the ability to tell
  a stale one from a current one before a person decides to rotate it.
- No migration of every environment-configurable value this box has today —
  only the specific behaviors and secrets this item names move; everything
  else keeps reading the environment the way it does now, unless and until
  a later item moves it.

## Changed during planning

The brief arrived already specifying implementation shapes: names of
functions and files, table layouts, wire schemas. Those were set aside for
the design stage; this document was rebuilt from them at the problem and
outcome level, which the plan-skill template requires and the original
brief skipped entirely. Two substantive corrections came out of the
interview rather than the initial framing: a status field in the box's own
status response was headed for a type change that would have collided with
another work item's concurrent, in-flight rewrite of the neighboring field
next to it — caught only by asking who else holds that shape right now, not
by anything in the brief itself — and resolved by adding a sibling field
instead of repurposing or nesting into either existing one. Separately,
"readable by anything that can read the process environment" was the
framing offered first for the secrets problem; the interview surfaced that
the sharper and more consequential fact is that the box cannot accept a
credential through any channel at all today, which is what actually blocks
one of the console's own features — that became the lead of the problem
statement rather than a trailing clause. A claimed citation was also
corrected during planning: two ids in the original brief were assumed to
name rows in the console's own pre-mortem register, but they turned out to
be a different console artifact's ids entirely — the original wording would
have sent a later reader looking for register rows that do not exist.
