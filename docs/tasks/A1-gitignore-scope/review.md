# Review: Scope the gitignore to this project (from plan.md 2026-09-02)

Reviewed: working tree vs HEAD (8962dc9) — 1 file changed, +19/-211
(`.gitignore`); `docs/tasks/A1-gitignore-scope/` itself is untracked (this
work item's own artifacts, not part of the diff under review).
Reviewer context: fresh session, cold review — no build-stage context beyond
`intent.md`, `spec.md`, `plan.md` and the diff.

## Evidence

```
docs/tasks/A1-gitignore-scope: all present artifacts valid
CHAIN OK
trim trailing whitespace.................................................Passed
fix end of files.........................................................Passed
mixed line ending........................................................Passed
check for case conflicts.................................................Passed
check yaml...............................................................Passed
check toml...............................................................Passed
check json...............................................................Passed
check for merge conflicts................................................Passed
check for added large files..............................................Passed
check that scripts with shebangs are executable..........................Passed
check that executables have shebangs.....................................Passed
detect private key.......................................................Passed
ruff.....................................................................Passed
ruff-format..............................................................Passed
shellcheck...............................................................Passed
Detect secrets...........................................................Passed
LINT OK
Success: no issues found in 1 source file
TYPES OK
▶ hermetic run: TZ=UTC LANG=C.UTF-8 PYTHONHASHSEED=0, empty env
..                                                                       [100%]
2 passed in 0.01s
TESTS OK
VERIFY OK
```

## Scope

`git diff --stat HEAD` shows exactly one file touched: `.gitignore`
(+19/-211). That matches `plan.md` § Files that change verbatim (it names
only `.gitignore`) — no file the plan named is missing from the diff, and no
file outside the plan was touched. No `src/` or `tests/` file is part of
this change, consistent with the intent (a config-only work item).

The working-tree `.gitignore` content was diffed character-for-character
against the fenced block in `spec.md` § Design — identical, including the
two comments (`# Packaging output — anchored, so src/sadana/build/
survives`, `# Session state, not history`) and line ordering.

## Findings

Three passes run — bugs, security, compliance — plus a scope check against
`plan.md` § Files that change. Detail below; severity roll-up (Important /
Nits) follows the passes.

### Bugs pass
None. There is no code path here — a `.gitignore` has no logic to break,
only patterns to match. Checked every pattern renders as intended (below);
no typo'd glob, no accidental double-negation, no stray `!` reintroducing a
path.

### Security pass
No secrets, keys, or credentials appear in the diff (`detect-secrets` and
`detect private key` both pass in Evidence, and I read the full diff by
eye). The change is strictly a reduction plus a rename/reorganization of
ignore rules — it does not widen what the process can reach or what git
will accept; if anything it tightens things by anchoring `/build/` and
`/dist/`. No PII, no logging concern (not applicable to a `.gitignore`).

### Compliance pass

**spec.md § Acceptance criteria** — all four verified directly, not taken on
faith:

- `git ls-files` before and after: took a real before-snapshot via
  `git stash` / `git ls-files` / `git stash pop`, diffed against the
  after-snapshot — byte-identical. No tracked file became untracked or
  newly ignored. **Satisfied.**
- `git check-ignore -v` for each representative path — ran all twelve named
  in the spec (`.venv/`, `__pycache__/`, `.pytest_cache/`, `.mypy_cache/`,
  `.ruff_cache/`, `build/`, `dist/`, `.env`, `.claude/active-task`,
  `.sadana-bootstrap-backup/`, `.vscode/`, `.DS_Store`); every one matched,
  each citing the exact `.gitignore` line number the spec's design block
  put it on. **Satisfied.**
- `git check-ignore -v src/sadana/build/` — ran it; exit code 1, no output,
  i.e. no match. Anchoring holds: a `build/` inside `src/` is not hidden.
  **Satisfied.**
- `make verify` ends `VERIFY OK` — see Evidence above. **Satisfied.**

**plan.md § Proof** — all three items discharged:

- `git ls-files` before/after byte-identical — discharged above (re-derived
  independently rather than trusted from the plan's claim).
- `git check-ignore -v` output for every acceptance-criteria path plus the
  negative case — discharged above.
- `make verify` output ending `VERIFY OK` — discharged, see Evidence.

**spec.md § Rejected alternatives — checked for drift:**

- Hermes's incident-grown entries (`#38529`-style comments, Electron/Node/
  Playwright/Nix-specific rules): none present in the new `.gitignore`.
  Confirmed by reading the file in full — every comment is a plain category
  header, no issue-number or postmortem references.
- `.venv/venv` anchoring: spec explicitly rejected anchoring these
  (asymmetric with `/build/`/`/dist/` on purpose). The shipped file has
  `.venv/` and `venv/` unanchored — matches the rejected-alternatives
  decision, not the alternative.
- Anticipating unused tooling (coverage, Node, Docker, CI caches): none
  present. Confirmed against `pyproject.toml`/`Makefile` myself — no
  coverage tool, no Node toolchain, no Dockerfile in this repo — so nothing
  here is speculative.

**Design principles:**

- *Learn from reference first* — applied. `spec.md` documents reading
  `../hermes-agent/.gitignore` in full (215 lines) and names specifically
  what was and wasn't adopted (root-anchoring as a pattern: adopted;
  incident-driven entries and unused-tooling entries: declined). The diff
  is consistent with that account — no hermes-specific tooling leaked in.
- *Reduce number of bets* — applied. Spec explicitly declines to anchor
  `.venv/venv` "without a scenario that needs it," and declines to add
  tooling entries ahead of the work item that would introduce them. Fewer
  speculative rules than the file it replaced (211 lines removed, mostly
  dead template entries for tools this repo does not use).
- *More plugins not more core* — not applicable, and spec says why: a
  `.gitignore` is a single flat file with no plugin/extension seam for this
  kind of rule to live behind.
- *Catch scenario at least step-cost* — applied. Spec's rationale (§
  Rejected alternatives, "Least step-cost") argues this edits the cheapest
  existing step (git's own ignore check, pre-`git add`) rather than adding
  a new pre-commit hook or broadening an existing one. Confirmed no
  `.pre-commit-config.yaml` hook was touched by this diff.
- *Minimise mutable state* — applied/not-applicable, per spec's own
  reasoning: a `.gitignore` is static configuration, not stateful data, so
  there's nothing to inventory as stored-vs-derived. No state was added by
  this change.

### Important
None.

### Nits
None — the file matches the spec's design block exactly, and I could not
find anything worth flagging as a preference below Important severity that
isn't either already spec-settled or outside this stage's remit (e.g. the
`*.py[cod]` vs. original template's `*.py[codz]` character-class narrowing
is part of the approved spec content, not something introduced by
implementation — not a review finding against this diff).

## Decision
Approved by Adam, 2026-09-02. No Important findings to resolve.
