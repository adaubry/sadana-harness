# sadana-harness

Version 1.01 (03/09/2026)

An agent runtime built from scratch. `../hermes-agent` is a **reference corpus.**
Refer to our methodology for learning from the reference.
Nothing here imports from it, we can sometimes copy it if it speeds up the process.

WSL only. Python lives in `.venv`; `make` puts it on PATH for you, so there is nothing to activate.

## Glossary

- sadana backbone: The foundational architecture of sadana-harness
  establishing its core paradigm constraints, state contracts, and abstraction interfaces.
  It acts as an adaptable substrate (or graft host) capable of absorbing the 21 L1 reference blocks
  from hermes-agent without architectural compromise,
  providing a flexible and robust base to build toward MVP and PMF.
- SDLC: Software development lifecycle
- AI-native SDLC: A reimagined SDLC that works around ai agents with a 6 stage loop,
  AI agents run through the loop and Humans stand above the loop instigating, directing and governing

## Important meta context

sadana-harness is a reconstruction. `../hermes-agent` is a working agent
runtime — 2.3M lines over 27,000 commits you will take inspiration from.

The hermes-agent codebase has previously been measured into two levels: L0 and L1.
L0 is the coarsest partition of the hermes-agent system,
we divided their codebase in 6 chunks, and one of them is what we call L1.
L1 is the core of the hermes-agent system: including test files it reaches >50% of their repo,
we divided L1 into 21 reference blocks, and the order those blocks must be built in is already known.

We've planned to split the SDLC in three phases:

- Phase 1: From nothing to the "sadana backbone"
- Phase 2: From the "sadana backbone" to a MVP (Minimum Viable Product)
- Phase 3: From a MVP to PMF (Product Market Fit)

(current phase: Phase 1)

### During phase 1:

We will focus on scalability of what we create and copy the hermes-agent solutions
without compromising on own core paradigm.
This repository will seem empty, and that is the plan working, not a problem to solve.
We are not discovering what to build. We are rebuilding a shape someone else
arrived at by accident, on purpose, in the order they could not use.
Three consequences you might encounter:

- Early steps might have no callers yet, deliberately
- The order is evidence, not preference
- What should we build first" is already answered

### During phase 2:

We will focus on the features that will actually make the paradigm-shift.

### During phase 3:

We will iterate on our MVP, sand off the rough edges, focus on

### Our methodology for learning from the reference

**We copy decisions** Read the reference to learn how a problem was solved and what it cost,
then try to write our own answer. You are encouraged to copy their codebase if that represents a shortcut,
they have had this codebase for years, the only problem is that it is catered for different end goals.

**If you decide to copy their work** Really ask yourself how this will interfere with our paradigm,
watch out for tightly coupled, hidden assumptions in hermes-agent's codebase that will silently break when
transplanted into our codebase with our different paradigm.

## How work flows

You will apply the AI native SDLC, this framework was built around your capabilities.
1 work item = 1 artifact chain = 1 commit. 4 files, 6 stages:

    plan      intent.md    /plan-skill
    design    spec.md      /design-skill
    build     plan.md      /build-skill      (+ the code diff)
    test      —            no file: evidence, pasted
    deploy    review.md    /deploy-skill
    maintain  —            no file: a NEW intent.md, restarting at plan

Test and Maintain commit nothing of their own. Test produces the literal output of `make verify`,
which lands in `review.md` under `## Evidence` where the person deciding can see it.
Maintain produces a diagnosis written as a new `intent.md`, which re-enters this chain at the top.

Start a work item with `scripts/artifact.py new <ID> <slug>`.
`scripts/artifact.py status` says where you are and what is missing.
Artifacts live in `docs/tasks/<ID>-<slug>/`.

A hook enforces this. Writing `src/` or `tests/` without a valid `plan.md`
is refused, and so is writing a stage's artifact before the previous stage validates.
When you are blocked, the message names the missing thing — fix that, do not route around it.

Each artifact has a **trace section** that only gets filled if the methodology
actually ran: `Changed during planning`, `Concerns`, `Risks`, `Findings`.

Never write a plausible trace for work you did not do.

## Verifying your work

- Chain: `make chain` (must end "CHAIN OK")
- Lint: `make lint` (must end "LINT OK")
- Types: `make typecheck` (must end "TYPES OK")
- Test: `make test` (must end "TESTS OK"; all green)

