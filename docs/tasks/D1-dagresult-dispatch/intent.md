# Intent: A plugin's result stops being a single line of text

Author: Adam Aubry (maintainer). Status: draft.

## Problem

When a plugin runs today, nobody reading what happened — not a person
looking at the conversation, not the automated check built to catch bad
agent behaviour — can actually tell what the plugin did. All that comes
back is one line of text. A plugin that succeeded and a plugin that failed
partway through look identical. A plugin that produced a file or a link has
no way to say so, beyond hoping the words mention it. And the one mechanism
this project has built specifically to catch behavioural regressions as the
agent gains more capabilities is blind to the thing that will end up
carrying every capability it ever gains — it can only see whatever text a
plugin happened to write.

This has already been named and left open once: an earlier piece of work
recorded that a plugin has "no channel to report anything back to its own
caller beyond a result string," and deliberately left it unsolved for
whichever later work would actually build this. This is that work.

## Proposed outcome

Wherever the running system hears back from a plugin, it receives a
structured answer instead of a bare string — one that can say what happened,
whether it worked, and what it produced, separately from the words a person
or the model would read. Every place in the system that currently talks to
a plugin as "the answer is this string" is moved onto the new shape in this
same piece of work, including the one script that is the only place this
mechanism has ever been exercised against a live run. When this is done,
nothing in the repository is still treating a plugin's outcome as
indistinguishable from a plugin's error.

The smallest version worth having is exactly this: the shape of the answer,
and every existing caller and demonstration updated to use it. Nothing about
what a plugin is allowed to *do* changes here — only what it is able to
*say* when it's done.

## Affected users and systems

Only the maintainer touches this repository today, but the honest answer to
"who has this problem" is every future user of the plugin system: this is
the foundational contract that every plugin, present and future, gets built
against, and a mistake here is expensive to unwind later precisely because
everything downstream will assume it.

Concretely, this reaches: the three places in the already-built turn-handling
system that call into a plugin and get an answer back; the one hand-written
proof that this mechanism works end to end against a live model, and the two
small example plugins that proof drives; and the earlier piece of work whose
own notes say this gap is still open — those notes become stale the moment
this item closes it and should say so.

## Constraints

- This item does not define the file a plugin author writes, or build
  anything that turns that file into a runnable sequence of steps. That is
  separate, parallel work, read against the same design material as this
  item but landing as its own piece of work.
- This item does not give a plugin the ability to reach outside the process
  it runs in, and it does not add any way to pause a plugin partway through
  and pick it back up later. A plugin still runs start to finish in one
  pass, and nothing here changes that.
- This is a clean break, not a transition. Every place that currently
  expects a plain-text answer from a plugin — including the one proof
  script and its two example plugins — moves to the new answer shape in
  this same commit. Nothing is left half-migrated, and nothing bridges the
  old shape and the new one temporarily; this project already declines that
  kind of shim for code that can simply be changed outright.

## Open questions

- What should a person actually be told when a plugin fails partway
  through? They need something useful, and they must not be handed the
  internals of what went wrong. Left to whoever designs this next.

## Changed during planning

Two things moved during the interview. First, whether the one hand-written
proof script and its two fixture plugins belong inside this item's scope was
genuinely open going in; it resolved as in-scope, because leaving them on
the old answer shape would mean the repository no longer has anything that
demonstrates the mechanism working end to end after this item lands.
Second, the interview confirmed this is a clean break with no transitional
shim between the old and new answer shape, rather than leaving that
ambiguous — consistent with this project's standing rule against
backwards-compatibility hacks where the code can simply be changed.
