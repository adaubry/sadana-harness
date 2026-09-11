# Intent: An error that blames the wrong thing

Author: adam aubry (owner). Status: draft.

## Problem

Running a command that exists, from the wrong copy of the project, produces an
error saying the command does not exist.

That is literally true of the copy being run, and it points at the wrong
cause. A person reads "invalid choice" and concludes they have the name wrong,
or that the feature was never built. What actually happened is that the
program they ran is older than the one they were reading about.

This project is developed in several working copies at once, each on different
work, each with a different set of commands. A command added in one is absent
from the others, and nothing in the error distinguishes "no such command" from
"not in this build".

It cost a real attempt at configuring a plugin. The command was correct, the
instructions were correct, and the message sent the person looking at the
wrong thing entirely.

## Proposed outcome

When a command is not recognised, the person can tell from the message which
copy of the program answered them.

The version of the program being run is visible at the point of failure, not
only when it is asked for specifically.

Nothing changes about what the program does when a command *is* recognised.

The smallest version still worth having: the failure names where the program
was run from.

## Affected users and systems

Anyone running this project from more than one working copy, which today is
how it is developed and will be how it is supported.

The command-line entry point, which owns how a bad command is reported.

Nothing else. This is about a message.

## Constraints

The way command-line arguments are handled is not replaced, worked around, or
wrapped to achieve this. Its own behaviour for unrecognised input, for asking
what the commands are, and for asking the version is established and correct.

A message meant to orient somebody who is lost stays short. A paragraph is
noise at the moment somebody wants one fact.

Nothing here makes a correct command slower or noisier.

## Open questions

Whether this is worth doing at all is a fair question and should be asked
before it is built. It cost one person one confusion. If the answer is that
developing in several copies at once is the unusual thing and the message is
fine, that is a legitimate outcome and this work item closes unbuilt.

## Changed during planning

The scope shrank a long way. The first version had the program detecting that
another copy nearby had the command and suggesting it — which means one copy
of the program knowing about others, a large idea for a small confusion. What
is left is that a failure should say where it came from, which is one line and
is most of the value.

It also changed from a bug into a question. Writing down how often this can
happen made it clear it happens to one person developing in parallel copies,
not to anyone using the finished thing — so the honest first question is
whether to build it, and that is now the open question rather than an
assumption.
