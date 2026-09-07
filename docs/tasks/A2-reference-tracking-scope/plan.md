# Plan: Make cited analysis documents part of the version-controlled record (from intent.md 2026-09-07)

## Context

Build stage for **A2-reference-tracking-scope**. No hermes reference block
applies — `spec.md` § Concerns already establishes this is repo-hygiene
tooling, not agent-runtime code, the same finding `A1-gitignore-scope` made
for the same reason. `spec.md`'s own Design section is unusually explicit
(exact `.gitignore` content, exact script shape, exact hook YAML), so this
plan does not re-derive it — it sequences it and adds one thing `spec.md`'s
Design section left unaddressed: `.pre-commit-config.yaml`'s **top-level**
`exclude:` key (separate from adding a new hook to `repos:`) currently
blanket-skips every existing hook — formatting, secret-scanning, all of it —
for the whole `docs/reference/` directory, on the reasoning (its own
comment) that it holds "a corpus you did not write." Once four of its five
files are tracked as hand-authored source, that reasoning no longer covers
them, and leaving the exclude as-is would mean they are tracked in name
only, never actually linted like other source. Checked by hand before
committing to this: with the exclude narrowed to just the CSV, every
existing hook already passes cleanly against all four files with zero
changes needed (pasted in § Proof). This is a mechanical implementation
detail, not a new design decision, and does not touch any `spec.md` §
Rejected alternatives.

## Files that change

- `.claude/skills/build-skill/SKILL.md` — fix 4 occurrences of the stale
  `hermes_core_blocks.csv` citation to `hermes_core_blocks_kind.csv`.
- `scripts/check_reference_citations.py` (new) — `find_broken_citations`
  (pure), `extract_citations` and `git_ls_files` (I/O), `main()`, per
  `spec.md` § Design.
- `tests/unit/test_check_reference_citations.py` (new) — exercises
  `find_broken_citations` only, with fabricated `Citation`/`tracked`
  values.
- `.gitignore` — replace the `# References` / `docs/reference/*` block with
  the single annotated `docs/reference/hermes_core_blocks_kind.csv` line.
- `.pre-commit-config.yaml` — narrow the top-level `exclude:` regex from
  `docs/reference/` to `docs/reference/hermes_core_blocks_kind\.csv`, and
  add the new `check-reference-citations` local hook.
- `docs/reference/conversation_block_blueprint.md`,
  `docs/reference/dispatch_closure_state_bug.md`,
  `docs/reference/eval_harness_ci_blueprint.md`,
  `docs/reference/plugin_blueprint.md` — newly tracked, content unchanged.
- `CLAUDE.md` — replace the "Caution, docs/reference/ ... is generated and
  must never be hand-edited" sentence with the two-sentence version naming
  the CSV specifically and the new pre-commit check, per the amendment the
  user approved during the design stage.
- `docs/tasks/SDLC-second-opinion-timing/plan.md` — one-line fix, forced by
  the new check and authorized as a one-off exception to "never patch a
  closed work item"; see § Deviation from the approved plan below.

## Order of work

1. Fix `.claude/skills/build-skill/SKILL.md`'s stale filename. Independent
   of everything else, zero risk, verifiable by grep alone.
2. Write `scripts/check_reference_citations.py` and
   `tests/unit/test_check_reference_citations.py` together. Run the new
   test file narrowly (`make test` filtered to it, or the runner's
   equivalent) to prove the pure function correct before it is wired into
   anything that gates a commit.
3. Edit `.gitignore` per `spec.md` § Design.
4. Narrow `.pre-commit-config.yaml`'s top-level `exclude:` to the CSV only.
5. `git add` the four authored files — only now that neither `.gitignore`
   nor pre-commit's `exclude` blocks them.
6. Add the new `check-reference-citations` local hook to
   `.pre-commit-config.yaml`.
7. Run `python3 scripts/check_reference_citations.py` by hand against the
   resulting tree. Must exit 0.
8. Prove the negative case by hand: temporarily reintroduce one broken
   citation (revert step 1's fix in a scratch copy, or drop one file from
   the `git add`), confirm the script exits 1 and names that exact file,
   line and path, then restore the fixed state.
