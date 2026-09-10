# Intent: Building a plugin by drawing it

Author: Adam Aubry (product owner). Status: draft.

## Problem

Building a plugin means writing code. That puts every capability this
system could ever gain behind one narrow door: someone who can program.

A person can know exactly what they want a procedure to do — ask this,
check that, then go and do the other thing — and still have no way to
express it. They learn to program, they find a programmer, or the thing
doesn't get built. Most of the people with the clearest idea of what a
procedure should do are in the third group.

## Proposed outcome

Someone who cannot write code lays out a plugin's steps by dragging boxes
onto a canvas and joining them with arrows, saves it, comes back later,
and sees the same shape drawn back.

What they produce is not a beginner's format that something else has to
translate. It is the very same description of a plugin a programmer writes
by hand today, and it works in both directions: a plugin someone wrote by
hand opens in the editor and draws correctly, and a plugin drawn in the
editor runs with nothing converted in between.

Some steps still need a programmer, and the editor is honest about which.
A step that asks the agent something, a step that waits for an answer from
outside, and a step that ends the run need nothing from anyone but the
person drawing. A step that works something out, reaches outside, or
decides which branch to take still needs a programmer to supply the piece
that does the actual work — the editor draws and wires those boxes and
marks plainly that they are waiting on someone. A plugin half-finished in
exactly this way is a normal thing to save and reopen; it simply isn't
ready to run yet.

## Affected users and systems

The people this whole approach exists for: creators who are not
programmers. Not one person, not internal — this is meant to be used by
people who could not otherwise build anything here at all. And, right
beside them, the programmers who fill in the steps that still need code,
who now get handed a finished shape instead of a description in prose.

What notices this changed: everything that already reads a plugin's
description has to accept what the editor produces without special-casing
it — that is the whole bet, and it is the reason the description was kept
as plain data instead of code in the first place. This is also the first
part of the system a person looks at in a browser rather than a terminal,
so how it is served and reached is new ground rather than an extension of
something already there.

## Constraints

- It runs on one person's own machine for now, but nothing about it may
  make a real, hosted version impossible later. Decisions that quietly
  assume one person on one machine are the thing to avoid.
- It reads and writes the same plugin description a programmer writes by
  hand. No second format, no conversion step, nothing to keep in sync.
- The editor never writes the code parts. It draws them, wires them, and
  marks them as waiting; someone else supplies the code itself.
- Saving and reopening an unfinished plugin — one whose code parts are not
  filled in — has to work. That is a normal working state, not an error to
  refuse.
- The boxes on offer are exactly the kinds of step the system actually
  has. Adding a new kind is its own piece of work, never a setting: every
  box is a concept a non-programmer has to understand, and the palette is
  the whole vocabulary they get.
- The loop box appears but is marked as not yet usable, because nothing in
  the system can run one yet. A creator can lay out the shape they intend;
  they are told plainly it won't run.
- Not in this: signing in, accounts, or any question of who is allowed to
  edit what. Also not in this: writing the code parts in the browser.

## Open questions

Where a drawn plugin's layout lives. The plugin description has no place
to record where a box sits on the canvas, so reopening one either
re-arranges the boxes automatically every time, or the positions have to
be kept somewhere — and "somewhere" is either inside the plugin's own
description, which changes a format three closed pieces of work already
depend on, or beside it in something new. This is the requester's call and
belongs in the design stage, with the real cost of each option written
down rather than guessed at here.

## Changed during planning

Five things moved. The phase question was raised and settled first: this
is where the project stops building backbone and starts building the thing
the backbone was for. The "no-code" claim was then challenged directly and
narrowed — three of the step kinds still need a programmer, so the editor
draws and wires them but leaves the code to someone else, which is the
honest version of the claim rather than the marketable one. The shape
reversed mid-interview: it was first split into a round-trip core plus
later polish items, then put back to a single work item at the requester's
direction, knowing the diff will be the largest this project has produced.
The loop box came up because nothing can run one yet; the decision was to
show it marked unusable rather than hide it, so a creator can still lay
out what they mean. And the delivery form was pinned to local-for-now with
an explicit standing requirement that a hosted version stay possible,
which is a constraint on every design decision that follows rather than a
preference about where it runs today.
