---
name: plan-skill
description: Run the Plan stage for a work item — interview the user until the intent is genuinely specified, then write intent.md. Use whenever a new work item starts, when the user says to start on a feature or task, or when docs/tasks/<ID>/intent.md is missing or incomplete.
---

# Plan stage — the interview

Interview the user until an `intent.md` becomes writable, then write it.

**Ask in rounds, ordered so no question depends on an answer you have not
heard yet.** One or two at a time. A wall of questions gets a wall of shallow
answers.

**Finding facts is your job. Decisions are the user's.** If a question needs
something you could look up — what exists in the tree, what the reference
corpus does, what a command currently prints — go and find it before asking.
Put every decision to them and wait.

## What intent.md has to be

Three properties, and every rule below serves one of them.

**Human readable.** Someone who does not write code must be able to read it
and recognise their own problem. Prose, short sentences, no jargon, no module
names, no code fences. The reader is a stakeholder, not a maintainer.

**Machine actionable.** The section headings are fixed so the next stage — and
`scripts/artifact.py` — can read it without a human translating. Never rename
or reorder them.

**Version controlled.** It is committed, it carries an author and a status,
and it is amended rather than replaced. Git history is the audit trail: a
later reader must be able to see what the intent was when the code was
written, not only what it became.

## Procedure

1. `scripts/artifact.py status` — confirm the active work item.
2. Ask the user what they want and why, in their own words. Write nothing yet.
3. Work the challenges below, section by section, in rounds.
4. Stop when you could answer every challenge yourself.
5. Write `docs/tasks/<ID>-<slug>/intent.md`.
6. `scripts/artifact.py check`. Fix what it reports. Never hand back a red check.

## The challenges

Grouped by the section each one feeds.

**Problem** — a person failing at something, in the present tense.

- Is this a problem in the world, or a fact about a codebase? "X is too big
  and does things I don't need" is a diff, not an intent.
- Could someone who has never seen our code recognise it?

**Proposed outcome** — what is observably true afterwards.

- Can you state it without naming a module, file, library or pattern? If not,
  they are designing, and design belongs in `spec.md` where it can be argued
  with.
- Is it visible from outside the system?
- What is the smallest version still worth having?

**Affected users and systems** — who and what this touches.

- Who else has this problem? "Only me" is a fine answer; make them say it,
  because a tool for one person and a tool for many diverge early.
- What finds out that this changed? Name the parts, not the mechanism.

**Constraints** — true regardless of design.

- Which of these would a good idea lift? That one is a preference, not a
  constraint. Move it out.
- What is this work item declining that a reasonable person would expect?

**Open questions** — what you could not resolve.

- What did we leave unanswered, and who can answer it? An empty section is a
  real outcome; omit it rather than inventing filler.

**Shape** — asked last, because it depends on everything above.

- Does this belong to one commit? If the honest answer is two or three, stop
  and split it into separate work items now.

## Writing the file

```markdown
# Intent: <short name>

Author: <name> (<role>). Status: draft.

## Problem

## Proposed outcome

## Affected users and systems

## Constraints

## Open questions (omit if there are none)

## Changed during planning
```

The first five sections are Anthropic's template and are the contract — keep
the names exactly. `## Changed during planning` is this project's own trailing
addition: it is the trace, and the only thing that lets anyone tell an
interviewed intent from a dictated one.

Write in it what the interview actually altered — a narrowed scope, a split
work item, a dropped assumption, a constraint that turned out to be a
preference. Ten words minimum, and the validator counts them.

If nothing changed, say so **and say which you believe**: the intent arrived
unusually well formed, or you did not push hard enough. The second is common
and worth recording.

## Do not

- Write the file before the interview.
- Ask the challenges as a list.
- Accept "make it better" as an outcome.
- Let a confident first framing survive unexamined.
- Continue to `spec.md`. Separate stage, separate skill — and the chain gate
  will stop you.
