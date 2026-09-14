# Intent: Approval and ownership in the work-item chain

Author: Adam Aubry (product owner). Status: approved.

## Problem

Three people are about to share this build machine, each working on a
different part of one product at the same time, with one person agreeing to
all of it. The machine cannot keep either of the promises they are relying on.

Today an agent can write a plan for a piece of work and start changing the
program in the same breath. The gate that stands between the two only checks
that the plan is well formed — that it has the right headings and says
enough. It never checks that a person read it and agreed. "It was approved"
is something everybody says happened afterwards and nothing recorded at the
time.

The same gate never reads the plan's own list of the files the work will
touch. So once any plan exists at all, a piece of work may change anything in
the program. Three people working in parallel have no way to find out that one
of them wandered into another's part until it is already done and someone
notices it in a review, which is the most expensive possible moment.

And a second plan, for the product being built on top of this machine, already
refers by number to steps of a plan for this machine that nobody has written
down. The two are drifting apart before either has started, and the only cure
is to write the missing one.

## Proposed outcome

A person's agreement is recorded in the work item itself, in a word only that
person writes, and nothing that changes the program happens before the word is
there. A piece of work may touch only the files its own plan named; anything
else is refused by name, at the moment it is attempted, with a sentence saying
what to do instead. And the plan for this machine exists as a tracked
document, numbered step by step, so the other plan's references resolve to
something real and the decisions underneath both are written down once instead
of being re-argued in each of them.

The smallest version still worth having is all three at once. Without the
recorded agreement the refusal means nothing; without the file list the
agreement covers far too much; without the document the other plan keeps
citing steps that do not exist.

## Affected users and systems

Everyone who works through this machine's six-stage chain, and the one person
who agrees to their work. From the moment this lands, that person has a new
duty that cannot be delegated: write the word, in three files, per piece of
work. In exchange, the refusals stop being advice and start being refusals.

The chain's own bookkeeping tool and the guard that calls it both change. The
guard gains the program's tests, which it could be talked around until now by
writing a file from inside a shell command instead of the ordinary way.

Everything already finished is untouched: closed work items are not edited to
match the new rule, and a path outside the program and its tests is refused
nothing it was allowed yesterday.

The other product's plan is affected most of all, because the references it
already makes stop being promises about a document and start resolving to one.

## Constraints

- The word that records agreement is the person's. No agent writes it, in any
  file, for any reason — including while testing the machinery, except as
  plain data inside a test, which is the one place that cannot be mistaken for
  the real thing.
- The bookkeeping tool stays one file and keeps the four commands it has. The
  table of stages at the top of it stays the only place a stage's rules are
  tuned.
- Eight decisions are settled and are recorded rather than reopened: how a
  thing is named and identified for life; what happens to rows written before
  identity existed; the two outside libraries that may be added and where;
  which account a caller acts as; that there is one door with two ways in;
  how the store is locked; which clock is read for what; and that a box
  refuses anyone but the organisation that enrolled it.
- The machine's plan document is hand-written and tracked like source, and
  every mention of it from anywhere else has to resolve for someone who clones
  this repository fresh.
- The change applies to itself the moment it lands: this piece of work cannot
  be reviewed or committed until the person writes the word in its own three
  files. That is the mechanism working, not an obstacle to route around.
- Nothing else moves to the other repository yet. This chain stays here, in
  this language, until that product's first step takes it.

## Open questions

- What is guarded is the program and its tests. The chain's own tooling, the
  guard itself, and every document stay outside it — so this work item's
  principal file is not covered by the ownership rule it adds. Whether that set
  should widen later is not decided here, and nothing today depends on the
  answer.

## Changed during planning

The refusal sentence changed. The first framing told the writer that adding a
file to the plan "resets the status and needs re-approval" — which nothing
enforces and which would have been the machine asserting something untrue. It
now says what is true and can actually be honoured: add it and say so in your
next message, because the agreement was for the older list.

A second, genuinely enforcing option was put and declined: recording a
fingerprint of the agreed file list and refusing when it drifts. It buys
nothing against a writer who is determined — anyone able to edit one line can
edit two — and costs a person a second thing to type correctly every time. The
honest framing was accepted instead: this whole mechanism is a speed bump
against drift, not a control against intent, and what it really catches is an
agent that forgot the chain, which is the same agent that forgets to touch the
status line.

The two honour rules were merged into one entry in the project's own list of
things not to do, rather than added as two, because they are one rule.

The eleven promises and the step titles were not in this repository. They were
asked for and supplied rather than inferred; inventing them is precisely the
failure this work item exists to stop.
