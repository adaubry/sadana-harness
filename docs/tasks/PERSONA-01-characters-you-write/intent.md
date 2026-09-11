# Intent: Characters you write

Author: Adam Aubry (maintainer). Status: draft.

## Problem

Sadana has no character, and nobody using it can tell that this is
something they are allowed to change. It answers in one voice that was
written once, by us, in a paragraph nobody chose, and the only way to
change that voice is to know that a particular file exists somewhere
under a configuration directory and to open it in an editor. Nothing
tells you the file is there. Nothing tells you what is in it right now.
There is no way to keep two voices and use one of them, no way to hand
the voice you like to someone else, and no way to come back a month
later and see which one is speaking.

For a person meeting an assistant for the first time, how it talks to
them is the first thing they want to change and the first thing they
judge it by. Right now the honest answer to "can I make it talk
differently" is "yes, if you already know where we hid it."

## Proposed outcome

A person can give the assistant a character, by name, and get it back.

They write a character: a name, a line describing it in their own words,
and the voice itself — the tone it takes and the style it answers in.
They can write several. They can list what they have written and tell
them apart from the descriptions alone. They can choose one, and from
then on the assistant speaks that way wherever it speaks to them — in
conversation, in a reply it sends back through a webhook, in something a
schedule started while they were asleep, and inside any procedure a
plugin runs on their behalf. They can choose nothing, and get a plain,
neutral voice that is not pretending to be anyone.

The character belongs to the person, not to a single conversation and
not to the installation. A conversation that has already started keeps
the voice it started with, for its whole life, no matter what is changed
underneath it; a new voice is something the next conversation gets. A
person who opens an old conversation months later hears what they heard
at the time.

Sadana ships no characters. It ships one neutral voice and the means to
write your own. The smallest version still worth having is: write one
character, select it, hear it, and see which one is active.

## Affected users and systems

Anyone who runs sadana. The problem is not specific to this project's
maintainer — it belongs to whoever installs it later and wants it to
sound like something other than our default paragraph.

What finds out this changed: every surface that speaks. Typed
conversation, replies the gateway sends back out, anything a schedule
triggers, and the work plugins do in the agent's name — all four must
speak in the chosen voice, because the character belongs to the person
and not to the doorway. Conversations themselves are affected, because
each one now has to remember the character it was started with, and
conversations recorded before this change have to keep opening and keep
sounding exactly as they do today. Whoever sets sadana up for the first
time is affected, because the first run no longer quietly writes a
persona file on their behalf.

## Constraints

A conversation's voice cannot change while it is alive. This is not a
preference — the system already guarantees that what frames a
conversation stays byte-for-byte identical from its first turn to its
last, with exactly one documented exception, and a character switch is
not that exception.

The voice is authored, never generated. Sadana does not invent a
character, suggest one, or improve the one you wrote.

Characters can be written as files a person owns and keeps in version
control, and managed through commands; both ways are wanted, and the two
must never be able to disagree about what a character says. The cost of
two front doors was put to the user during the interview and accepted
deliberately.

Names are how a character is addressed, everywhere and forever: in what
a person types, in what a conversation records about itself, and in what
gets shown back. Nothing long-lived stores a copy of the voice text in
place of the name.

Existing conversations must keep loading. Anything recorded before this
change is read back as it always was.

This work item declines everything about hermes's version of this that
is not the voice: the per-character terminal colours and skins, the tips
ticker, the goals system, the virtual pet and its image pipeline, and
the achievements dashboard — roughly six thousand lines of decoration
that never reaches the model. It also declines shipping a catalogue of
ready-made characters, per-conversation overrides, and any mid-flight
switch.

## Open questions

Should a character be shareable the way a plugin is — installed from
somewhere, rather than only written locally and copied by hand? Nobody
needs it yet; the user can answer it when someone asks.

How far a character is allowed to reach is unresolved. A voice that only
changes tone is safe; a character that tells the assistant to act
differently — to stop asking before it does things, for instance —
overlaps what the rest of the system decides. Where that line sits is
the user's call, and design should surface it rather than assume it.

## Changed during planning

The interview cut this item roughly in half twice and then made it
smaller a third time. It arrived as hermes's whole persona block —
twenty-nine files and about nine thousand lines, most of it terminal
decoration with a virtual pet in it — and the surface half was dropped
outright once it was laid out next to the one-work-item-one-commit rule,
leaving only what actually reaches the model.

The shipped catalogue went to zero. Hermes ships fifteen built-in
characters, most of them jokes, and the first framing assumed sadana
would ship some equivalent set. The user chose to ship none: one neutral
voice and the means to write your own. That is the paradigm statement
this item was meant to make, and it is a subtraction rather than an
addition — a character here is authored by a person, in the same way a
plugin is, not picked off a list we wrote.

The existing persona file was going to be preserved and quietly turned
into a character, on the grounds that nobody's edits should be lost. The
user chose a clean break instead: it stops being read, and its contents
have to be re-entered as a character. That is only cheap because the
project has one user today, and it is worth writing down that the choice
was made knowing the cost.

Binding moved. The first framing had the character chosen when a
conversation starts; the user put it on the person instead, so it
follows them across every surface, and kept the conversation stable —
which turned what looked like a preference into the hard constraint that
a live conversation never changes voice.

Two options were taken with their costs stated in front of them rather
than by default: characters authored both as files and through commands,
which creates two things that can disagree, and a character carrying a
description and separate tone and style rather than one paragraph of
text. Both are recorded here as accepted costs, not as things that went
unexamined.
