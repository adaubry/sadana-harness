# Spec: Scope the gitignore to this project

Intent: docs/tasks/A1-gitignore-scope/intent.md

## Requirements

1. Every remaining line in `.gitignore` traces to something this project's
   own tooling actually produces (venv, bytecode/tool caches, packaging
   output, secrets/env files, the session-state file, bootstrap backups), or
   to a minimal editor/OS allowance — not to a tool this project does not
   use. Traces to intent § Proposed outcome.
2. Patterns for packaging output (`build`, `dist`) are anchored to the repo
   root, so a same-named directory inside `src/` is never hidden. Traces to
   intent § Problem.
3. No file currently tracked by git becomes excluded by the new rules —
   already verified true of the target content below; re-verified as
   acceptance criterion 1. Traces to intent § Constraints.

## Design

`.gitignore` is replaced in place — same path, new content, no code, no new
file. Final content:

```
# Environments
.venv/
venv/

# Bytecode and tool caches
__pycache__/
*.py[cod]
.pytest_cache/
.mypy_cache/
.ruff_cache/

# Packaging output — anchored, so src/sadana/build/ survives
/build/
/dist/
*.egg-info/

# Secrets
.env
.env.*

# Session state, not history
.claude/active-task

# Bootstrap backups
.sadana-bootstrap-backup/

# Editor / OS
.vscode/
.idea/
.DS_Store
```

Every entry was checked against what this repo's own tooling can actually
produce, read from `pyproject.toml` and `Makefile` rather than assumed:
`.venv/` (the project interpreter, per `CLAUDE.md`'s "do not activate
`.venv`" rule), `__pycache__/`/`*.py[cod]` (interpreter bytecode),
`.pytest_cache/` (`make test`), `.mypy_cache/` (`make typecheck`),
`.ruff_cache/` (`make lint`, pre-commit's ruff hook), `/build/`, `/dist/`,
`*.egg-info/` (the `setuptools` build backend in `pyproject.toml`), `.env` /
`.env.*` (no secret loading exists yet, but this is the standard shape and
costs nothing to hold open), `.claude/active-task` and
`.sadana-bootstrap-backup/` (carried over unchanged from the current file —
both are this harness's own runtime state). No coverage, Hypothesis, or
Node-derived output exists in the toolchain (checked `pyproject.toml` and
`Makefile`), so none of those families are listed.

`.venv/` and `venv/` are deliberately left unanchored, asymmetric with
`/build/` and `/dist/`. See § Rejected alternatives.

## Interface

Not applicable — a `.gitignore` is input to git, not a component with a
call boundary. Its "inputs" are candidate paths at `git add` time; the only
externally-observable behaviour is whether a given path is ignored, which
is what the acceptance criteria check directly.

## Acceptance criteria

- [ ] `git status` after the change shows no currently-tracked file becoming
      untracked or newly ignored (`git ls-files` before and after is
      identical).
- [ ] `git check-ignore -v` reports a match for one representative path per
      category: `.venv/`, `__pycache__/`, `.pytest_cache/`, `.mypy_cache/`,
      `.ruff_cache/`, `build/` (repo root), `dist/` (repo root), `.env`,
      `.claude/active-task`, `.sadana-bootstrap-backup/`, `.vscode/`,
      `.DS_Store`.
- [ ] `git check-ignore -v src/sadana/build/` reports **no** match — the
      anchoring requirement (criterion 2) holds even though the directory
      does not exist yet; `check-ignore` does not require the path to exist.
- [ ] `make verify` still ends `VERIFY OK`.

## Non-goals

- Anticipating tooling no work item has introduced yet (Node, coverage,
  Docker, CI-specific caches). Add those when the work item that introduces
  the tool lands, not speculatively.
- Any change to what pre-commit or CI checks — this only changes what git
  offers them to check.

## Rejected alternatives

**Reference corpus.** `../hermes-agent/.gitignore` (215 lines) is the
closest analogue and was read in full. Declining to adopt its shape: it
grew reactively, one incident at a time, over years — more than a dozen
entries cite issue numbers or postmortems (`#38529`, `#66189`, `#70552`,
`#72002`, `#8`/`#COMMIT-1`), and most of it encodes tooling this project
does not have (Electron/`apps/desktop`, Node/`node_modules`, Playwright,
Nix, a CLI installer with its own runtime-state files). Importing any of
that would directly violate requirement 1 — it would stop being traceable
to this project's own tooling. One pattern from it is adopted:
root-anchoring build output (hermes anchors `/venv/`, `/bin/` the same way
this spec anchors `/build/` and `/dist/`) — the same hazard, independently
converged on. Its negation-pattern technique (`!path` to claw back a file
under an otherwise-ignored directory) is noted but not used — nothing in
this project needs it yet, and inventing the need would be scope creep past
the intent.

**Anchoring `.venv/`/`venv/` to the repo root, for symmetry with
`/build/`/`/dist/`.** Declined. The hazard the anchoring requirement exists
for is a *generic* name doubling as legitimate source-tree content —
plausible for `build`/`dist` (arbitrary output-directory names) but not for
`.venv`/`venv`, which are virtualenv-specific and match nothing else this
project's layout would ever legitimately contain under `src/` or `tests/`.
Anchoring them would cost nothing today but is a bet this spec declines to
add without a scenario that needs it (design guideline 2 — reduce the
count of decisions, not just their size).

**Reduce the number of bets / plugin seam (guideline 2).** No plugin or
per-work-item extension point exists for ignore rules, and this work item
does not sit near one — a single flat file is the only shape this problem
has. Not applicable beyond: the design does not foreclose future entries,
since appending a line is always an addition, never a modification.

**Least step-cost (guideline 3).** The scenario is "an unwanted file gets
tracked." This edits the content of an existing, cheapest-possible step
(git's own ignore check, which runs before `git add` ever stages anything)
rather than adding a step (a new pre-commit hook) or making an existing
step heavier (broadening what `detect-secrets` or `check-added-large-files`
scan). Those hooks stay exactly as they are — this spec only makes the
earlier, cheaper step actually cover what the later steps would otherwise
have to catch.

**Minimise mutable state (guideline 4).** No state is introduced. A
`.gitignore` is static configuration, not data with a lifetime — there is
nothing here to inventory as stored-versus-derived.

## Concerns

None outstanding. Confidence: the interface is a single static file with no
callers to break, the acceptance criteria are directly checkable with
`git check-ignore`, and `project-structure` and `reference-lookup` policy
skills referenced by `design-skill` do not exist in `.claude/skills/` in this
repo (only `build-skill`, `deploy-skill`, `design-skill`, `plan-skill`,
`testing-conventions` do) — neither applies. `testing-conventions` does not
apply either: it governs `tests/*.py` files, and this work item introduces
no module and no test file; verification is the acceptance criteria above,
run and pasted at the Test stage per `CLAUDE.md`.
