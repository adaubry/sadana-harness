# Spec: Make cited analysis documents part of the version-controlled record

Intent: docs/tasks/A2-reference-tracking-scope/intent.md

## Requirements

1. Every path under `docs/reference/` that tracked content cites resolves to
   a file also tracked in git, with exactly one declared exception. Traces
   to intent § Proposed outcome ¶1.
2. `docs/reference/hermes_core_blocks_kind.csv` — the only file under
   `docs/reference/` that is actually regenerated from `../hermes-agent` —
   stays excluded from version control, unchanged in behavior. Traces to
   intent § Constraints ¶1 and `CLAUDE.md`'s existing "generated and must
   never be hand-edited" rule.
3. No file currently tracked in git becomes untracked as a side effect.
   Traces to intent § Constraints ¶2.
4. A check runs on every commit that fails, naming the citing file, line,
   and the unresolved path, whenever tracked content cites a
   `docs/reference/` path that is neither tracked nor the requirement-2
   exception. Traces to intent § Proposed outcome ¶2 and § Affected users
   and systems (pre-commit as the enforcing system).
5. `docs/reference/plugin_blueprint.md` — hand-authored, currently cited by
   nothing tracked — is tracked now, on the same basis as the other three
   authored files, not left pending until something happens to cite it.
   Resolves intent § Open questions.

## Design

**`.gitignore`.** Delete the `# References` / `docs/reference/*` block
added in `7d3445e`. Replace it with a single line naming the one file it
was ever accurate for:

```
# The hermes core-block index is regenerated from ../hermes-agent and must
# never be hand-edited or trusted stale (CLAUDE.md, "The reference corpus").
# Every other file under docs/reference/ is hand-authored analysis and is
# tracked like any other source.
docs/reference/hermes_core_blocks_kind.csv
```

This follows the pattern the reference corpus itself uses for its own
generated artifacts — one ignore entry per generated file, the comment
naming the exact thing that produces it — rather than a directory-wide
wildcard that cannot distinguish an artifact's provenance from its
neighbours'. See § Rejected alternatives.

**Tracking.** `git add` the four hand-authored files:
`conversation_block_blueprint.md`, `dispatch_closure_state_bug.md`,
`eval_harness_ci_blueprint.md`, `plugin_blueprint.md`. Each is added once,
as-is; none is modified by this work item beyond one fix below.

**Forced fix.** `.claude/skills/build-skill/SKILL.md` cites a stale
filename under `docs/reference/` — `hermes_core_blocks.csv`, missing
`_kind`, and describing a 3-column shape the real 5-column CSV does not
have — a rename that was never propagated, unrelated to the gitignore bug
this work item was opened for. Turning on requirement 4's check surfaces it immediately, and a check
that fails on the commit that introduces it cannot ship. The fix is the
narrowest one available: correct the four occurrences of the filename in
that file to `hermes_core_blocks_kind.csv`. Nothing else in that skill
changes.

**The check** — new file, `scripts/check_reference_citations.py`, split the
way `scripts/artifact.py` already splits itself internally (pure functions
separate from the I/O that feeds them, one file, no new module boundary):

```python
Citation = NamedTuple("Citation", file=str, line=int, path=str)

CITATION_RE = re.compile(r"docs/reference/[\w.-]+\.(?:md|csv)")
ALLOWED_UNTRACKED = {"docs/reference/hermes_core_blocks_kind.csv"}

def find_broken_citations(
    citations: Iterable[Citation], tracked: set[str]
) -> list[Citation]:
    """Pure: no git, no filesystem. Testable with fake input."""
    return [c for c in citations
            if c.path not in tracked and c.path not in ALLOWED_UNTRACKED]

def main() -> int:
    tracked = set(git_ls_files())            # I/O: one `git ls-files` call
    citations = list(extract_citations(tracked))  # I/O: read each tracked
                                                   # file's text, minus
                                                   # docs/reference/ itself
    broken = find_broken_citations(citations, tracked)
    for c in broken:
        print(f"{c.file}:{c.line}: cites {c.path}, which is not tracked "
              f"and not the declared generated exception")
    return 1 if broken else 0
```

`extract_citations` excludes `docs/reference/` itself from the files it
scans — the corpus being cited should not also be scanned for
self-citations — and is I/O (`git grep`-equivalent read of every other
tracked file), kept separate from the pure filter above for the same
reason `artifact.py` keeps `sections_of`/`validate` apart from its
`cmd_*` functions.

**Wiring.** A new `local` hook in `.pre-commit-config.yaml`:

```yaml
  - repo: local
    hooks:
      - id: check-reference-citations
        name: docs/reference/ citations resolve to tracked files
        entry: python3 scripts/check_reference_citations.py
        language: system
        pass_filenames: false
        always_run: true
```

