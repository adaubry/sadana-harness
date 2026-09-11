# Intent: Who the agent is talking to

Author: Adam Aubry (maintainer). Status: draft.

## Problem

The assistant does not know who it belongs to, so it treats its owner's own
machinery as a stranger.

A person writes a character, chooses it, and then hears it in one place only:
the terminal they typed in. A schedule they wrote themselves, firing at six in
the morning to do a job they asked for, answers in a flat default voice — and
remembers what it learned somewhere the person will never see, under a name
made out of the trigger's own title. Nothing about that was chosen. It is what
happens when the only thing the assistant knows about a piece of work is which
door it arrived through.

The same gap shows up the first time someone picks a character at all. They
name themselves the way they think of themselves, the tool says it worked, and
nothing changes, because the part of the system that does the talking was
using a different name for them all along. There is no way to find out what
that name was — nothing lists the people and places the assistant has spoken
with — so the person is left to conclude the feature is broken.

## Proposed outcome

The assistant has an owner, and knows the difference between its owner and
everyone else.

Work that the owner set in motion is the owner's work. A schedule they wrote
speaks in their voice and remembers into their memory: what it learns at six
in the morning is there the next time they talk, in the same place everything
else it knows about them lives. What those schedules have already learned,
filed away until now under their own names, becomes part of what the assistant
knows about the owner — nothing learned is thrown away on the day this
changes.

A message from someone the assistant has never met is not the owner. It stays
in the plain, neutral voice, and what it learns stays with the person it came
from, until the owner says otherwise.

Choosing a character for a name nothing has ever used fails, and says so, at
the moment it is typed — except for the owner's own name, which always works,
because picking your voice before you have said anything is a perfectly
ordinary first move. And a person can ask who the assistant has spoken with,
and get a list, so a name they need is something they can look up rather than
guess.

The smallest version still worth having: a schedule the owner wrote speaks in
the owner's voice, and picking a character for a name that does not exist
fails out loud.

## Affected users and systems

Whoever runs sadana — one person per installation, which is the shape this
assumes throughout. Anyone whose messages arrive from outside is affected too,
in that this decides they are deliberately *not* the owner.

What finds out this changed: scheduled work, which becomes the owner's rather
than its own; what the assistant remembers, both where new things are written
and where the old ones now live; the commands that choose and show characters;
and the terminal, which has had an owner's name all along and now shares it
with everything else.

## Constraints

A conversation is still one thread. This changes who a piece of work belongs
to; it never merges two conversations into one, and it never changes what was
already said in either.

Moving what the schedules remembered happens once, and loses nothing. A person
who looks afterwards finds every fact that existed before, in the owner's
memory rather than scattered.

Nobody becomes the owner by arriving. A stranger's message can never promote
its sender to the owner, however it is addressed, and the owner is never
inferred from traffic.

Everything recorded before this change keeps working: old conversations still
open and still answer.

This declines giving names to the people on the other end of a webhook — there
is still no way to say "this one is my colleague" — and it declines serving
more than one person from one installation. Both are real things a reasonable
person might expect; neither is needed yet, and both would change what an
owner means.

## Open questions

Whether a regular correspondent should ever stop being a stranger — a chat the
owner recognises, given a name and a voice of its own. Nobody needs it yet,
and the answer belongs to whoever first has a correspondent worth naming.

## Changed during planning

The item that entered this interview was a voice question and leaves as an
identity question. It began as the review finding that scheduled replies come
out in the wrong voice; the first answer — that a schedule is the owner's own
machinery, so it inherits the owner's voice — turned out to have a much larger
consequence nobody had asked for, and the interview stopped to surface it: an
account is not only what the assistant sounds like, it is whose memory the
assistant writes into. Told that, the user chose to take both rather than
split the voice from the memory, so scheduled work becomes the owner's work in
full.

Half the original finding needs no work at all. The webhook side was raised as
a regression — strangers now hearing a neutral voice where they used to hear
the owner's — and the user's answer was that neutral is correct for someone
the assistant has never met. That half closes as built rather than as a fix,
which is worth recording so the next reader does not go looking for the
missing code.

One decision was walked back during the interview. Refusing a character
selection for an unknown name was accepted, then immediately tested against
the most ordinary first use there is — picking a voice before you have ever
said anything — which it would have refused. The owner's own name is now
always allowed, and the refusal applies to everyone else's.

The user was offered a split into two work items, identity first and the
commands second, with the reason for splitting stated: one commit would carry
a behaviour change and a usability change through a single review. They chose
one item deliberately, and this records that the cost was named before the
choice rather than after.
