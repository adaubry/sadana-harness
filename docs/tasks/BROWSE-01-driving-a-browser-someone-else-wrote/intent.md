# Intent: Driving a browser someone else wrote

Author: adam aubry (owner). Status: approved 2026-09-11.

## Problem

The agent can fetch a page's address and a sentence about it. It cannot look
at the page.

Searching returns a list of titles and descriptions written by whoever
published each site. Anything that requires reading the page itself — what a
form asks for, what a table actually says, what happened after clicking
something, whether a claim in a search result is borne out — is out of reach.
The agent reports what the internet says about a page rather than what the
page says.

The obvious next step is fetching the page and reading it, and that does not
work for most of the web any more. A great many pages are a small amount of
markup and a program that builds the rest once a browser runs it. Fetching
gives you the shell. Reading it properly means running a browser.

Running a browser is not something this project can reasonably write. It is
years of work and there is software that already does it. Which means the real
question is not "how do we browse" but "are we willing to run somebody else's
program to do it" — and that question has been deferred at every step of this
plan, deliberately, until it became the only thing left.

## Proposed outcome

A person can ask for something that requires looking at a page, and get an
answer drawn from what the page actually showed.

The thing that does the browsing is entered once, with a goal in plain
language, and comes back once with what it found. It is not a set of
instructions the agent issues one at a time — it is one step of a procedure,
the same shape as everything else here.

Whatever gets installed to make that work, and what it is allowed to reach, is
written down before anything is installed rather than discovered afterwards.

The person is told what running this costs them: the software it needs, the
browser it needs, and that it sends what it sees to a model in order to decide
what to do next.

The smallest version still worth having: one goal in, one report back, no
memory of previous browsing, no interaction with the person part-way through.

## Affected users and systems

The person running this project, on whose machine a browser and a piece of
third-party software would be installed and run, under their own account, with
their own access.

The plan for what this project imports, which has one item left and this is
it.

The way a plugin reaches outside, which has just gained the ability to run a
program and has never been pointed at anything that was not written here.

Whatever the browser is asked to look at, since a page is chosen by the agent
and read by software that decides for itself what to do next.

## Constraints

The thing that browses is run as a program, not imported. Nothing new is
added to what this project itself depends on.

It is entered by goal and exited once, never driven step by step. A plugin is
a procedure, and a procedure does not click buttons one at a time.

What it is allowed to see is decided deliberately: it gets what it needs and
not the environment this project runs in.

Whether it can be trusted is not settled by the fact that it works. This is
software this project did not write, reading pages this project did not
choose, and deciding what to do next on its own.

## Open questions

**Is running third-party software on this machine acceptable, and under what
terms?** **Answered by the owner, 2026-09-11: yes, proceed.** Recorded rather
than removed, because the reasoning is what a later reader needs and because
the same question returns for every import after this one. The honest position is that the tool would run with the person's own
access, on their own files, and nothing built so far contains it — the
program-running facility says plainly that it is not a boundary. The options
are: accept that, on the grounds that it is the same trust already extended to
anything else installed on the machine; require isolation first, which means
either another piece of software or a dependency and would come before this;
or decide the capability is not worth the exposure and close this item
unbuilt, which would have been a legitimate answer and would have closed the
plan at five of six. The first was chosen: the tool runs with the same access
the person already grants everything else on their machine.

**What has to be installed, and is that acceptable?** The browsing software
itself, and a browser for it to drive — neither is present on this machine
today. That is a real download and a real thing running locally.

**What does it use to decide what to do?** It drives a model of its own to
choose each step, which means a key and a per-use cost, and it means the pages
it reads are sent somewhere. That is a second exposure and it is not the same
one as the first.

## Changed during planning

The problem statement changed from a capability to a question, and that is the
substance of what this stage produced.

The first framing was "add browsing", with the third-party software as an
implementation detail to be chosen at design time. Reading what the remaining
work actually consists of made clear that the implementation is the easy half —
the facility to run a program exists, the shape of a plugin that enters once
and exits once is settled, and the reference has already shown this exact
collapse from thirteen ways of poking at a browser to one way of stating a
goal. What is unsettled is whether to run it at all.

So this intent deliberately stops short of proposing a design, and is written
to be readable by somebody deciding whether to proceed rather than somebody
building. If the answer is no, the work item closes here having cost nothing,
and that is a real outcome rather than a failure.

Two things came out of scope while writing it. Keeping a browser open between
requests, and letting the person intervene mid-task, both require a plugin that
can be driven rather than entered — which the shape of a plugin here does not
permit, by an earlier decision that this item is not reopening.
