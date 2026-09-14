# Intent: Serving the console's first screens through the door

Author: Adam Aubry (maintainer). Status: approved.

## Problem

The console's first screens are already designed: a list of a person's
conversations, an open conversation with its messages arriving live, a runs
page that reads a run, its trace and its steps and jumps back to the message
that caused it, and a page that downloads what a plugin made. None of that
has anything to read from today. The data already lives in this box's store,
and a conversation can already be turned by calling straight into the
runtime, but nothing about any of it is reachable through the one door the
console actually calls. Someone trying to build those first screens right
now hits requests the box simply does not answer yet.

## Proposed outcome

Through the door, a caller can list and open the conversations that belong
to their own account, rename or archive one, read a conversation's messages
in order, send a new message and watch it become a turn whose answer shows
up as a finished message and a finished run, list runs with what each one
cost and why it ended, jump from a run back to the message that started it,
look at a run's trace and its per-step detail (honestly empty where that
detail isn't produced yet), and list and download the files a run's plugin
left behind. Every one of those is visible with exactly the fields the
console's own screens use, scoped to the account asking and to nothing else,
and checked by a repeatable test rather than by hand once.

## Affected users and systems

The console is the only consumer of everything this item exposes, and
everyone using the console's first screens depends on this being scoped
correctly to their own account — a mistake here is one account seeing
another's conversation. The item touches no other running surface: the
terminal keeps working exactly as it does today. This item is also one of
two happening at the same time, from the same starting point, on separate
copies of the code, that get combined afterwards without either side seeing
the other's work first; that shapes how it's allowed to touch anything it
doesn't own outright.

## Constraints

- The fields, states, actions and filters this item exposes for each of the
  six things it serves are not this item's to invent — the console's own
  contract already fixed them in writing, and the job is to implement that
  contract exactly, not design a new one.
- Nothing irreversible is offered where the contract doesn't ask for it: a
  conversation can't be deleted through the door, and a plugin's output
  can't be created through it, only read and downloaded.
- A run's step-by-step detail is only as complete as the box can currently
  produce — the finer-grained record isn't built yet, so that part of the
  door answers honestly empty rather than faking completeness.
- Anything this item adds to a file the parallel item might also touch has
  to be additive only — new lines in an agreed place, never a rewrite of
  something already there — because the two sets of changes are merged
  together afterwards with neither side able to review the other's first.

## Changed during planning

The brief arrived exhaustively specified, down to exact field names,
capability strings and the exit-reason mapping the console's own contract
already fixes — none of that needed renegotiating at this level, so the
interview surfaced nothing to change about problem, outcome or affected
systems. The one real judgment call was how much of that technical detail
to carry into this document at all; the answer was to describe every
boundary in plain terms without naming the files, modules or exact field
lists that belong in spec.md instead. The intent arrived unusually well
formed, the same pattern this project saw on H19.
