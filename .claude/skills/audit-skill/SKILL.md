---
name: audit-skill
description: Audit whether a finished block actually solved the problem it was meant to solve, by having the code restated blind and comparing that against the plan and the artifact chain. Use when the user asks whether the project is on track, whether a block did what it was supposed to, or asks for an audit or a look back over finished work.
---

# Audit — did we build what we meant to build

This is a maintain-stage run at block scale. It changes no code, produces no
diff, and its output is not a report — it is a list of `intent.md` files
waiting to be written.

**It works because the auditor never learns what answer it is supposed to
give.** Everything else here is bookkeeping around that one rule.

## Why blind

Hand an agent "here is the problem, here is the diff, did it solve it?" and it
will say yes. Almost always. It is primed, confirming is cheaper than
disproving, and you spend a day learning nothing. So the auditor gets the diff
and is asked what problem _this code_ solves, with no idea what problem was
intended. Divergence then shows up on its own instead of being negotiated.

The same reasoning is why `deploy-skill` reviews cold. This is that principle
one level up.

## 0. Prepare the input, and split it in two

The user's input is one file, `docs/audits/<YYYY-MM-DD>/blocks.md`, with one
entry per block:

```
<sha>[;<sha>;<sha>…] — <block name>: <the problem this block was meant to solve>
```

A block is one commit or many. That is the point of compiling them — a block
is a unit of _purpose_, and purpose rarely lands in exactly one commit.

**Split it before anything else runs.** Blindness has to be mechanical, not a
promise:

- `docs/audits/<date>/assignments/<letter>.md` — one file per block,
  containing **only** an opaque letter and the SHA list. No block name: a name
  like "CONFIG as a contract" gives away most of the answer.
- `docs/audits/<date>/key.md` — the letter → block name → problem statement
  mapping. **Only the user and the comparison step in §3 ever open this.**

Check for a SHA appearing in two blocks before you split. That is either a
triage slip or one commit that did two blocks' work — which is a finding in
its own right, about a work item that crossed a boundary. Resolve it with the
user first; it decides which auditor gets that diff.

## 1. Pass one — restate the code, blind

One subagent per block, **launched in parallel**. Each gets its assignment
file and nothing else. Give every one of them this, verbatim except the
letter and SHAs:

```
You are auditing one block of finished work in sadana-harness.

The block is: <letter>
Its commits, in order:
  <sha>
  <sha>

Read them. `git show --stat <sha>` first for every commit to see the shape,
then the production-code diffs. Skip test diffs unless a claim you want to
make depends on one.

These commits are one unit of purpose. Judge their NET EFFECT — what is
true of the repository after the last one that was not true before the
first. A later commit in this block may reverse an earlier one; if so, only
the outcome counts, and the reversal itself is worth mentioning.

Do NOT read docs/tasks/. Do NOT read the commit messages' reasoning beyond
what you need to locate files — you are describing what the code does, not
repeating what its author said about it.

Return a written report in your final message. Do NOT create or edit any
files. Cite file paths and line numbers for every claim; a report without
citations is incomplete and will be relaunched.

Answer in exactly this shape:

  WHAT THIS CODE SOLVES
  Three to five sentences, plain language, as if explaining to someone who
  has never seen this repository. What could the program not do before, and
  can do now?

  HOW IT SOLVES IT
  The mechanism, in two or three sentences, with citations.

  WHAT IT DOES NOT DO
  Things a reader might reasonably assume are covered and are not.

  SURPRISES
  Anything in the diff that does not serve what you described above —
  changes to unrelated files, a second concern riding along, a seam left
  open. "None" is a valid answer and should be given when true.
```

The commit messages in this repository are long and argue for their own
design. That is why the prompt tells the auditor to stay out of them. If an
auditor's report starts echoing a commit message's phrasing, relaunch it.

**Use the strongest model available for this pass.** The failure mode here is
agreement, not error, and weaker models agree more.

## 2. Pass two — what the chain said at the time

A separate agent, after pass one has returned. It may read everything pass one
could not: `docs/tasks/<ID>-<slug>/intent.md` and `spec.md` for every work
item in the block.

It cannot be the same agent. Once the intent has been read it cannot be
unread, and pass one's value evaporates.

