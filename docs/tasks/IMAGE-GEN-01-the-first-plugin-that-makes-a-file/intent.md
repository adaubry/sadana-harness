# Intent: The first plugin that makes a file

Author: adam aubry (owner). Status: draft.

## Problem

Ask the agent for a picture and it writes about one. It can describe what the
picture would look like, at length, and it cannot produce it. For a whole
category of ordinary requests — a diagram, an illustration, a mock-up, a
thing to look at — the answer is always words about the thing instead of the
thing.

Underneath that is a gap in what this project has ever done. Everything it
produces is text. A place for a plugin to write a file was built and nothing
has ever written one, so the whole path from "a plugin made something" to "the
person has it" has never been walked. A facility with no user is a guess.

There is also a cost that has been deferred three times and is about to stop
being theoretical. Nothing deletes anything. That was fine while nothing made
files; the moment something does, and especially something a person will
cheerfully run twenty times to get the picture right, the disk starts growing
and nothing stops it.

## Proposed outcome

A person asks for a picture, describes what they want, and gets one — a real
image file, on their own disk, with the agent telling them where it is.

The file lands in the place that already exists for things a plugin makes, and
nowhere else. It is a normal file they can open, move, or delete like any
other.

The picture itself never becomes part of the conversation. What the agent
learns and repeats is that a file was made and where it is — not the image's
contents, which are large, and which would crowd out everything else being
discussed if they were carried around as text.

The person needs an account with whatever service draws the picture, is told
so clearly, and is told what it will cost them, because unlike searching this
one is not free.

There is a demonstration that a real picture was really drawn by a real
service and really written to disk — run on its own, against the real thing,
with its output recorded where the decision to ship is made. Making a file is
the half that has never been proven, so the demonstration has to show the file
existing and being a picture, not merely that a request succeeded.

The smallest version still worth having: one description in, one image file
out, one path reported. Not editing an existing picture, not variations, not
choosing a size or a style beyond what the description says, not more than one
image at a time.

## Affected users and systems

The person running this project, who gets pictures and pays for them, and
whose disk now fills up.

Anyone writing a plugin that produces something rather than saying something.
This is the first one, so whatever is awkward about it is what they will hit.

What finds out that this changed:

- What the agent can do, which gains an ability.
- The place a plugin writes files, which has existed unused and now has its
  first real user — including whether what was built is actually usable.
- The record of what a plugin run produced, which has carried a way to name a
  file since before there was anywhere to put one.
- The person's disk, which now grows every time somebody asks for a picture.
- The step that asks permission before a plugin acts, which now guards
  something that costs real money each time it runs.

## Constraints

One drawing service, reached directly, with no arrangement for a second.

Nothing new gets installed. If it cannot be done with what this project
already depends on, the answer is a different service.

The image's contents never enter the conversation. Only the fact of the file
and its location travel back. This is not a preference about tidiness: an
image carried as text is enormous, and it would push out the thing the person
was actually talking about.

The file is written inside the run's own place and nowhere else, and a claim
that it was written somewhere else is refused rather than believed.

The account key is a setting the plugin declares and the person supplies, the
same as the last one. It is never in this repository.

Drawing a picture takes far longer than fetching a page, and whatever governs
how long this project waits for an outside service was chosen for fetching
pages. It has to accommodate this without being removed.

Nothing here deletes anything, and this is the work item where that stops
being free. Saying so is a constraint; fixing it is not in scope, and the open
question below is where that gets decided.

## Open questions

What happens to old pictures is now urgent rather than theoretical, and the
owner has to answer it. Three plausible answers: a cap on total size with the
oldest going first, an age after which anything is fair game, or nothing at
all plus a command that empties the lot when asked. This work item ships the
thing that creates the problem and deliberately does not pick; picking it
belongs to its own work item, informed by what a month of actual use looks
like.

Whether this plugin should ask for its own account key, when the person has
already supplied one for the same company at first-run setup, is undecided and
is a genuine fork. Asking again is honest about a plugin's key being the
plugin's, and it is annoying and looks like a bug. Reaching for the one
already there is convenient and quietly makes a plugin a thing that can read
the host's credentials, which is the distinction the settings work was built
to keep visible.

## Changed during planning

Three things changed, and one of them changed the shape of the work.

The most important: "the picture never enters the conversation" started as a
tidiness note in passing and is now a constraint. Checking how a drawing
service actually replies made it concrete — the picture comes back encoded as
text, and it is very large. The obvious implementation hands that straight
back, and the obvious implementation is wrong in a way that would not look
wrong: it would work, produce a correct file, and quietly consume most of the
room the conversation had left. That is now written down where it cannot be
missed.

The waiting time was not in the first draft at all. Everything this project
has fetched so far comes back in under a second, so how long it is willing to
wait was set once, for that, and has never mattered. Drawing takes tens of
seconds. Nothing about the feature is interesting and it would have failed
every time.

The retention question was going to be listed as a constraint and moved to an
open question, because writing "nothing deletes anything" as a constraint
implies it has been decided, and it has not — it has been deferred three
times, and this is the work item that makes the deferral cost something. It is
now stated as a cost this item knowingly creates, with the decision named as
somebody else's and scheduled.

Scope narrowed once more than expected. Editing a picture and asking for
variations both came out: they need a way to refer to a picture that already
exists, which means naming things that were made earlier, which is the
retention question wearing a different hat. One unanswered question is enough.
