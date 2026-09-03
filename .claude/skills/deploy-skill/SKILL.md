---
name: deploy-skill
description: Run the Deploy stage for a work item — gather the verification evidence, review the change across three passes, take a second opinion from the review commands, reconcile it against intent, spec and plan, write review.md, and stop for a human decision. Use when a work item's code is written and review.md is missing, when the user asks for a review, or before opening a PR.
---

# Deploy stage — review, then hand the decision over

This file is both the policy and the procedure. There is no separate review
policy document: one file cannot drift from itself, and this one is loaded
automatically at the moment the review happens, which a root-level document
would not be. If a second reviewer ever needs the policy — a CI bot, another
agent — split the passes and severity rules back out then, not before.

The code is written and `make verify` is green. Green means the change did not
break anything. It does not mean the change was worth making, matches what was
specified, or is safe to ship. That judgement is this stage.

**You produce findings. You never produce an approval.** The agent that wrote
the code has no way to approve it. Your output is `review.md` with a `Pending`
decision and a request for a human answer.

## Before you start

`plan.md` must exist and validate. If it does not, Build is not finished and
there is nothing here to review against.

There is no `verification.md`. The Test stage commits no file — it produces
evidence, and this stage is where that evidence lands, because the person
deciding needs it in front of them.

## 1. Review cold

You are about to review code you probably wrote. That is the weakest moment in
this whole chain: nothing structural stops a builder approving its own work,
because there is no branch protection here yet.

So buy the separation instead of assuming it. **Run this review in a fresh
session, or delegate it to a subagent, with no context beyond the diff and the
three artifacts.** A reviewer who remembers writing the code reviews its
intentions. A reviewer who reads it cold reviews what it says.

If you are continuing in the same session and cannot delegate, say so in
`review.md` explicitly — under `## Findings`, as a stated limitation of this
review. A weak review that admits it is weak is usable. One that does not is
worse than none.

## 2. Gather the evidence

Run it yourself. Do not carry a result forward from earlier in the session,
and do not trust a claim that it was green.

```bash
make verify
```

Paste the output whole into `## Evidence` — a summary of the output is not the
output. If it does not end `VERIFY OK`, stop: the change is not ready for
review, and saying so is the correct outcome of this stage.

## 3. Establish the scope

```bash
git diff --stat <base>..HEAD
git diff <base>..HEAD
```

Read the whole diff before writing a single finding. Note the file and line
counts — they go in the header.

Then compare the files in that diff against `plan.md` § Files that change.
**A file touched that the plan did not name is an Important finding**, and so
is a file the plan named that the diff does not touch. Nothing self-reports
deviations any more; detecting them here is the job.

## 4. Run the passes

Three passes. Every finding carries its pass as a tag:

- **Bugs** — logic errors, broken edge cases, subtle regressions. Not "this
  could be cleaner": what breaks, on what input, with what result.
- **Security** — injection, authentication gaps, PII in logs, secrets in
  source, anything that widens what the process can reach.
- **Compliance** — the change matches `spec.md`, `plan.md` and the design
  principles. This one is below, because it is the pass only you can run.

## 5. The compliance pass

Everything else in this stage a linter could approximate. This part cannot be
automated, because it compares the code against documents. Work through it
item by item and write down what you checked, not just what you found.

- **Every `## Proof` item in `plan.md` is discharged.** Take them one at a
  time and name what discharges each: a test in the diff, a line in the
  evidence you pasted, a file that now exists. `VERIFY OK` discharges "the
  suite passes" and nothing else — it cannot tell you whether
  `test_config.py` covers the malformed-file case the plan promised. An
  undischarged Proof item is an Important finding.
- **Every item in `spec.md` § Acceptance criteria is satisfied, and you can
  say where.** Name the file and the test.
- **`spec.md` § Rejected alternatives — did the implementation drift back
  toward one?** This is the single most common way a good spec produces bad
  code, and it never announces itself. Re-read that section against the diff
  deliberately.
