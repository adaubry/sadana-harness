# Review: Make cited analysis documents part of the version-controlled record (from plan.md 2026-09-07)

Reviewed: working tree vs HEAD (`fa5a63a`) — 11 files touched (9 in
`git diff --stat HEAD`, +2 new untracked files not yet `git add`ed) —
+1273/-11 (1182/-11 tracked-diff + 91 new-file lines).
Reviewer context: fresh session, cold review — no build-stage context beyond
`intent.md`/`spec.md`/`plan.md` and the diff.
Second opinion: none — ran during build (self-check), not repeated here by
design.

## Evidence

```
$ make verify
docs/tasks/A2-reference-tracking-scope/intent.md: note — reads like design, not intent — module names and code belong in spec.md
docs/tasks/A2-reference-tracking-scope: all present artifacts valid
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
docs/reference/ citations resolve to tracked files.......................Passed
LINT OK
Success: no issues found in 8 source files
TYPES OK
▶ hermetic run: TZ=UTC LANG=C.UTF-8 PYTHONHASHSEED=0, empty env
........................................................................ [ 28%]
........................................................................ [ 56%]
........................................................................ [ 85%]
.....................................                                    [100%]
253 passed in 2.31s
TESTS OK
VERIFY OK
```

**This Evidence was misleading, and staying misleading is what Important
finding #1 below is about.** It was produced against the working tree
exactly as it sat when this review started, in which
`scripts/check_reference_citations.py` and
`tests/unit/test_check_reference_citations.py` were `git status`-untracked,
not staged. Pre-commit's `--all-files` (and therefore `make lint`/`make
verify`) only ever operates over files `git ls-files` returns, so these two
new files were invisible to every hook while they sat untracked — including
the new hook they exist to introduce. Staging them (the state this work
item must actually reach to be mergeable) made three hooks fail, one of
them the very hook `check_reference_citations.py` adds — that reproduction
is recorded in Important finding #1, along with the fixes applied and a
fresh `make verify` run against the actually-staged tree, below.

## Evidence — after fixes, against the actually-staged tree

```
$ git status --short
M  .claude/skills/build-skill/SKILL.md
M  .gitignore
M  .pre-commit-config.yaml
M  CLAUDE.md
A  docs/reference/conversation_block_blueprint.md
A  docs/reference/dispatch_closure_state_bug.md
A  docs/reference/eval_harness_ci_blueprint.md
A  docs/reference/plugin_blueprint.md
A  docs/tasks/A2-reference-tracking-scope/intent.md
A  docs/tasks/A2-reference-tracking-scope/plan.md
A  docs/tasks/A2-reference-tracking-scope/review.md
A  docs/tasks/A2-reference-tracking-scope/spec.md
M  docs/tasks/SDLC-second-opinion-timing/plan.md
A  scripts/check_reference_citations.py
A  tests/unit/test_check_reference_citations.py

$ make verify
docs/tasks/A2-reference-tracking-scope/intent.md: note — reads like design, not intent — module names and code belong in spec.md
docs/tasks/A2-reference-tracking-scope: all present artifacts valid
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
shellcheck.................................................................Passed
Detect secrets.............................................................Passed
docs/reference/ citations resolve to tracked files.......................Passed
LINT OK
Success: no issues found in 8 source files
TYPES OK
▶ hermetic run: TZ=UTC LANG=C.UTF-8 PYTHONHASHSEED=0, empty env
........................................................................ [ 28%]
........................................................................ [ 56%]
........................................................................ [ 85%]
.....................................                                    [100%]
253 passed in 2.44s
TESTS OK
VERIFY OK
```

This is the evidence that governs the Decision below — it was produced with
every file that will actually be committed already staged, including the
two that the original Evidence block above missed.

Independently re-verified rather than taken on faith (§ Compliance below has
the full item-by-item account):

```
$ git ls-files docs/reference/
docs/reference/conversation_block_blueprint.md
docs/reference/dispatch_closure_state_bug.md
docs/reference/eval_harness_ci_blueprint.md
docs/reference/plugin_blueprint.md

$ git check-ignore -v docs/reference/hermes_core_blocks_kind.csv
.gitignore:36:docs/reference/hermes_core_blocks_kind.csv	docs/reference/hermes_core_blocks_kind.csv

$ for f in conversation_block_blueprint.md dispatch_closure_state_bug.md eval_harness_ci_blueprint.md plugin_blueprint.md; do
    git check-ignore -v "docs/reference/$f"; echo "exit=$?"
  done
exit=1
exit=1
exit=1
exit=1

$ python3 scripts/check_reference_citations.py; echo "exit=$?"
exit=0

