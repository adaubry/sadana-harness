# Plan: Scope the gitignore to this project (from intent.md 2026-09-02)

## Files that change

.gitignore

No hermes reference block applies — a `.gitignore` is not production code,
and `spec.md` already settled the design (§ Design, § Rejected alternatives)
against `../hermes-agent/.gitignore` directly. No test file: this work item
introduces no module, so `testing-conventions` does not apply (spec §
Concerns); verification is the acceptance criteria below, run and pasted at
the Test stage.

## Order of work

1. Record the current tracked-file baseline: `git ls-files` (already known
   empty of anything sensitive from the plan-stage interview, but taken again
   here as the literal before-snapshot the first acceptance criterion diffs
   against).
2. Replace the full content of `.gitignore` with the block in `spec.md` §
   Design — nothing added, nothing dropped, no drift back toward the
   rejected alternatives (hermes's incident-grown entries, anchoring
   `.venv`/`venv`, or anticipating unused tooling).
3. Run the cheap, targeted checks first: `git ls-files` again (diff against
   step 1's snapshot must be empty) and `git check-ignore -v` for each path
   in the spec's acceptance criteria, including the negative case
   (`src/sadana/build/` must NOT match).
4. Only once step 3 is clean, run `make verify` — the broad, slower check —
   and confirm it ends `VERIFY OK`.

Ordered this way so a mistake in the new file (a missing entry, an
accidentally-broadened pattern) is caught by a two-second `check-ignore`
call before spending the time on a full `make verify` run.

## Risks

**What could this break?** Nothing in the pre-commit hooks, `make`, or the
test suite reads `.gitignore` — it is consulted only by git itself, at
`git add`/`git status` time. The only thing that depends on its content is
whether files already sitting on disk (`.venv/`, `__pycache__/`,
`.mypy_cache/`, `.pytest_cache/`, `.ruff_cache/`, `.sadana-bootstrap-backup/`)
stay untracked. If the new file failed to cover one of those, the visible
symptom would be `git status` suddenly listing a cache directory as
untracked — which step 3's per-category `check-ignore` catches before it
ever reaches `git add`.

**Most risky step?** Step 2, because it is the only step that changes
anything — a single-file overwrite has no partial-failure state, so the risk
is entirely in the content being wrong (missing an entry, or silently
reintroducing something the spec rejected), not in the mechanics of the
edit. That is why steps 3 and 4 exist as two separate, increasingly broad
checks rather than one: step 3 catches a wrong-content mistake in seconds,
step 4 catches anything step 3's fixed list of paths did not think to check.

**Drift check against `spec.md` § Rejected alternatives:** the plan writes
exactly the block already in `spec.md` § Design — no hermes-style
incident-comments, `.venv`/`venv` left unanchored as specified, no
coverage/Node/Docker entries added ahead of a work item that needs them.

## Proof

- `git ls-files` before and after step 2 are byte-identical (pasted diff,
  empty).
- `git check-ignore -v` output for every path listed in `spec.md` §
  Acceptance criteria, including the `src/sadana/build/` negative case
  (must produce no output).
- `make verify` output ending `VERIFY OK`.
