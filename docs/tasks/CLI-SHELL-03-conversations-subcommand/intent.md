# Intent: Finding a past conversation from a terminal, for real

Author: Adam Aubry (maintainer). Status: draft.

## Problem

A conversation can already be found again without knowing its exact
saved name — that ability was built, but nothing can actually reach it.
There is still no command to type. Someone who wants to see what has
been saved, or find one they half-remember, has to already know how to
write Python to call the right function themselves.

## Proposed outcome

Typing one real command with nothing after it shows every conversation
that has ever been saved, one short line each — enough to recognize
which one is wanted, not everything it contains. Typing the same command
with something remembered about a conversation — part of its name, or
something actually said in it — narrows that down to the ones that
match. Nothing about how a conversation is saved, searched for, or what
its own name is changes; this only makes what was already searchable
actually reachable from a terminal, for the first time.

## Affected users and systems

A person with terminal access to a running instance — the maintainer,
or the team, reached however a running instance is reached — and,
eventually, the agent itself, which may want to look up its own past
conversations rather than only ever having a human do it by hand.
Nothing about how a conversation actually runs is touched by this.

## Constraints

- Read-only. This never creates, changes, or deletes a saved
  conversation, or anything about one.
- Shows one short line per matching conversation, not everything it
  contains. Seeing everything a specific conversation actually holds
  still means already knowing enough to ask for it directly, elsewhere
  — this command only helps recognize which one that is.
- No filtering beyond one thing typed after the command. Nothing here
  adds a way to narrow by date, or by any other property, beyond
  matching what was already searchable.

## Changed during planning

The interview narrowed the source framing in four places. First, the
word this project uses for the thing being found was confirmed as this
codebase's own existing word for it, not the reference material's word
for the same concept — the work item's own name was corrected mid-
interview to match. Second, showing everything and finding something
specific were confirmed as one command with an optional thing typed
after it, rather than two separately-named commands, since the
underlying ability already treats "show everything" as "search for
nothing in particular." Third, what gets shown per match was pinned
down as one short recognizable line, explicitly declining to show
everything a conversation contains in the same breath — a reasonable
first instinct, but not what this work item does. Fourth, who this
serves was confirmed to include the agent itself eventually, not only a
human operator, matching how the wider effort this belongs to already
expects more than one kind of user over time.
