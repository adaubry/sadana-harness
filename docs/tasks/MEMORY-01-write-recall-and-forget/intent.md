# Intent: You shouldn't have to reintroduce yourself every time

Author: Adam Aubry (maintainer). Status: draft.

## Problem

Every conversation with an agent built on sadana-harness starts blank. A
person who has already told the agent how they like things done, or
mentioned something true about themselves or their work, has to say it again
next time — the agent has no way to carry any of that forward from one
conversation into the next. This gets worse, not better, the more a person
actually uses the agent: the things worth knowing about them pile up
unrecorded and unused.

## Proposed outcome

An end-user of a sadana-built agent no longer has to repeat themselves.
Things worth remembering about them are picked up during an ordinary
conversation — without the person having to explicitly ask for anything to
be saved — and are available to the agent in later conversations with that
same person. What counts as worth remembering follows a rubric: whoever
deploys the agent sets a default for it (starting from "whatever helps the
agent know this person better" if they don't set one), and an individual
end-user can further adjust that rubric for themselves on top of the
deployer's default.

A person can see what has been remembered about them and remove any entry
they don't want kept; once removed, the agent stops using it.

None of this changes what the agent is instructed to do or how it behaves.
It only changes what the agent knows about a person. A human — not the
agent — is still the one who changes behavior.

## Affected users and systems

- End-users of any agent built on sadana-harness: what gets remembered,
  recalled, and shown back to them is scoped to each of them individually.
- Whoever deploys such an agent: they set the default rubric for what's
  worth remembering, before any end-user adjusts their own.
- Every later conversation a given end-user has with the agent: it starts
  from a different footing than before this exists, because relevant things
  about that person are already available to it.
- Wherever an end-user manages their own account or settings: that's where
  they'd see and remove what's been remembered about them.

## Constraints

- The agent does not author or rewrite its own instructions, procedures, or
  skills as part of this. It remembers facts and decisions that were already
  made; a human still changes behavior. (This is the half of hermes-agent's
  LEARNING block that does not survive into this paradigm — the
  autonomously self-maintaining half stays out.)
- Capturing something worth remembering cannot require the end-user to
  explicitly ask for a save every time — it has to work from the ordinary
  flow of a conversation, the same way it already does in the reference
  corpus.
- A failure to record a memory cannot interrupt or degrade the conversation
  it happened during.
- One end-user's remembered facts are never used in, or contribute to,
  another end-user's conversation. There is no cross-user aggregation or
  analytics as part of this.
- This does not replace or duplicate the verbatim conversation history
  sadana-harness already keeps (the CONVERSATION block). What this holds is
  distilled — facts and decisions — not transcripts.
- The deployer's default rubric is the floor for every end-user under that
  deployment; an individual end-user's own adjustment sits on top of it for
  themselves only, it does not change the default for anyone else.

## Open questions

- If a deployer changes their default rubric after end-users already have
  memories accumulated under the old one, are those existing memories
  reclassified against the new rubric, or left as they were? Not resolved in
  this interview — left for spec.md.

## Changed during planning

The interview surfaced three real changes from the framing this item
started with. First, scope: it began sounding like a tool for one operator,
but the user confirmed sadana-harness is multi-user from the start — memory
is partitioned per end-user of whatever product gets built on top, not per
developer. Second, the "known set of guidelines" language in the original
notes could have been read as a fixed taxonomy of memory types (the way the
reference corpus and other systems categorize memories into several fixed
kinds) — the user rejected that reading explicitly: there is one category,
and what qualifies for it is governed by a single customizable rubric
instead of a fixed set of types. Third, recall and the ability to view and
delete a remembered entry were pulled into this same work item rather than
deferred — the user's reasoning was that a memory never surfaced, or never
removable, isn't a finished feature.
