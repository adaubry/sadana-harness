# Intent: CONFIG as a contract, not a module

Author: Adam Aubry (maintainer). Status: draft.

## Problem

Whoever builds the next piece of sadana-harness needs a way to read a
configuration value, and right now there isn't one. Left alone, they will
reach for whatever is easiest — usually a single settings object imported
wherever a value is needed. Every person who builds after them copies that
same shortcut, because it already works and nothing tells them not to. By the
time configuration itself gets defined properly, changing how a single value
is supplied, or turning a piece of behavior on or off, means finding and
editing every place that copied the shortcut.

This already happened once. In the system this project rebuilds from,
configuration is spread across three files in three different places, and it
is imported directly, by name, from more places than anything else in that
system — more than twice as often as the next most-depended-on concern.
There is no single place to swap where a value comes from or toggle a
behavior; every one of those import sites would need to change by hand to do
either.

## Proposed outcome

Before anything else in sadana-harness needs a configuration value, there is
one defined, documented way to ask for one — and one defined way to change
where that value comes from, or turn a behavior on or off — that does not
require editing every place the value is used. Anyone building a later piece
of this project can find and follow that way without having to guess, and
without needing to know how configuration is stored or loaded internally.

And as more of the project gets built, asking for a new value never means
growing or editing one shared file that everything else already depends on.
The way of asking for a value has to hold the same way at twenty blocks as
it does at one — nobody who adds the twentieth piece of configuration should
need to touch the same file as the person who added the first.

## Affected users and systems

Affected now: the maintainer, building this project alone. Affected later:
any collaborator who joins and builds a further piece of sadana-harness —
they inherit whatever pattern gets set here, the same way every block in the
reference system inherited its config pattern from whichever one used it
first.

Every piece of sadana-harness built after this one is a system that finds
out this changed: each one will need configuration, and each one will use
whatever contract this work item establishes.

## Constraints

CONFIG cannot depend on any other block of sadana-harness to do its job,
because none of the other purpose blocks exist here yet — it has to work
standing alone.

Storing and rotating secrets and credentials is out of scope for this work
item. Configuration needs to be loadable and readable; keeping it safe once
loaded is a separate concern for later work.

## Changed during planning

The first framing was a diagnosis of the reference system, not a problem
statement for this project — it described a fact about another codebase
rather than someone failing at something here. The interview moved it to the
failure sadana-harness will have later if this is skipped now. Scope was
confirmed as sadana-harness only: no writeup of the reference system's own
design is part of this outcome. The outcome was widened from "just the
contract" to the full CONFIG block, and two constraints were added that
weren't in the first framing — no dependency on other unbuilt blocks, and
secrets handling is explicitly out of scope. Shape was checked and kept as
one work item, with the split-or-not decision deferred to the design stage
rather than forced now.

A second round happened after design had already produced a first spec: the
first design used one shared, growing settings object, and looking at where
that leads as more blocks get built surfaced a real risk — the same shared
file becoming the next version of the sprawl this whole work item exists to
avoid, just one level down. That risk, and the requirement it implies, was
added to the proposed outcome above before any of it became code, which is
what this stage is for.
