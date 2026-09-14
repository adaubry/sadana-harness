# Intent: The door — one framework for every future console request

Author: Adam Aubry (maintainer). Status: approved.

## Problem

Everything a person can do on this box today happens through a CLI handler
that opens a store and prints to a terminal. That works for the one person
sitting at a keyboard. It does not work for a browser-based console: a
browser cannot type into a CLI, and nothing on the box today answers a typed
request the same way twice, refuses what it should refuse with an error a
program can act on, or can tell a retried request from a duplicate one. Every
future console feature — reading a conversation, approving a paused run,
installing a plugin — needs a foundation that already handles all of that,
and today there is not one.

## Proposed outcome

A box exposes one entry point that answers requests from anything holding a
token the console issued: it can list, read, create, update, remove, and act
on the things the box manages, in one consistent shape, with errors a program
can distinguish from one another, without ever repeating a request's effect
if that request is retried, and with a way to check on anything that takes
too long to finish immediately. The entry point is provable without a real
network connection, so its correctness can be checked on every change to the
box's code, not only when someone remembers to test it by hand.

When this item is done, the box can answer, through this entry point, for
one thing it manages: itself — its own version, health, and capabilities.
Everything else the box manages gets wired in one at a time, in later, separate
work.

## Affected users and systems

Not just the person at this box's own terminal. Every future console
feature — approvals, plugin management, conversation history, run
monitoring — is a client of this entry point, and each is its own later work
item that only has to add its own piece of what the box manages, not rebuild
how requests are made, authenticated, paged, or retried. Once the hosted
console is live, every organisation's own box carries this same entry point,
so a box's owner — who may not be technical — is also affected: a defect here
surfaces as broken behaviour in someone else's browser, not as a stack trace
on this box's own terminal. This item touches no other running system: it
adds a new way in, and the CLI keeps working exactly as it does today.

## Constraints

- The shape of the entry point — which requests it accepts, how paging and
  filtering work, the shape of an error, how it verifies who is asking — is
  not this item's to invent. The console side of this project has already
  fixed that contract in writing, in a document outside this repository, and
  this item's job is to implement it exactly, not to design a new one.
- The token that proves who is asking must be verified with a well-reviewed,
  existing signing library, not a hand-rolled verifier, however simple the
  actual checks seem — the class of bug that comes from hand-rolling this is
  exactly the kind that would defeat the whole reason a door with a token
  exists.
- This item wires in the box's own status as the first, and only, thing the
  door can talk about. Everything else the box manages — conversations,
  plugins, schedules, secrets, and so on — is deliberately left for later,
  separately scoped work.
- The way in stays loopback-only for now. There is no real network path from
  a hosted console to a box yet, and this item does not build one.

## Changed during planning

Nothing substantive changed. The brief was checked against the live
repository — an existing but unused test category set aside for exactly this
kind of contract check, the exact dependency list, the loopback-listener
precedent this project already has, and every decision this project's
cross-repository console plan already records — and it matched in every
case. The two genuine judgment calls put to
the author were sizing (kept as one work item, since a partially built door
is not a usable contract) and how to record the handful of danger codes this
repository cannot itself verify, coming from the console's own frozen plan in
a sibling repository (recorded verbatim, on trust). The intent arrived
unusually well formed.