```
For each work item listed below, read its intent.md and spec.md and answer:

  WHAT THIS WORK ITEM SET OUT TO DO
  Three sentences, from the artifacts only. Not what the code does — what
  the documents say was wanted.

  Then, for the block as a whole: do these work items describe one coherent
  purpose, or several? If several, name them.

Return a written report in your final message. Do NOT create or edit any
files. Cite the artifact path for every claim.
```

That last question matters on its own. A block whose work items describe
three unrelated purposes was mis-scoped regardless of what the code does.

## 3. The three-way comparison

Now open `key.md`. For each block you have three statements:

- **A** — what the code actually does (pass one)
- **B** — what the work items said they would do (pass two)
- **C** — what the plan said this block was for (the problem statement)

Comparing all three tells you _where_ drift entered, which decides the fix:

|              | reading                                                                                                                   |
| ------------ | ------------------------------------------------------------------------------------------------------------------------- |
| A = B = C    | Solved. Move on.                                                                                                          |
| A = B ≠ C    | **Drift at plan time.** You planned the wrong thing and then built it faithfully. The process worked; the plan was wrong. |
| A ≠ B, B = C | **Drift at build time.** The plan was right and the code went elsewhere. The process did not hold.                        |
| A ≠ B ≠ C    | Both, or the block has no single purpose. Re-read pass two's coherence answer.                                            |

"Equal" means _same problem_, not same words. Two sentences describing the
same capability in different vocabulary are a match. A sentence that describes
a narrower capability is not.

## 4. Verdicts

One per block, and each one that is not SOLVED names its consequence:

- **SOLVED** — the code answers the problem. Nothing follows.
- **PARTIAL** — it answers part of it. Name the part that is missing.
- **DRIFTED** — it answers a different problem. Name that problem.
- **OVERSHOT** — it answers the problem and more. Name the extra.

Record them in `docs/audits/<date>/findings.md`, each with its A/B/C row and
its drift location. That file is the audit's own record; it is not a work item
and does not enter the artifact chain.

## 5. Turn every finding into an intent

This is the deliverable. A finding nobody acts on is worse than no audit,
because it creates the feeling of having checked.

- **PARTIAL** → a new `intent.md` for the missing part. Ordinary work item.
- **DRIFTED, and the different problem was the right one** → the _plan_ was
  wrong, not the code. Update the build order and CLAUDE.md. **No code change,
  and no work item** — this is the one outcome that produces neither.
- **DRIFTED, and the original problem still stands** → an `intent.md` that
  names both: what was built, and what is still missing.
- **OVERSHOT** → the extra code exists with no intent behind it. Either write
  that intent retroactively as its own work item, or delete the code.
  Unowned code is how a codebase becomes hermes.
- **Anything that contradicts the paradigm** → the serious one. Not a gap,
  debt against the goal. An `intent.md` that says _replace_, and it jumps the
  queue.

Each of these follows CLAUDE.md's maintain-stage shape already: the anomaly
and its evidence, a proposed outcome, the affected systems, the open
questions. Create them with `scripts/artifact.py new <ID> <slug>` like any
other work item.

**Never patch a closed work item.** Its chain records what was decided and
when; editing it to match what the code became destroys the only thing it was
for — and it would destroy the evidence this audit just produced.

## Handling a block that spans many commits

- Read `git show --stat` for all of them before reading any diff. The shape
  tells you where the work actually happened.
- Judge the net effect. Ten commits that build and then partly dismantle
  something have one outcome, not ten.
- A block over roughly six commits is worth reporting at module level rather
  than line level — which modules exist now that did not, and what each one
  is for.
- If a commit in the list does not serve the block's apparent purpose, say so
  under SURPRISES rather than forcing it into the story. That is how a
  mis-scoped work item surfaces.

## Do not

- Show an auditor the problem statement, the block name, or `key.md`.
- Let one agent do both passes.
- Run the audit on a block whose work is still in flight — an unfinished
  block will read as PARTIAL every time, which is noise, not a finding.
- Accept a report without citations. Relaunch it.
- Write `findings.md` and stop. The intents are the deliverable.
- Change any code during the audit. It produces no diff and needs no
  `make verify`.
