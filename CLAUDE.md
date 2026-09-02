# sadana-harness

An agent runtime built from scratch. `../hermes-agent` is a **reference corpus,
not a dependency** — we read it to see what this class of problem already looks
like solved, then decide independently. Nothing here imports from it, and
nothing here should be a copy of it.

WSL only. Python lives in `.venv`; `make` puts it on PATH for you, so there is
nothing to activate.

## How work flows

One work item = one artifact chain = one commit. Six stages, four files:

    plan      intent.md    /plan-skill
    design    spec.md      /design-skill
    build     plan.md      /build-skill      (+ the code diff)
    test      —            no file: evidence, pasted
    deploy    review.md    /deploy-skill
    maintain  —            no file: a NEW intent.md, restarting at plan

Test and Maintain commit nothing of their own, and that is deliberate. Test
produces the literal output of `make verify`, which lands in `review.md` under
`## Evidence` where the person deciding can see it. Maintain produces a
diagnosis written as a new `intent.md`, which re-enters this chain at the top.

Start a work item with `scripts/artifact.py new <ID> <slug>`.
`scripts/artifact.py status` says where you are and what is missing. Artifacts
live in `docs/tasks/<ID>-<slug>/`.

A hook enforces this. Writing `src/` or `tests/` without a valid `plan.md` is
refused, and so is writing a stage's artifact before the previous stage
validates. When you are blocked, the message names the missing thing — fix
that, do not route around it.

Each artifact has a **trace section** that only gets filled if the methodology
actually ran: `Changed during planning`, `Concerns`, `Risks`, `Findings`.
Writing a plausible trace for work you did not do is the one failure this
system cannot detect, and the one that makes the rest of it worthless.

## Verifying your work

- Chain: `make chain` (must end "CHAIN OK")
- Lint: `make lint` (must end "LINT OK")
- Types: `make typecheck` (must end "TYPES OK")
- Test: `make test` (must end "TESTS OK"; all green)

`make verify` runs all four, cheapest first, and ends "VERIFY OK".

Run `make verify` before reporting any task complete, and paste the output — a
summary of the output is not the output. A run that ends without its sentinel
did not pass, however little red there is; an empty suite exits 5 and is a
failure here, not a green run.

That pasted output is the Test stage's whole deliverable. At Deploy it goes
into `review.md` under `## Evidence`; before then it goes in the conversation.
When CI exists it will move to the PR check run and `## Evidence` can go.

If a test fails, fix the code, not the test. Never skip it, mark it xfail,
loosen an assertion or widen a tolerance. If the test itself is wrong, say so
and stop — do not diagnose and repair it in the same move.

## Tests

How to write a test is not in this file. Load the `testing-conventions` skill
before writing, changing or reviewing one — it owns tier, naming, fixtures and
what may be asserted.

Two rules stay here, because they bind every session and not only the ones
writing tests.

**One door.** Never invoke pytest directly. Always `make test`, which runs
`scripts/run_tests.sh` and pins timezone, locale, hash seed and a blanked
credential environment. A bare `pytest` is not a faster test run — it is a
different environment that happens to import the same files.

**Fixing a bug: write the failing test first.** Commit it on its own, red.
Then make it pass without touching it. A test that existed before the fix and
could not be rewritten is the only real proof the bug is gone.

## After you ship

The maintain stage. Nothing is in production yet, so "shipped" means merged to
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

## Things Claude gets wrong here

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
