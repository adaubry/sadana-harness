---
name: build-skill
description: Run the Build stage for a work item — plan the implementation against the reference corpus, interrogate the plan until it stands on its own, get approval, then implement. Use when a work item's spec.md is complete and plan.md is missing, when the user asks to plan or start an implementation, or when the user names a hermes block to work from.
---

# Build stage — plan first, implement second

Two phases with a hard stop between them. Phase one produces `plan.md` and
ends when the user approves it. Phase two writes code. **Never begin phase two
without an explicit approval in this conversation.**

## Phase one — the plan

### 1. Enter plan mode

Say so, then enter it. Plan mode lets you read and interrogate without
editing, which is the whole point of this phase.

If you cannot enter it, continue anyway — the chain gate refuses writes to
`src/` and `tests/` while `plan.md` is missing or invalid, so the enforcement
does not depend on the mode. Plan mode is what stops you _drifting_ into code
mid-thought; the gate is what stops you succeeding at it.

### 2. Locate the prior art

Read `intent.md` and `spec.md` for this work item. Then find the reference
files that already solve something like this.

**The index is `docs/reference/hermes_core_blocks.csv`** — 2,368 rows,
one per file in the reference corpus's core, with columns:

| column     | meaning                                                                                                                                                                                                                                                                                                           |
| ---------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `block`    | one of 21 purpose blocks — `CONFIG`, `CONVERSATION`, `MODEL-ACCESS`, `CONTEXT`, `SESSION-STORE`, `GATEWAY-DAEMON`, `CHANNELS`, `CLIENT-SURFACE`, `CLI-SHELL`, `EXECUTION`, `BROWSER`, `MEDIA`, `LEARNING`, `DELEGATION`, `SCHEDULING`, `SAFETY`, `PLUGIN-SYSTEM`, `OBSERVABILITY`, `PERSONA`, `LIFECYCLE`, `DOCS` |
| `filename` | basename                                                                                                                                                                                                                                                                                                          |
| `path`     | path inside `../hermes-agent`                                                                                                                                                                                                                                                                                     |

```bash
# every production file in a block
awk -F, '$1=="CONFIG" && $3=="production-code" {print $5}' \
  docs/reference/hermes_core_blocks.csv

# how big is a block, really
awk -F, 'NR>1 && $3=="production-code" {c[$1]++} END{for(b in c) print c[b], b}' \
  docs/reference/hermes_core_blocks.csv | sort -rn
```

No field contains a comma, so `awk -F,` is safe. If only the three-column
`hermes_core_blocks.csv` is present, the columns are `block,filename,path`

**When the user names a block, load it before asking anything.** List its
production files, then read the three to six whose names match the concern in
`spec.md`. Do not read the whole block. Report what you found: the file, the
pattern, and whether it is worth adopting.

Finding facts is your job. The user should never be asked for something in
that CSV.

### 3. Draft the plan

```markdown
# Plan: <short name> (from intent.md <YYYY-MM-DD>)

## Files that change

path/one.py (new), path/two.py, tests/test_two.py

## Order of work

1. …
2. …

## Risks

## Proof
```

**Files that change** — every file, with `(new)` where it does not exist yet.
Tests included; a plan that omits its tests is a plan that will not produce
them.

**Order of work** — a numbered sequence, each step landing something that can
be run. Not a task list: an order, chosen so the risky part comes after
something already works.

**Proof** — the specific evidence, naming files and states. "Tests pass" is
not proof. "test_config.py covers override, fallback, missing key and
malformed file; `make verify` green" is.

### 4. Interrogate it

Do this to your own draft, out loud, before showing it. Three questions:

- **What could this change break?** Not the code you are adding — what
  _already works_ that this could disturb. Existing callers, shared fixtures,
  the environment other tests assume. Answer with named things.
- **Which step is the most risky, and why that one?** There is always one.
  If every step feels equally safe, the ordering has not been thought about.
  Then: can the order be changed so the risky step lands after something
  proven?
- **Which options did the spec already reject, and is this plan drifting back
  toward one?** Re-read `## Rejected alternatives` in `spec.md`. An
  implementation that quietly reinstates a rejected design is the most common
  way a good spec produces bad code, and it never announces itself.

The answers go in `## Risks`. That section is the trace of this stage — a plan
nobody questioned has thin risks, and the validator will say so.

### 5. Iterate to the handover bar

**Could an engineer who has never seen this conversation implement the change
from the plan alone?** Read the draft as that person. Every time you rely on
something said in the conversation but not written down, write it down.

The usual gaps: a file listed with no indication of what changes inside it; a
step whose order depends on a reason only you know; "the existing pattern"
with no path; a risk stated without its mitigation.

### 6. Write it and stop

Write `docs/tasks/<ID>-<slug>/plan.md`, run `scripts/artifact.py check`, fix
what it reports, then **show the plan and ask for approval.**

Ask plainly: _approve this plan, or tell me what to change._ Do not start
implementing. Do not treat silence, enthusiasm, or a follow-up question as
approval.

## Phase two — implement

Only after approval.

Work the numbered steps in order. After each one, run the narrow check for
what you touched, not the full suite — that is what the narrow commands in
`CLAUDE.md` are for.

**If the plan turns out to be wrong, stop and say so.** Do not quietly
implement a different plan. A deviation you announce is a plan amendment; a
deviation you hide is what `verification.md` will have to explain later, and
by then it costs more.

Once every numbered step is done: run `/ponytail-review` and `/simplify`
against your own diff. Sort what comes back — worth taking now, a future
work item's problem, or something `spec.md` already settled — and act on
the first bucket in this same diff:

- **Worth taking now** — it removes a bet, drops mutable state, or moves a
  step off the common path. Apply it here.
- **A future work item's problem** — it would change behaviour. Note it in
  a sentence when you report the work; don't act on it now, and don't let
  it expand this diff.
- **Already settled** — it contradicts something `spec.md` § Rejected
  alternatives already decided. One line noting the spec held, move on.

Then run `make verify` and report both: what the self-check found and did,
and the verify output.

When the code is done, the Test stage takes over. That is a separate skill
and a separate artifact.

## Do not

- Write code before approval.
- Ask the user for anything the CSV can answer.
- Read a whole reference block instead of the files that matter.
- List files without their tests.
- Claim "tests pass" as proof.
- Report the work done without having run the self-check and `make
  verify`, in that order.
- Let the implementation drift back to something `spec.md` rejected.
