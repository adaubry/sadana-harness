# Intent: door nouns for agents and memory

Author: Adam Aubry (product owner). Status: approved.

Contract impact: none in the grammar; serves four console nouns.
Dangers covered: A6.
Reopen cost: the console's A19 evidence.

## Problem

The console already has screens waiting for four things it cannot yet get an
answer about: which voice the assistant is speaking with for a given account,
a ready-made starting point for writing a new voice, which voice is the
account's default, and what has been remembered about the person it is
talking to. Every one of those requests reaches the box today and comes back
"not found," not because the information doesn't exist — voices are files on
disk, memories are rows in a database — but because nothing translates either
one into the shape the console asks for.

A person setting up their assistant cannot see, from the console, which
voice is active, cannot start a new one from a template instead of a blank
page, cannot make a voice the default without dropping to a terminal, and
cannot review or correct what has been remembered about them at all.

## Proposed outcome

From the console, an account can: see every voice available to it and which
one is its default; start a new voice from a built-in template; switch a
voice from a draft to something usable, and make it the default; browse
everything remembered about the people it has talked with, see whether each
remembered thing is a decision, a preference, or a plain fact, and which
conversation it came from; stop a remembered thing from being used without
erasing the record of it; and see and change the rule that decides what gets
remembered for its account.

None of this changes what gets captured or how — the assistant already
decides what to remember, in prose, against a rule the account can already
adjust. This only makes the result of that visible and manageable from the
console, the same way the console can already see and act on other things
the box does.

Deleting a voice outright is not part of this — the console will be told,
honestly, that the box cannot do that yet, rather than being given a stub
that pretends to.

## Affected users and systems

- Console operators managing an assistant's voice and reviewing what it has
  remembered about the people it talks to.
- The console itself, which gets real answers instead of 404s for four of
  its screens.
- Nobody using the existing terminal commands for voices or memory is
  affected — those keep working exactly as they do today; the console is a
  second way to reach the same underlying files and rows, not a replacement
  for the first.
- The one place the assistant already writes something to memory mid-
  conversation gains two small labels on what it writes — what kind of thing
  it is, and which conversation it came from — so the console can show that
  provenance. Nothing about when or whether it writes changes.

## Constraints

- A voice already in use by a conversation that has started never changes
  under that conversation; only conversations created after a voice is
  edited see the new version. This work cannot and does not touch that.
- A voice stays a file the person owns, not something this turns into a
  database row with its full text duplicated into it.
- What's remembered about one account is never visible to another account,
  through the console or otherwise.
- This does not introduce a new scheme for classifying what gets remembered.
  The account's own rule for what's worth remembering, in prose, stays the
  only thing that decides that; the label this adds afterwards is a
  description of what was already written, not a new set of buckets the
  assistant is asked to sort into.

## Changed during planning

Two things were narrowed in interview. First, the "kind" label on a
remembered entry (decision, preference, or fact) was confirmed as a
cosmetic, after-the-fact description for the console to filter and show —
not a reopening of the earlier, deliberate decision to have one kind of
memory governed by a customizable rule instead of a fixed taxonomy; that
decision stands, and this only labels its output. Second, the draft
description of where a remembered entry "came from" originally named two
sources, one for the assistant noticing something mid-conversation and one
for a person adding something directly through the console. Only the first
of those exists anywhere in this codebase today — there is no second way to
write a memory entry yet — so the outcome was narrowed to describe the one
real source rather than reserve a value for a path nothing builds.