- **The five design principles**, as `design-skill` applied them. Each is a
  citable compliance finding:
  1. **Learn from the reference first.** Did the change ignore prior art in
     `../hermes-agent` that `spec.md` did not explicitly rule out? A design
     that reinvents without saying why is incomplete, not original.
  2. **Reduce the number of bets.** The cost of a change is fixed; the
     consequence is not. A decision that is expensive to reverse and that
     `spec.md` did not sanction is Important.
  3. **More plugins, not more core** — but only where the plugin
     infrastructure already supports it. Behaviour in the core that belonged
     in a plugin is a finding; so is a plugin seam invented before anything
     can load it.
  4. **Catch the scenario at the least step-cost.** Adding a step to the
     common path to handle an uncommon one is a finding. So is making an
     existing step heavier or harder when a cheaper placement existed.
  5. **Minimise mutable state.** State that could have been a value, a
     parameter or a return value is a finding.

A deviation from `plan.md` is always an Important finding, however small the
code change. The size of the deviation is not the problem; the fact that the
artifact chain no longer describes the code is. Nothing declares deviations
for you — the Test stage writes no file — so the only place they surface is
the diff comparison you did in step 3.

## 6. Second opinion — the review commands

Your own passes are finished and your findings are written down. **Only now**
run the two commands, in this order. Run them earlier and they become an
anchor: you will find what they found. Run them here and they are a check on a
view you already committed to, which is the only way a second opinion is worth
anything.

**First `/ponytail-review`.** Give it the work item and the areas the diff
actually touches — named from `plan.md` § Files that change, not a generic
request:

```
/ponytail-review my work on <short name> — <the two or three areas the diff
touches, in the project's own words>
```

**Then `/simplify`**, once `/ponytail-review` has returned its output. It reads
the same change and proposes reductions.

If a command is not available in this session, say so under `## Findings` as a
stated limitation of the review — the same way a review that could not be run
cold declares itself — and continue. A missing tool is not a reason to skip
the stage.

### Triaging what comes back

Neither command's output is findings. Both produce **candidates**, and every
candidate is sorted before it can appear in `review.md`. Never paste a
command's raw output into the review.

`/simplify` in particular will return more than belongs here, because its job
is to suggest and this stage's job is to be read. Sort each candidate:

- **It removes a bet, drops mutable state, or moves a step off the common
  path** → a Compliance finding, citing design principle 2, 5 or 4. These are
  the ones worth having, and they are why this step exists.
- **It contradicts something `spec.md` § Rejected alternatives already
  settled** → not a finding. One line noting the spec held, and move on.
- **It would change behaviour** → not a finding at all. It is a candidate for
  the next work item's `intent.md`. Say so in a sentence and keep it out of
  `## Findings` entirely; this review is about whether the change matches what
  was decided, not about deciding something else.
- **Anything else** → a Nit, inside the cap of five, or dropped.

If a command duplicates a finding you already made, keep yours and note that
it was confirmed — do not list it twice. If a command **contradicts** one of
yours, that disagreement is the most useful thing a second opinion produces:
resolve it, and put the resolution in the finding rather than dropping either
side silently.

## 7. Severity, and what not to report

**Important** — a defect, a security exposure, or a compliance gap. Something
that should change before merge.

**Nit** — a preference. Cap them at five and drop the rest; a review with
thirty nits and two real findings has hidden the two.

**Do not report at all:**

- Anything `make verify` already catches. Lint, formatting, import order,
  types — the pipeline owns those, and repeating them here trains the reader
  to skim.
- Generated files, lockfiles, vendored code.
- Speculative refactors, "you could also…", architecture you would have
  preferred. If it is a real design objection, it belongs in `spec.md` for the
  next work item, not in a review of this one.

