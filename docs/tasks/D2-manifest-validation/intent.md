# Intent: A plugin's own description of itself can be trusted before anything runs

Author: Adam Aubry (maintainer). Status: draft.

## Problem

There is no way today to write down what a plugin does and find out, before
running any of it, whether that description is actually coherent. Someone
writing a plugin's file — right now, that's only the maintainer, hand-writing
example fixtures — can misspell a name, point a step at something that
doesn't exist, or leave a piece unreachable, and nothing says so until the
system tries to run that exact broken piece, if it ever does. A marketplace
showing a plugin's shape to a person deciding whether to install it, or a
visual tool meant to display that shape without ever executing a stranger's
code, cannot exist at all if the only way to find a broken plugin is to run
it.

## Proposed outcome

The system can read a plugin's own description of itself and say, before a
single step of it runs, whether that description can be trusted: every
reference inside it points at something that is actually there, every piece
it names actually exists, and every step it declares can actually be
reached from where a person would enter it. When something is wrong, the
system says plainly what and where, as a specific, nameable answer rather
than stopping with an unexplained error partway through reading it. Nothing
about running a plugin's steps changes here — this is entirely about
knowing a description is sound before execution is ever attempted.

## Affected users and systems

Only the maintainer touches this today, but this is the gate every future
plugin author's work has to pass before anything of theirs is allowed to
run — a future marketplace, a future visual editor, and every person reading
a plugin's shape by hand all depend on this check existing and being
trustworthy without ever having to execute someone else's code to trust it.

Directly, this reaches the part of the system that currently only reads a
plugin's accompanying instructions and nothing else about its shape, and it
becomes the first real use, outside where it was first proven, of a pattern
this project already established elsewhere: a classified answer describing
exactly what went wrong, never an uncaught failure.

## Constraints

- This item does not run a single step of a plugin's declared sequence.
  Confirming the description is internally coherent is the whole of it —
  whether a step actually behaves the way it claims to is not checked here,
  and cannot be without running it.
- This item does not fetch, install, or manage where a plugin's files come
  from. It assumes the files it is validating are already present in the
  one place the system already knows to look for them.
- This item does not decide how a plugin's own external dependencies get
  installed, and it does not change what the rest of the system shows the
  model about an installed plugin. Both are separate, later pieces of work
  reading the same design material as this one.
- Every check this item is responsible for reports its result the same way
  an already-proven part of this system already reports a classified
  failure — a named answer, never an uncaught error stopping the program.
  This item does not invent a second way to do that.

## Open questions

- Should this validation run once, for every installed plugin, when the
  system starts up, or lazily, the first time a particular plugin's
  capability is needed? Left to whoever designs this next.

## Changed during planning

Two things were confirmed rather than left implicit. First, the full set of
checks named in the design material is in scope together, not a partial
first slice — they are one coherent list that keeps a plugin's declared
shape honest against what it actually names, and passing a description that
still has a dangling reference would defeat the point. Second, the shape a
failure takes was confirmed to reuse the classified-answer pattern already
proven elsewhere in this system, rather than inventing a new one for this
item alone.
