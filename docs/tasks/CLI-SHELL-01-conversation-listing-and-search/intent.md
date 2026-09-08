# Intent: A saved conversation can be found again without already knowing its exact name

Author: Adam Aubry (maintainer). Status: draft.

## Problem

A conversation now survives the process that was running it, but the only
way to get one back is to already know the exact name it was saved under.
If you don't remember that exact name — or never wrote it down anywhere
else — the conversation is effectively invisible: there is no way to see
what has ever been saved, and no way to find one by something you actually
remember about it, like what it was about or what was said in it.

## Proposed outcome

A person, or the agent acting on their behalf, can see every conversation
that has ever been saved, and can find a specific one back by searching for
something they remember about it — part of its name, or something that was
actually said in it — without needing its exact saved name up front. The
search does not care about letter case. Nothing about how a conversation is
saved, or loaded back by its exact name, changes.

## Affected users and systems

Everyone who will eventually use sadana-harness, and the agent itself —
both will want to find a past conversation again without carrying its
exact saved name around from elsewhere. For now, only the maintainer, and
only a test, actually exercises this: this work item does not make it a
callable command yet. A directly-invokable command that a person or the
agent can actually run is a separate, later work item in the same
larger effort — the one that gives this a real caller for the first time.

## Constraints

- Nothing about how a conversation is created, saved, or loaded back by
  its exact name changes. This work item only adds a way to find one
  without already knowing that name.
- Matching does not care about letter case.
- This is not wired to anything that calls it in production yet — no
  registered command, no menu entry, no other caller. A test is the only
  thing that drives it in this work item.
- Finding a conversation this way hands back the same full thing loading
  it by name would — not a stripped-down summary that then has to be
  loaded again to actually see it.

## Changed during planning

The interview narrowed the source framing in four places. First, "session
listing and search finally have a caller" turned out to mean a store-level
way to find conversations, not an actual runnable command — a real
command a person or the agent invokes directly is confirmed as its own,
later work item in this block, so "nothing can drive this except a test"
means no wiring at all yet, not merely an unregistered command. Second,
listing and searching were confirmed to land together in one commit rather
than splitting search into a follow-up, since the source material named
them as one deferred half. Third, search was confirmed to cover both a
conversation's own name and what was actually said in it, in one
operation, rather than two separate lookups. Fourth, finding a conversation
was confirmed to hand back the same full thing a caller already gets from
loading it by name, rather than a lighter summary row — keeping one shape
for "here is a conversation" instead of two. Letter-case sensitivity was
pinned as the one real constraint; the finer points of how a match is
made are deliberately left open for the design stage to judge.

A later check asked directly whether this work item covered everything the
wider effort it's named after actually needs. It did not, by design: a full
read of that wider territory (recorded separately, since it names things
this document deliberately doesn't) confirmed this item is correctly the
first, narrowest slice of a longer sequence — not an accidentally
incomplete guess at the whole thing. Nothing above changed as a result;
what changed is that "a separate, later work item" is now a known, ordered
fact rather than an assumption.
