# Intent: The tether — enroll, connect, and be reachable

Author: Adam Aubry (project owner). Status: approved.

## Problem

A hosted console is being built, in a separate repository, to let a
customer manage their box from a browser. Every capability built for it so
far — approving a paused step, watching a run stream live, installing a
plugin, editing a schedule — assumes a request can already reach the box.
None of it can actually be tried, because nothing does: a customer's box
sits behind their own firewall with no port anyone outside can reach, it
has no way to introduce itself to a console, no way to prove it is the box
it claims to be, and no way to stay reachable when a network blips or the
console itself restarts. The console has nothing to point at, and the
person building it cannot demonstrate any feature end to end.

Separately, nobody can update a box, retire it, or account for everything
it holds for a customer without physically being at the machine — there is
no remote lifecycle at all.

## Proposed outcome

- A box can be pointed at a console with one setup step, and from then on
  keeps a connection to it open on its own, re-establishing it when it
  drops, without anyone watching.
- The box proves who it is with a key it generated itself, rather than
  trusting whatever answers on the other end of the connection.
- Once connected, the console can send the box work and get answers back,
  and the box can push the console updates the moment they happen — all
  over the one connection it opened.
- Someone can tell a box to update itself, to stop belonging to a console,
  or to have everything it holds for one customer accounted for and wiped
  — all remotely, none of it requiring a person at the machine.
- Because the real console has no relay to connect to yet, this work also
  ships a stand-in that speaks the same protocol, so every claim above is
  provable today rather than only on paper.
- A fresh machine can be taken from nothing to connected by following a
  written set of steps, with no undocumented manual work in between.

## Affected users and systems

- Every customer whose box will eventually enroll with the console.
- Whoever installs and operates a box (the install runbook is the
  operator's only interface to this).
- The console's own team, in the other repository — this work is the only
  real counterpart they have to build and test against until their relay
  exists.
- Every door-facing feature already built (approvals, streaming, plugins,
  schedules) — none of it is reachable from outside the box's own network
  until this ships.
- The concurrent work happening in the other lane on plugin-related door
  capabilities and documentation, which edits three of the same files this
  item eventually touches.

## Constraints

- The box never listens for inbound connections; it only ever dials out.
  This was decided before this work item existed and is not revisited here.
- Exactly one new outside dependency may be added, for the one outbound
  connection this needs; nothing else changes.
- The protocol itself — the enrollment call, the handshake, the message
  shapes — is fixed by the console's own frozen design; this box implements
  it as given, not as this item's author would design it fresh.
- The console's real relay does not exist yet, so a stand-in is required to
  prove any of this works; that stand-in is scaffolding, not a product this
  item is building for its own sake.
- A separate, concurrent piece of work is editing three files this item
  will also need to touch (a capability list and two pieces of console
  documentation). Both pieces of work run in the same window with no fixed
  deadline relative to each other; this item's edits to those three files
  are sequenced to happen only after the other work's commit has merged in,
  purely to avoid the two edits colliding.
- Verifying this against a real customer box on real staging hardware is
  its own, later piece of work. This item is complete when it is proven
  against its own stand-in relay, not against a real one.
- A box's private key must never be written to a log, printed, or included
  in any error message, under any failure.
- Whatever answers on the other end of the connection is never trusted with
  identity by default — the box still checks every credential it receives
  itself, regardless of what claims to have sent it.

## Open questions

- Where the box's own reported version number should be read from, so that
  after a remote update it can tell whether the update actually took
  effect. Left for the design stage to resolve.
- Whether the ledger's change-recording function needs a new way to wake up
  a waiter the instant a change lands, or whether polling it periodically
  is good enough. If a new wake-up mechanism is needed, it touches a file
  another, already-closed piece of work owns, and that edit must be called
  out explicitly rather than folded in quietly.

## Changed during planning

Interviewed rather than dictated on three points, though the requester
arrived with an unusually complete picture. First, whether this stays one
work item given its size (key generation, the connection itself, three
remote operations, a stand-in relay, and an install runbook) — confirmed
as one, matching the single numbered step it already occupies in the
console-fit plan. Second, that the real-hardware checkpoint is explicitly
a separate, later piece of work rather than this item's own finish line —
confirmed, narrowing this item's "done." Third, the concurrent-work
framing was corrected mid-interview: the two pieces of work run in the
same window with no real deadline between them, not one waiting on the
other by a fixed time; only the three shared files are sequenced, and for
ordering reasons alone.
