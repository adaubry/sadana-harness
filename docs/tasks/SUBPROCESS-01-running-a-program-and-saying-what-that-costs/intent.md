# Intent: Running a program, and saying what that costs

Author: adam aubry (owner). Status: draft.

## Problem

A plugin can talk to a service over the network and can do nothing else. It
cannot run a program.

That rules out a whole category of what people actually want done. Driving a
browser, converting a video, resizing a picture, reading a document format,
running a checker over some code — all of it is a program that already exists
on the machine, and none of it is reachable. The two capabilities built so far
both happened to be a web request, which made the gap easy not to notice.

The plan for what gets built next runs into this immediately. The remaining
capability on the list is browsing the web properly, and the thing that does
it is a program, not an endpoint. It is blocked, and so is everything shaped
like it.

Underneath that is a harder problem, and it is the reason this has been put
off rather than simply not done yet. Everything a plugin does today runs
inside this program, with everything this program can reach. That was
acceptable while every plugin was written by us. The moment a plugin runs
somebody else's program, it stops being true, and nobody has decided what
should happen instead.

There is a specific version of that which is easy to miss. This program now
holds the keys a person has supplied — for searching, for drawing pictures,
and for whatever comes next. A program started carelessly inherits the
environment it was started from. Running somebody's tool would hand it every
secret the person has ever configured, for no reason, and nothing would say
so.

## Proposed outcome

A plugin can run a program on the machine and get back what it printed and
whether it worked.

The program is named explicitly, with its arguments given as a list rather
than a line of text somebody assembles. There is no path by which what a model
wrote becomes part of a command being interpreted.

A program that runs too long, or prints too much, is stopped rather than
allowed to continue indefinitely. Both limits are ones a person can see and
change.

A program starts with the secrets this project holds removed from its
environment, unless the plugin running it explicitly passes one. Nothing is
inherited by default.

Most importantly: what this does and does not protect against is written down
where somebody deciding to use it will read it, in plain terms, without
overstating it. If it is not a defence against a hostile program, it says so
in those words.

The smallest version still worth having: run a named program with arguments,
with a time limit, an output limit, and a scrubbed environment, and report
what happened.

## Affected users and systems

The person running this project, on whose machine these programs run, with
their access to their own files.

Anyone writing a plugin that needs something other than a web request, which
is the next thing on the plan and several things after it.

The part of this project that reaches outside, which currently does one thing
and will now do two.

Whoever later decides whether to run software this project did not write. This
work item does not make that decision, and must not be mistaken for having
made it.

## Constraints

One way of running a program, locally. Not a choice of places to run it, not
an arrangement that anticipates running things elsewhere, no matter how
obviously that will be wanted eventually.

Nothing new gets installed to make this work.

No command line is ever assembled from text. The program and each argument are
separate values from the start, and nothing in between interprets them.

The environment a program starts with is built, not inherited.

The output a program produces is bounded, as is the time it may take. This
project has already been caught once treating another party's output as
though it would be a reasonable size.

This is not a security boundary and nothing here may be described as one. A
program run this way can do what the person running this project can do. The
work item states that plainly rather than implying otherwise by silence, and
the deliberate consequence is that deciding to run somebody else's software
stays a separate decision that somebody has to make on purpose.

## Open questions

What posture to take toward software this project did not write is the
question this work item exists to put a floor under, and it does not answer
it. Running things with the person's own access is honest and simple and
offers nothing against a hostile program. Real isolation means either
something else installed on the machine or a dependency, both of which this
project has so far refused. The middle — limits on time, memory and output —
stops an accident and not an attacker. The owner decides how far to go, and
the answer should be made before anything runs software from outside, not
during.

Whether a program should be able to be given a working directory, and whether
that should be restricted to somewhere known, is undecided. Leaving it out is
smaller; putting it in is most of what makes running a converter useful.

## Changed during planning

The framing changed once and it changed what this is.

The first version was "add the ability to run a program", a capability. That
is the smaller half. The reason this one item blocks the rest of the plan is
not that running a program is hard — it is roughly twenty lines — it is that
running a program is the moment this project stops being able to assume
everything it executes was written by us. The outcome now leads with saying
what the thing does not protect against, because a primitive that quietly
implies more safety than it has is worse than none: somebody builds on it.

One item moved out of the outcome into a constraint, and it is the one most
likely to have been missed. "Do not leak secrets into the program" was not in
the first draft at all. It surfaced from reading what this project now holds:
keys for two services, in the environment, which anything started from here
would inherit for free. That is not a theoretical exposure; it is the default
behaviour of the obvious implementation.

Scope narrowed twice. Running something and watching it as it goes, and
running something that keeps running after the answer is given, both came out.
Each needs somewhere to keep a running thing between calls, which is a
different problem with its own unanswered questions, and neither is needed by
what is actually blocked.