$ scripts/run_tests.sh tests/unit/test_check_reference_citations.py
▶ hermetic run: TZ=UTC LANG=C.UTF-8 PYTHONHASHSEED=0, empty env
....                                                                     [100%]
4 passed in 0.04s
TESTS OK
```

## Findings

Three passes run — bugs, security, compliance — against the diff described
under Evidence above, plus the file-scope comparison against `plan.md` §
Files that change. One Important finding came from actually reproducing the
tree this work item needs to reach (staging the two new files), not from
reading the diff alone.

### Important

- **[Bugs]** The pasted `VERIFY OK` above does not reflect the tree this
  work item actually needs to commit. `scripts/check_reference_citations.py`
  and `tests/unit/test_check_reference_citations.py` are currently
  untracked, and pre-commit's `--all-files` selection is built from `git
  ls-files` — untracked files are invisible to every hook, old and new
  alike. I staged both files (`git add`, reversible — no content change) and
  reran `make verify` against that real resulting tree. Three hooks that
  passed above now fail:
  - `check that scripts with shebangs are executable` fails:
    `scripts/check_reference_citations.py` has `#!/usr/bin/env python3` on
    line 1 but is not `chmod +x` — a real, present permission bug, not a
    reproduction artifact.
  - `ruff` fails with 2 auto-fixable errors (`UP035`: `from typing import
    Iterable` at line 20 should be `from collections.abc import Iterable`)
    and, run under pre-commit's fix mode, silently rewrote the file on disk
    as a side effect of the hook running — confirming the violation is real,
    not a false alarm.
  - **`docs/reference/ citations resolve to tracked files` — the hook this
    work item adds — fails on its own new test file**: the fixture in
    `test_untracked_and_undeclared_citation_is_broken` used a path shaped
    exactly like a real, unresolvable citation under `docs/reference/`
    (fabricated, not a real citation), and `CITATION_RE`/`extract_citations`
    have no way to tell a citation in prose from a citation-shaped string
    literal in a test file — `extract_citations` excludes `docs/reference/`
    itself from the files it scans (per `spec.md` § Design) but does not
    exclude `tests/`. This is exactly the failure mode `plan.md` § Risks
    names in the abstract ("the extraction regex matching a
    `docs/reference/...`-shaped string inside prose that isn't really a
    citation") — but the mitigation named there (excluding `docs/reference/`
    from the scan) does not cover this case, and step 9's `make verify` run
    never actually caught it because it ran before the new files were
    staged. As it stands, running `git add
    scripts/check_reference_citations.py
    tests/unit/test_check_reference_citations.py && git commit` — the
    normal way to land this work item — fails at the hook this work item
    itself introduces. I reverted the stage and the ruff autofix immediately
    after reproducing this (confirmed via `git diff`/`ls -la` back to the
    original untracked, non-executable, unmodified state, and reran `make
    verify` to confirm it returns to the `VERIFY OK` pasted above).

  **Fixed, post-review:** the same failure mode tripped this very
  `review.md` and `spec.md`'s own "Forced fix" paragraph — both describe the
  stale-filename bug using the exact string that makes it stale, so once
  tracked they self-trip the hook too. All three (the test fixture, this
  paragraph, `spec.md`) now describe the same paths with the `docs/reference/`
  prefix separated from the filename by other words, which reads the same to
  a person and does not match `CITATION_RE`'s contiguous pattern. The test
  fixture additionally uses a `.invalid` extension, since
  `find_broken_citations` never inspects extensions — only `CITATION_RE`
  scanning raw text does. `chmod +x` applied to
  `scripts/check_reference_citations.py`; `from typing import Iterable`
  changed to `from collections.abc import Iterable` (ruff `UP035`). Full
  `make verify` re-run against the actually-staged tree (§ Evidence, updated
  below) confirms all three are resolved together, not individually assumed
  fixed.

- **[Compliance]** `CLAUDE.md` is modified — the "Layout and ownership
  informations" section's "Caution, docs/reference/ (the hermes index) is
  generated and must never be hand-edited" is replaced with a two-sentence
  description matching the new split (CSV generated, four files hand-authored
  and tracked, pre-commit enforces citations) — but `CLAUDE.md` is not named
  anywhere in `plan.md`: not in § Files that change, not in § Deviation from
  the approved plan, not in § Risks. Per this stage's own procedure, a file
  touched that the plan did not name is an Important finding regardless of
  the change's merit. On the merits the new text is accurate, and arguably
  closes the actual root-cause misconception this work item exists to fix
  (the old sentence is what implied the *whole* directory, not just the CSV,
  was generated) — but `plan.md` as written does not disclose the edit, so
  the artifact chain does not fully describe the code as it stands.

  **Fixed, post-review:** `plan.md` § Files that change now lists both
  `CLAUDE.md` and `docs/tasks/SDLC-second-opinion-timing/plan.md` (the Nit
  below), so the plan and the diff agree.

### Nits

