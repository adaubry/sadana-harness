# Intent: Nothing deletes what plugins make

Author: adam aubry (owner). Status: draft.

## Problem

Plugins now write files and nothing ever removes them.

This is not a prediction. Proving the picture-drawing plugin worked took three
runs, and three files of roughly 1.3 megabytes are still on disk with no way
to find them except knowing where to look, and no way to remove them except
knowing that too. Every one of those was a test of the same thing. A person
iterating on a description until the picture is right does exactly that, and
each attempt is kept forever.

The cost has been deferred three times, each time honestly and each time on
the grounds that nothing was writing files yet. Something is now.

What makes it worse than ordinary clutter is that nobody is watching. The
files land under the directory this program keeps its own state in, which a
person has no reason to look inside. There is no report of how much is there,
no warning as it grows, and the first signal will be a disk filling up for
reasons that are not obvious from anywhere.

## Proposed outcome

Old things a plugin made stop accumulating without anybody having to think
about it.

A person can find out how much is being kept and where, without knowing the
layout in advance.

Whatever rule decides what goes is one a person can state in a sentence and
change if they disagree with it.

Nothing that is still being referred to disappears underneath someone, and
nothing disappears silently enough that a person wonders whether it was ever
made.

The smallest version still worth having is genuinely undecided and is the
substance of this work item — see the open question. It may be as small as a
command that clears everything on request.

## Affected users and systems

The person running this project, whose disk this is.

The place plugin output is kept, which gains a lifetime for the first time.

Any plugin that produces a file — currently one, shortly more, since moving
pictures and speech are both scheduled and both produce larger files than
still pictures do.

The record of what a plugin run produced, which names files by path. Whatever
removes a file makes those references stale, and how that is handled is part
of this.

## Constraints

Nothing is removed that a conversation still refers to, unless the person
asked for exactly that.

A rule about what to keep is a behaviour, so it is something a person can see
and change, not a number compiled into the program.

Removing things is the kind of action that cannot be taken back, so whatever
does it says what it did.

This is not a general storage system. It governs what plugins produce, not
conversations, not settings, not the database.

## Open questions

Which rule, and this is the whole question. A cap on total size with the
oldest going first is predictable and needs somebody to pick a number. An age
limit is easy to explain and throws away something you wanted last month. A
command that clears everything on request is the smallest honest thing and
leaves the problem with the person. The right answer depends on what a month
of real use looks like, which is why this was not decided when the cost was
created, and it needs a month of real use before it is decided now.

## Changed during planning

This intent exists because three previous work items each recorded the same
deferral and the last one made it cost something real. That history is the
argument: nothing here is a new discovery, and the only thing that changed is
that the files are now on disk rather than hypothetical.

The framing changed once. The first version was "add a retention policy",
which assumes the answer is a policy. It might be a command. Writing it as
"things accumulate and nobody is watching" left room for the smallest answer
to win, and the open question now names three candidates instead of assuming
one.