No Makefile change: `make lint` already runs `pre-commit run --all-files`
(`Makefile:26-27`), so this hook is live under `make lint` and `make
verify` the moment it exists in the config. `make chain` is untouched —
`scripts/artifact.py check` validates a single active work item's own
four stage artifacts; a repo-wide citation scan is a different scenario
(any tracked file, not just the current task's), and does not belong
folded into that command. See § Rejected alternatives.

**Test.** `tests/unit/test_check_reference_citations.py`, named after the
module per `testing-conventions`, exercises only `find_broken_citations`
with fabricated `Citation`/`tracked` values — no real git call, no real
filesystem, per "what a unit test may touch."

## Interface

- `scripts/check_reference_citations.py`: no arguments, no config file.
  Exit 0 and silent on success (matching the other pre-commit hooks already
  in the config, which are silent unless they have something to say). Exit
  1 with one line per violation on failure, each naming the citing file,
  its line number, and the unresolved path.
- `find_broken_citations(citations, tracked) -> list[Citation]`: pure,
  total, no exceptions raised — an empty `citations` or `tracked` input is
  a valid, boring case (no citations found, or nothing tracked yet), not
  an error.

## Acceptance criteria

- [ ] `git ls-files docs/reference/` lists exactly the four authored files
      and not the CSV.
- [ ] `git check-ignore -v docs/reference/hermes_core_blocks_kind.csv`
      matches, citing the new `.gitignore` line.
- [ ] `git check-ignore -v` on each of the four authored files produces no
      match.
- [ ] `python3 scripts/check_reference_citations.py` exits 0 against the
      resulting tree.
- [ ] Reintroducing one broken citation by hand (temporarily reverting the
      `build-skill/SKILL.md` fix, or temporarily re-gitignoring one authored
      file) makes the script exit 1 and name that exact file, line, and
      path — proven once, then reverted back to the fixed state.
- [ ] The new unit test exercises both a resolvable and a broken citation
      against `find_broken_citations` and passes.
- [ ] `make verify` ends `VERIFY OK`.

## Non-goals

- Reproducing or auditing whatever process originally generated
  `hermes_core_blocks_kind.csv` — no such script exists in this repo today;
  flagged in § Concerns, not built here.
- A general broken-link checker for citations outside `docs/reference/` —
  this catches the one scenario the intent names.
- Any change to how or when a blueprint document gets authored during a
  work item (intent § Constraints ¶3).

## Rejected alternatives

**Adopting hermes's `.gitignore` wholesale, or its incident-comment style
generally.** Already rejected once, in `A1-gitignore-scope/spec.md` §
Rejected alternatives, for the same reasons (issue-number comments, tooling
this project does not have); not re-litigated here. What *is* adopted from
it — one entry per generated artifact, comment naming its generator — is
a narrow, specific pattern, not the file wholesale.

**Extending `scripts/artifact.py check` to also scan arbitrary source for
`docs/reference/` citations**, instead of a new script. Declined:
`artifact.py` validates one active work item's four stage files; this
scenario is "does any tracked file, task artifact or not, cite something
unresolvable" — a different scope entirely. Folding it in would make an
already-frequently-run function heavier with a concern it was never
designed to catch (guideline 3 — least step-cost means finding the
cheapest *correct* step, not the nearest existing one).

**A `provenance: generated|authored` frontmatter tag per file under
`docs/reference/`, checked by tooling.** Declined. Five files, one
exception. A metadata schema for a two-way split of five files is a bet
(guideline 2) — a shape, a place it can drift from the file it describes,
a reader who has to learn it — with no second scenario yet that needs
per-file granularity beyond the one hardcoded exception the script already
holds.

**Tracking only the three files current citations already point at,
leaving `plugin_blueprint.md` gitignored until something cites it.**
Declined: this is the same mistake shape as the bug this work item exists
to close — deciding file-by-file, at the moment something happens to point
at it, is exactly how `conversation_block_blueprint.md` ended up never
tracked at all. Classifying by file *type* (hand-authored vs. regenerated)
once, rather than re-deciding per file per citation, is fewer bets
(guideline 2), and directly serves the outcome's recurrence half: there is
nothing left to notice or re-decide the next time something cites it.

**One `git check-ignore` subprocess per citation**, rather than one
`git ls-files` snapshot compared in memory. Declined: N subprocess spawns
for N citations versus one, for the same answer — `git ls-files`'s output
is exactly the tracked-set membership test `find_broken_citations` needs,
and keeping that lookup as an in-memory `set` (guideline 4) is what makes
the core function pure and testable without shelling out at all.

## Concerns

**Guideline 1 (learn from reference).** No hermes core block is analogous
to this — it is repo-hygiene tooling, not agent-runtime code, matching
`A1-gitignore-scope`'s own finding that this class of work has no hermes
block to learn from. What was consulted instead: hermes's own `.gitignore`
comments for its generated artifacts (e.g. `automation-blueprints-index.json`,
annotated with the exact script that produces it) — the annotation
*pattern* is adopted, not any of its actual entries.

**Guideline 2 (plugin seam).** This work item is nowhere near the plugin
seam — no plugin infrastructure exists yet, and nothing here is agent- or
skill-facing runtime behavior. The "before the seam exists" caveat applies
trivially: nothing here forecloses a future plugin system, because nothing
here touches where one would attach.

**No `project-structure`, `reference-lookup`, or security/brand/UX skill
exists in `.claude/skills/`** — same finding `A1` and `B2` already made;
none applies here either.

**Provenance of the generated exception is undocumented outside prose.**
`hermes_core_blocks_kind.csv`'s "generated" status rests entirely on
`CLAUDE.md`'s word and the commit message that untracked it — no script in
this repo currently reproduces it. If it is ever lost, regenerating it
depends on someone remembering how it was originally built. Named here,
not fixed: reconstructing or scripting that generation is unrelated to
making existing citations resolvable, and would be its own work item if it
becomes a real problem (CLAUDE.md's own maintain-stage trigger: "something
you notice," not something to pre-solve speculatively.)

**The build-skill fix is forced, not chosen.** Fixing
`.claude/skills/build-skill/SKILL.md`'s stale filename is not a second bug
this work item went looking for — it is the one pre-existing violation the
new check would otherwise fail on at the moment of its own introduction,
so it has to move in the same diff. Flagged so the reviewer does not read
it as scope creep.