`make verify` runs all four, cheapest first, and ends "VERIFY OK".

Run `make verify` before reporting any task complete, and paste the output.
A run that ends without its sentinel did not pass, however little red there is;
an empty suite exits 5 and is a failure here, not a green run.

That pasted output is the Test stage's whole deliverable. At Deploy it goes
into `review.md` under `## Evidence`; before then it goes in the conversation.
When CI exists it will move to the PR check run and `## Evidence` can go.

If a test fails, fix the code, not the test.
You can only fix a test created in the same task/work item/artifact only if the test itself is wrong,
then you are authorized to diagnose and repair it in the same move.
If you aren't authorized, escalate to the user.

## Tests

How to write a test is in `testing-conventions` skill, load it
before writing, changing or reviewing one.

### Important rules regarding tests

- **One door.** Never invoke pytest directly. Always `make test`, which runs
  `scripts/run_tests.sh` and pins timezone, locale, hash seed and a blanked
  credential environment. A bare `pytest` is not a faster test run — it is a
  different environment that happens to import the same files.

- **Fixing a bug: write the failing test first.** Commit it on its own, red.
  Then make it pass without touching it. A test that existed before the fix and
  could not be rewritten is the only real proof the bug is gone.

## After you ship

The maintain stage. In phase 1 nothing is in production yet, so "shipped" means merged to
main and the triggers are manual: something you notice, a test that starts
failing, a behaviour that does not match what `intent.md` promised.

Its output is a **new `intent.md`** — the anomaly and its evidence, a proposed
outcome, the affected systems, the open questions — entering this chain at the
plan stage like any other work item. That is why there is no `maintain.md`: a
maintenance finding worth recording is worth a work item, and one that is not
worth a work item is worth a sentence in conversation.

Never patch a closed work item. Its chain records what was decided and when;
editing it afterwards destroys the only thing it was for.

## The reference corpus

`../hermes-agent` is read-only. Writes to it are denied.

The index is `docs/reference/hermes_core_blocks.csv` — 2,368 rows,
`block,tier,kind,filename,path`, one per file in hermes's core, across 21
purpose blocks.

    awk -F, '$1=="CONFIG" && $3=="production-code" {print $5}' \
      docs/reference/hermes_core_blocks_kind.csv

`build-skill` explains the columns and how much of a block to read. Finding
files in that index is your job — never ask the user which files are in a
block.

## Layout and ownership

    src/sadana/      the package
    tests/           unit · integration · contract · eval
    scripts/         run_tests.sh, artifact.py
    docs/tasks/      one directory per work item
    docs/reference/  the hermes index — generated, never hand-edited
    .claude/         skills, hooks, settings

A Make target is one line. The moment it needs branching, environment setup or
error handling it becomes a script in `scripts/`, and the target calls that.

Lint and format belong to pre-commit; ruff's arguments live in
`pyproject.toml`. Never add ruff or formatting flags to the Makefile, and
never add mypy to pre-commit — `make typecheck` owns static types.

## Research Subagents

- When delegating research to a subagent, state the deliverable explicitly: "Return a written report in your final message. Do NOT create or edit any files."
- Require the subagent to cite file paths and line numbers for every claim; a report without citations is incomplete and must be relaunched.
- For reference-implementation reviews (e.g. hermes files), make sure the agent is considering all the relevant files in the scope you gave him, he will find it in /docs/reference/hermes_core_blocks_kind.csv

## Things Claude gets wrong

Add an entry the second time a mistake repeats. Two so far:

- **`import sadana` works because of `pythonpath = ["src"]`** in
  `pyproject.toml`. Do not "fix" an import error with a `sys.path` edit or an
  editable install.
- **Do not activate `.venv`.** The Makefile puts it on PATH. A session that
  activates it and then runs a bare command is testing an environment CI will
  not reproduce.

## Do not

- Write code before `plan.md` exists and the user has approved it.
- Approve your own work or merge on your own judgement. That decision is the
  user's, and it is the only part of this process that cannot be recovered
  afterwards.
- Edit a closed work item's artifacts to match what the code became.
- Copy hermes code. Read it, understand why it is shaped that way, then decide
  for this project.
