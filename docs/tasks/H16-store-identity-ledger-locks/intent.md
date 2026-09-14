# Intent: Identity, a change log, and room for two people in the store

Author: Adam Aubry (product owner). Status: approved.

Contract impact: none in the console; defines the row every door response is
built from.
Dangers covered: P1, P3, P8; B3, B10, C8, A5.
Reopen cost: H19 onward, every noun.

## Problem

A console is being built that will sit far away from this box and show a
person what the box holds. To do that it has to be able to say four things
about any one item: what it is called, when it appeared, when it last changed,
and whether it is still there. It also has to be able to catch up — a console
that was offline for a week must be able to ask what happened while it was
away, and get an answer that does not require re-reading everything.

The box cannot answer any of that.

A saved conversation has a name and nothing else. It does not know when it was
started, when it was last touched, how many times it has been changed, or
whether it is still open. The same is true of everything else the box keeps:
what it remembers about a person, which voice each person picked, which
plugins are installed, what past runs cost. Some of those carry a single
timestamp, most carry none. None of them carries an identity that stays the
same when the name changes, and none of them has a number that goes up when
the thing is edited.

Worse, nothing anywhere writes down that a change happened. A row is
overwritten and the previous state is simply gone; a row is removed and
nothing is left behind to say it ever existed. An observer that holds a copy —
which is exactly what the console will be — has no way to notice it has
drifted, and no way to repair the drift once it has. Its copy silently stops
matching the box and its pages start showing things that are no longer true.

And the box can only talk to one person at a time. Every write anywhere in the
process waits behind a single lock, and that lock is held for the entire time
it takes the model to answer — which can be a minute. So while one person is
in the middle of a sentence, a second person's message waits, and so does
something as small as asking for the list of conversations. That is fine for
one person sitting at a terminal, which is the only situation the box has ever
been in. It is wrong the moment an organisation's members share one box, which
is what the console makes normal.

Each of these three is a decision that was right when it was made. None of
them is right for what comes next, and all three have to be fixed underneath,
in the place where things are kept, before anything is built on top. A door
that serves rows the box cannot vouch for would just be a faster way to be
wrong.

## Proposed outcome

Everything the box keeps that a person could later point at can answer the
same four questions. It has an identity of its own that never changes, even
when it is renamed. It knows when it came into being and when it was last
touched, in ordinary clock time that two different machines can compare. It
carries a number that goes up every time it is changed, so an observer can
tell a stale copy from a current one. And it carries a word saying what state
it is in, drawn from a short written-down list rather than invented per case.

Two kinds of thing that live as files rather than as rows — the written
characters the assistant can speak as, and the installed plugins — get an
entry apiece that keeps pace with the files themselves: a new file appears as
a new entry, an edited file bumps its number, a deleted file leaves a marker
saying it went. Anything a plugin run produced gets an entry too, so what a
run made can be listed without walking the disk.

Alongside all of that the box keeps a running log of changes. Every change to
anything, as it happens, in order, each with a place-marker. An observer that
has been away asks for everything after the marker it last saw and gets
exactly that. An observer that has been away too long, or that has lost its
place entirely, asks instead for the whole list of what exists right now, with
each thing's last-changed time and change-count, and repairs itself by
comparing. Nothing is ever removed without leaving a marker behind saying it
was removed, so a disappearance is something an observer learns about rather
than something it has to infer from absence. Forgetting something the box
remembers about a person becomes a change of state rather than an erasure, for
the same reason: an observer has to be told.

And two people can use the box at once. A conversation, not the whole box, is
what someone has to wait for; two people in two different conversations get
answers at the same time, and anybody merely reading — listing, looking, not
changing anything — never waits for a conversation at all. Two people in the
same conversation still take turns, which is correct.

The smallest version worth having is all three together. Identity without the
change log gives an observer rows it can name but no way to learn they moved.
The change log without identity has nothing stable to point at. And both of
them, on a box that can only serve one person at a time, describe a fleet the
box cannot actually host.

Nothing here is visible yet from outside the box: there is still no way in
from the network, and that is the next piece of work, not this one.

## Affected users and systems

Nobody using the box today sees a difference. The terminal behaves exactly as
it does now, and so does the scheduled work that fires on its own and the
channel that answers messages. That is the test of whether this was done
correctly.

Everything the box keeps is touched, because the point is that they all answer
alike: conversations and the messages in them, what is remembered about a
person, each person's own adjustment to how remembering works, which voice
each person picked, which plugins are registered and installed, the record of
past turns and past plugin runs, and the record of what was submitted to the
marketplace and what was decided about it. Each gains the same handful of
fields and each of their writing points starts recording what it did.

