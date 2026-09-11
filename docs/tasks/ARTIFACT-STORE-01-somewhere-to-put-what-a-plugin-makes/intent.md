# Intent: Somewhere to put what a plugin makes

Author: adam aubry (owner). Status: draft.

## Problem

Some plugins make things that are not words. An image, a spoken answer, a
screenshot of a page, a transcript of a recording. Every one of those is a
file, and this project has nowhere to put a file.

There is no place a plugin is told to write to, so a plugin author has three
bad options and no good one. Write into whatever directory the program
happened to start in, which is the person's own working folder and not
anywhere they asked for it. Invent a location, which every plugin then invents
differently, so nothing can be found twice and nothing can be cleaned up.
Or produce no file at all, which means the whole category of plugins that make
something is out of reach.

The promise has already been made and is not kept. A plugin run can already
hand back a reference to something it produced, and that reference is
described, in the place it is defined, as a path under the run's own output
directory. There is no such directory. Nothing creates one, nothing resolves
one, and the sentence describes a thing that does not exist.

The work item that introduced the reference said so plainly and left this out
on purpose, as its own stated non-goal, to be a later piece of work. This is
that piece.

The consequence is the same shape as the last one: four of the capabilities
this project plans to bring over — pictures, moving pictures, speech, and
reading a page — produce a file as their whole point, and not one of them can
be built until a file has somewhere to go.

## Proposed outcome

Every run of a plugin has a place of its own to write files, and the steps that
need it can find out where it is without being told by the conversation.

The place is created only when something actually writes to it, so a run that
produces nothing leaves nothing behind.

It sits under the directory this project already uses for the things it keeps
between runs, so a person who has already decided where this program may write
does not have to decide again, and moving that one setting moves these too.

A run's place is named after the run, using the same name the system already
uses to record that run happening, so the files a run produced and the record
of that run being made can be lined up afterwards by reading either one.

When a plugin says it produced a file, the reference it hands back points at
something that is really there, and a step that tries to write outside its own
run's place is stopped rather than quietly allowed.

The person finds out where a file went the way they find out anything else a
plugin did: the plugin's answer tells them, and that answer already carries the
reference today.

The smallest version still worth having: a directory per run, made on demand,
reachable from a step that is running, and a file reference that resolves. A
way to browse them, a way to expire them, and a record of them in the run
history are each separate and later.

## Affected users and systems

The person running this project, whose disk these files land on, and who will
eventually have to decide what happens to old ones.

The author of a plugin that makes something, who today cannot write that
plugin at all.

What finds out that this changed:

- Running a plugin. A step gains a way to ask where it may write.
- What a plugin run hands back. The reference to a produced file stops being
  a promise and starts being a path.
- The place this project keeps things between runs, which gains a second kind
  of content beside what is already there.
- The person's disk, which will now grow with use in a way it did not before.

## Constraints

A run's place is named from the name that run already has. This project has a
rule against minting a fresh identifier when the thing being named already has
one, and a plugin run already has one, used to record it.

Nothing is written unless something is actually produced. An empty directory
for every run is litter, and litter that accumulates per run is worse than
none.

A step writes inside its own run's place and nowhere else. The check is real
and is made where the path is resolved, not left to each plugin author to
remember.

This project keeps what it stores between runs in one known place, and that
place is already a single setting a person can move. This adds to it rather
than introducing a second such place with its own setting.

Nothing here deletes anything. No expiry, no size limit, no cleaning up after
a run. That is a real cost and it is named in the open questions, not hidden.

Nothing here shows a person a list of what has been produced. The plugin's own
answer already carries the reference, and a browsing surface is a different
piece of work with its own question about what it should show.

## Open questions

What happens to old files is unanswered and the owner has to answer it. The
honest position is that this work item makes a disk grow and does nothing
about it, which is acceptable while the only writer is the owner's own machine
and stops being acceptable the first time a plugin generates pictures in a
loop. The question is not technical — it is how long a thing a plugin made
should stay reachable — and it wants its own work item once there is real
usage to look at.

Whether a reference to a produced file should be written as the full path from
the root of the disk, or as a short path relative to the run's own place, is
undecided. The full path is immediately usable by a person and stops being
correct the moment the state directory moves or the files are copied
elsewhere. The short path survives being moved and needs the reader to know
what it is relative to.

## Changed during planning

Two things changed once the existing code was read rather than assumed, and
one framing was rejected.

The scope shrank twice. The blueprint calls this gap an output directory, and
the first reading had it covering a way to see what had been produced — a
listing, something in the run history. Both came out. The reference to a
produced file already travels back inside the plugin's own answer, which the
model reads and repeats, so a person already learns where a file went by the
same route they learn everything else. Building a second way to tell them
would have been a surface with no reader.

The naming was not free to choose. The first framing gave each run a fresh
generated identifier, which is the obvious thing and is against this project's
own rule: a run that gets recorded already has a name, and inventing a second
one means the files and the record of the run cannot be lined up afterwards
without a lookup table nobody is going to build.

One item moved from an outcome to a constraint. "A plugin should not be able
to write outside its own directory" was written as something the work would
achieve. It is not achievable in that form and saying so matters: a step runs
inside this program, so nothing stops it writing anywhere the program can
write. What is actually being built is that the path it is *given* is checked
and confined — addressing, not confinement of the process. That distinction
was already made and written down for the previous work item's secrets, and
the same honesty applies here; overstating it would make a later reader
believe a protection exists that does not.