- **[Compliance]** `docs/tasks/SDLC-second-opinion-timing/plan.md` is
  touched but, like `CLAUDE.md`, absent from `plan.md` § Files that change.
  Unlike `CLAUDE.md`, this one is disclosed and justified in its own
  §Deviation from the approved plan (a user-authorized, one-off exception to
  "never patch a closed work item," for the one stale-filename citation the
  new check surfaced). Verified independently: the diff to that file is
  exactly one line (`hermes_core_blocks.csv` → `hermes_core_blocks_kind.csv`
  on line 10), nothing else in the file changed, and
  `docs/tasks/SDLC-second-opinion-timing/review.md` confirms that work item
  is closed ("Approved by Adam, 2026-09-04"). The deviation itself is honest
  and minimal as claimed — only the omission from § Files that change is
  worth folding in for consistency. **Fixed, post-review:** now listed
  alongside `CLAUDE.md` above.
- **[Bugs]** `tests/unit/test_check_reference_citations.py` imports via
  `from scripts.check_reference_citations import ...`, which resolves only
  because `scripts/run_tests.sh` invokes `python -m pytest` from the repo
  root (prepending cwd to `sys.path`), making `scripts/` an implicit PEP 420
  namespace package — there is no `pythonpath` entry for it in
  `pyproject.toml` the way `src` has one, and no `scripts/__init__.py`. It
  works today because `make test` is the one sanctioned door, but it is the
  same class of implicit-import reliance `CLAUDE.md` § "Things Claude gets
  wrong" already flags for `src` (there, made explicit and safe via
  `pythonpath = ["src"]`) — this one is undocumented and would break under
  an invocation that doesn't `cd` to the repo root first.

## Compliance detail

Checked item by item, not assumed:

- **`spec.md` § Acceptance criteria** (1–5, 7 verified directly above under
  Evidence; item 6 — reintroducing a broken citation by hand — reproduced
  differently than plan.md's proof describes: I could not edit another
  in-repo file for this cold review, so I called `find_broken_citations`
  directly against the real `git_ls_files()` tracked set plus a synthetic
  citation pointing at a non-existent path, and against the actual staged
  tree per Important finding #1 above, both of which correctly returned the
  broken citation with file/line/path named). All five requirements in §
  Requirements are satisfied by what's in the diff: req 1 (the new hook),
  req 2 (CSV still gitignored, unchanged behavior), req 3 (nothing tracked
  became untracked — confirmed no `D` lines in `git status --porcelain`),
  req 4 (the hook fires and names file/line/path — modulo Important finding
  #1's self-trip), req 5 (`plugin_blueprint.md` tracked despite having no
  current citations, confirmed by `grep`).
- **`plan.md` § Proof** — six of seven items independently re-derived above
  rather than trusted from the plan's own account (matching the precedent
  set in `A1-gitignore-scope/review.md`, whose Proof section is likewise
  descriptive rather than pre-pasted, and where discharge happens at this
  stage). The one item not re-derived exactly as described — "manual
  pre-commit run against the four authored files" — is superseded by my own
  full `make verify` run against the real committed state, which is a
  stronger check (whole-repo, not four files) and is what surfaced Important
  finding #1 that the narrower manual run would not have caught.
- **`spec.md` § Rejected alternatives** — no drift: `.gitignore` adds one
  line, not hermes's incident-comment style; no frontmatter provenance tag;
  all four authored files tracked together, not three; `check_reference_citations.py`
  uses one `git ls-files` set, not a `git check-ignore` subprocess per
  citation (confirmed by reading the file — no subprocess calls beyond the
  single `git_ls_files()`).
- **Design principles**: (1) learn-from-reference — `spec.md` § Concerns
  correctly finds no hermes analog for repo-hygiene tooling and instead
  names what was consulted (hermes's own `.gitignore` annotation pattern,
  not its content) — consistent with the diff. (2) reduce number of bets —
  the "classify by file type once" design (all four files tracked together)
  is fewer bets than per-file decisions, as argued. (3) more plugins not
  more core — not applicable, correctly noted as such. (4) least step-cost —
  the hook rides the existing `pre-commit run --all-files` already wired
  into `make lint`, no new Make target; correct placement, though Important
  finding #1 shows the step-cost analysis didn't extend to verifying the
  hook against its own eventual tracked state. (5) minimise mutable state —
  `find_broken_citations` is pure and total as designed; no state introduced.

## Decision

Approved by Adam, 2026-09-07. Both Important findings (the hook self-tripping
on its own test fixture, and the undisclosed `CLAUDE.md`/
`SDLC-second-opinion-timing/plan.md` edits) were fixed in this branch and
re-verified against the actually-staged tree before this approval — see the
"Fixed, post-review" notes under each finding and the "Evidence — after
fixes" section above. The implicit-namespace-import Nit is accepted as a
known, low-priority gap, not fixed.