Three kinds of thing that had no entry at all get one: the written characters,
the installed plugins, and what a plugin run produced.

Existing stored data is kept, not rebuilt. A row written before any of this
existed keeps working. It is given an identity and a pair of timestamps the
first time the box opens after this lands — and the time it is given is the
moment it was filled in, not the moment it was created, which is written down
everywhere that time is shown so nobody mistakes one for the other.

The console being built elsewhere is the reason this exists, and every later
piece of work in that chain is built on the shape settled here.

## Constraints

- No existing field is renamed and nothing kept is thrown away. This adds; it
  does not rearrange.
- The part of the box that reasons about a conversation without touching a
  disk stays exactly as it is. Identities and times are stamped on at the
  moment something is written down, never carried around in the values the
  rest of the program passes between its own parts.
- Identities are made here, with nothing installed to do it. They sort by the
  moment they were made, so a list in identity order is a list in time order,
  and two made in the same millisecond still sort against each other.
  Each carries a short word in front saying what kind of thing it names, drawn
  from a fixed list that is checked when the program starts rather than when
  the mistake is made.
- The times recorded are ordinary clock times, because a clock time is the
  only kind two machines can compare. The stopwatch the box already uses to
  decide when a turn has run too long stays a stopwatch, and the two are never
  confused for each other.
- A change is only ever logged together with the change it describes, in one
  indivisible step. If the change does not stick, neither does the log entry.
  There is no path where one happens without the other.
- Filling in the old rows is a separate thing from logging changes, and writes
  nothing to the log. An observer meeting this box for the first time reads
  the whole inventory anyway, so writing thousands of entries about rows that
  did not really change would be noise standing in for news.
- The list of state words and the list of thing-kinds are each written down in
  one place and closed. Adding to either is a piece of work, not a field
  somebody types.
- The change log is written to by everything that keeps things, and knows
  about none of them.
- One person still cannot have two turns at once in the same conversation, and
  a plugin that stops to ask a person for approval still holds up that
  conversation until it is answered. Both are known, both are named out loud,
  and the second is removed by a later piece of work rather than this one.

## Open questions

- Several of the new state words have no way to be set yet. Nothing can mark a
  conversation closed, and nothing can switch a plugin off, until the door and
  the plugin work land. The fields and the rules that read them are built now
  anyway, deliberately, so the later work adds only the verb and not the
  enforcement — but until then they have one value in practice.
- What a purge looks like — being able to enumerate and remove everything held
  for one person — is promised by the console's plan and is not answered here.
  The log and the inventory are what will make it answerable; the verb itself
  is later work, and nothing today depends on which shape it takes.

## Changed during planning

Four things moved, and one turned up.

The four ids naming the risks this work is meant to head off were cited from a
plan kept in another repository that this box cannot read. Rather than let the
intent carry four opaque references, they were asked for and written out in
plain words: a console whose every screen assumes it can sort and page
anything; a remote copy that quietly stops matching the box; a deletion that
an observer can never learn about; and two people on one box colliding. Each
now has a sentence in this document saying what it is, which is what makes the
cross-reference worth having at all.

The scope was challenged and deliberately kept whole. This is three changes
that could each ship on their own, and the project's own rule is to split when
that is true. It was put and declined, with a reason: the change log is
meaningless without the identity to point at, and the identity is not safe to
serve from a box that can only answer one person at a time. Three commits
would each be individually green and collectively unusable until the last.

The check that proves two people can use the box at once was rewritten. As
first stated it measured a stopwatch and required two turns to finish inside a
window — which is the exact shape the project's own testing rules forbid,
because it goes red on a busy machine for reasons that have nothing to do with
the code. It now proves the thing directly instead: both turns must be
observed to be genuinely in flight at the same moment, and that is the part
that has to hold. The stopwatch figures are still measured and still reported,
because they are what a person reads to believe it — they are just no longer
what the check depends on.

The switch that hides a turned-off plugin was questioned as building for a
need nobody has yet, and kept on purpose after being put: the fields that make
it possible are written here, so the later work that adds the way to turn a
plugin off adds only that, and cannot forget the hiding.

What turned up: the clock already handed to the part of the box that writes
conversations down is a stopwatch, not a wall clock — it exists to answer how
much time a turn has left, and its numbers mean nothing to another machine.
Every time recorded by this work has to be read fresh from the wall clock at
the moment of writing. That was not obvious from the outside and would have
produced timestamps that looked fine, sorted correctly, and were meaningless
the moment anyone compared two boxes.