Signal density is the whole product of this stage. A review nobody reads
carefully is review theatre with extra steps — and adding a second opinion in
step 6 raises that risk, not lowers it, unless its output is triaged as hard
as your own.

## 8. Write review.md

`docs/tasks/<ID>-<slug>/review.md`:

```markdown
# Review: <short name> (from plan.md <YYYY-MM-DD>)

Reviewed: <base>..HEAD — N files, +X/-Y
Reviewer context: fresh session | same session as build (limitation noted)
Second opinion: /ponytail-review, /simplify — both ran | <name> unavailable

## Evidence
```

$ make verify
no active task — nothing to check
CHAIN OK
...
LINT OK
TYPES OK
▶ hermetic run: TZ=UTC LANG=C.UTF-8 PYTHONHASHSEED=0, empty env
...... [100%]
6 passed in 0.41s
TESTS OK
VERIFY OK

```

## Findings

### Important
- [Compliance] plan.md § Proof claims test_config.py covers the malformed-file
  case; the diff adds three cases and no malformed-file test.
- [Bugs] resolve_path() returns None for an empty spec; the one caller at
  src/loader.py:41 dereferences it without a check.
- [Compliance] /simplify proposed collapsing the two retry paths in
  client.py:70-96 into one; taking it removes a bet spec.md never sanctioned
  (principle 2). Confirmed against § Rejected alternatives — not one of them.

### Nits
- [Bugs] The retry bound in client.py:88 is a literal; spec.md § Design names
  it as configurable.

### Raised, not findings
- /simplify suggested dropping the empty-response guard entirely. That changes
  behaviour and belongs in a later intent.md, not in this review.

## Decision
Pending — awaiting <name>.
```

Write findings as full sentences with a location and a consequence. "Missing
test" is not a finding; the examples above are. A candidate that came from a
command is written as your finding, in your words, with the command named as
its source — not quoted.

`### Raised, not findings` is optional and belongs only where a command
produced something worth remembering for a later work item. Leave it out
rather than padding it.

**If the change is genuinely clean, say what you checked and found clean** —
name the passes, name the artifacts you reconciled, say what the two commands
returned and why none of it rose to a finding. `## Findings` is the trace of
this stage, and a three-word "No issues found." both fails the validator and
tells the next reader nothing about whether a review happened.

## 9. Stop

Show the findings and ask plainly: **approve, request changes, or tell me what
to look at again.**

Do not write the decision yourself. Do not merge. Do not treat silence, a
thumbs-up, or a follow-up question as approval. `## Decision` stays `Pending`
until a person gives you an answer, and then it records _their_ answer with
their name and the date:

```markdown
## Decision

Approved by Adam, 2026-09-02, with the Important compliance finding fixed in
this branch before merge.
```

Findings do not approve or block on their own. That is the point of the
separation, and it is the only part of this stage that cannot be recovered
later if it is skipped.

## 10. After the decision

If changes are requested, fix them in this branch and update `## Findings` with
what changed — do not open a new work item for your own review findings.

If approved, the PR description carries the chain: link `intent.md`, `spec.md`,
`plan.md` and `review.md`, and list the Important findings with their
resolutions. The evidence is already in `review.md`; when CI exists it moves to
the check run and `## Evidence` can go. The PR is the audit record — everything
a later reader needs to reconstruct why this change exists and who agreed to it
should be reachable from it without opening a session transcript.

## Do not

- Write your own approval, or merge on your own judgement.
- Review in the same session that wrote the code without saying so.
- Run `/ponytail-review` or `/simplify` before your own passes are written
  down. Their value is as a check, and a check that arrives first is an anchor.
- Paste a command's raw output into `## Findings`, or let a simplification that
  changes behaviour enter the review at all.
- Report anything `make verify` already enforces.
- Let nits outnumber Important findings.
- Skip the compliance pass because the tests are green — green tests and a
  spec-compliant change are different claims.
- Leave `## Decision` as `Pending` and call the stage done.