9. `make verify` — the full suite, since `.pre-commit-config.yaml` changed
   and `make lint` runs `pre-commit run --all-files`.

Ordered so the one step that can affect every future commit (6, wiring the
hook in) lands only after the pure function is already proven correct in
isolation (step 2) and the tree it will run against is already in its
final, passing shape (steps 1, 3-5) — the hook's first real run (step 7) is
a confirmation, not a first attempt.

## Risks

**What could this change break?** Nothing currently exercises
`docs/reference/` content — it was fully excluded from every hook, so
narrowing that exclude is new exposure, not a regression, and step 4 above
already checked by hand that nothing there fails any existing hook today.
`check-added-large-files`'s 1024KB cap is the one hook that could plausibly
reject a tracked doc; the four files are 44751, 9270, 13136 and 24731
bytes — all well under, checked explicitly, not assumed. No existing test
imports or reads `scripts/check_reference_citations.py`, so nothing else
can be disturbed by adding it.

**Most risky step, and why:** step 6, wiring the new hook in. It is the
only step whose mistake mode is "every future commit fails," not "this one
file is wrong." The specific failure this design is exposed to: the
extraction regex matching a `docs/reference/...`-shaped string inside prose
that isn't really a citation (a code fence, an example, one blueprint
quoting another). Mitigated two ways: `spec.md`'s own design already
excludes `docs/reference/` itself from the files `extract_citations` scans,
so the blueprint docs cannot trip the check on their own cross-references;
and steps 7-8 prove the script by hand, both the positive and negative
case, before `make verify`'s automated pre-commit run is trusted as the
only evidence.

**Drift check against `spec.md` § Rejected alternatives:** not reinstating
hermes's `.gitignore` wholesale, not building a frontmatter provenance
schema for the five files, not tracking only the three currently-cited
files (all four move together), not shelling out to `git check-ignore`
once per citation (the script builds one `git ls-files` set and checks
membership in memory). This plan's one addition beyond `spec.md`'s Design
— narrowing the top-level pre-commit `exclude:` — is not a rejected
alternative revisited; `spec.md`'s Design section did not address that key
at all.

## Deviation from the approved plan

Step 7's first real run surfaced a violation neither `spec.md` nor this
plan anticipated: `docs/tasks/SDLC-second-opinion-timing/plan.md:10` cites
the same stale `hermes_core_blocks.csv` filename as `build-skill/SKILL.md`
— but that work item is closed (`review.md`, "Approved by Adam,
2026-09-04"), and `CLAUDE.md` forbids patching a closed work item's
artifacts. Left alone, this one citation would fail the new hook on every
future commit for a reason unrelated to anything in this diff. Put to the
user directly rather than decided here: they authorized a one-off,
explicit exception to the no-patch rule for this single line, over the
alternatives of a named exemption in the checker or a blanket exemption for
all closed work items' artifacts. `docs/tasks/SDLC-second-opinion-timing/plan.md`
line 10 now reads `hermes_core_blocks_kind.csv`; nothing else in that file
changed.

## Proof

- `tests/unit/test_check_reference_citations.py`, run narrowly, before
  wiring — pasted output.
- `git ls-files docs/reference/` — exactly the four authored files, CSV
  absent.
- `git check-ignore -v` against all five files under `docs/reference/` —
  matches only the CSV, citing the new `.gitignore` line; no match on the
  other four.
- Manual pre-commit run against the four authored files with the exclude
  narrowed, confirming every hook already passes with no changes needed:

  ```
  trim trailing whitespace.................................................Passed
  fix end of files.........................................................Passed
  mixed line ending........................................................Passed
  check for case conflicts.................................................Passed
  check for merge conflicts.................................................Passed
  check for added large files..............................................Passed
  check that scripts with shebangs are executable..........................Passed
  detect private key........................................................Passed
  Detect secrets.............................................................Passed
  ```

- `python3 scripts/check_reference_citations.py` — exit code and (empty)
  output against the fixed tree.
- One pasted negative-case run — exit 1, naming the exact file, line and
  path of a deliberately reintroduced broken citation — with confirmation
  it was reverted immediately after.
- `make verify` output ending `VERIFY OK`.
