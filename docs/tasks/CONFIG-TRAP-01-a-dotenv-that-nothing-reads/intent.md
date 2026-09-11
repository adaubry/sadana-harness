# Intent: A dotenv that nothing reads

Author: adam aubry (owner). Status: draft.

## Problem

There is a file in the project directory that looks exactly like the place you
put your keys, and nothing in this program reads it.

The program reads one such file, kept with its own state, well away from the
project. The one in the project directory is the one a person finds first,
because it is where every other tool they have used keeps it, and because it
is right there. A key put in it does nothing at all, and does nothing
silently: no warning, no message, just a program that behaves as though no key
had been supplied.

This is not hypothetical. It cost a real attempt at using the picture-drawing
plugin: the key was in that file, the program could not see it, and the error
said the key was not set — which was true from the program's point of view and
useless from the person's.

There is a second half that makes it harder to reason about. That file is a
link pointing outside the copy of the project being worked in, so several
copies of the project share one file. Whether that is desirable is a separate
question; that it is invisible is the problem.

## Proposed outcome

A key put in the obvious place either works, or the person is told plainly
that it is the wrong place and where the right one is.

Whichever of those is chosen, nothing about keys is silent. The failure mode
that costs an hour is the one where everything looks correct.

Somebody arriving at this project can tell, from the project itself, where
secrets are supposed to go — without reading the program.

The smallest version still worth having is a clear message rather than
support: if the file is present and holds something that looks like a setting
this program knows, say so and say where it belongs.

## Affected users and systems

Anyone setting this project up, which so far is one person and will not stay
that way.

The part that loads stored settings, which currently reads exactly one
location.

The first-run command, which writes to that same one location and is the only
thing that tells a person where it is.

## Constraints

Reading a second location is a decision about precedence, not a convenience:
if both files exist and disagree, something has to win, and whatever wins must
be obvious rather than discovered.

A file holding secrets stays out of version control wherever it lives.

Nothing here changes where the first-run command writes. That is the location
of record; the question is only what to do about the other one.

## Open questions

Whether to read the project-directory file at all, or only to warn about it,
is the decision. Reading it is what a person expects and adds a precedence
rule to think about forever. Warning is smaller, keeps one location of record,
and means the obvious thing still does not work — it just stops being silent.

Whether the link pointing outside the working copy is deliberate is a question
for whoever made it. If several copies of the project sharing one set of keys
is wanted, it should be written down; if it is accidental, it is a second way
to be surprised.

## Changed during planning

The problem was first written as "keys should be read from the project
directory", which is a solution wearing a problem's clothes and assumes the
answer. The failure is not that the file is unread — it is that it is unread
*silently*, and a warning fixes the expensive half at a fraction of the cost.
That reframing is what put the smallest version in reach and turned the
original framing into one of two candidates in the open question.
