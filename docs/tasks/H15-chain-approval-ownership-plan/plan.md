# Plan: Approval and ownership in the work-item chain (from intent.md 2026-09-14)

Author: Adam Aubry (product owner). Status: approved.

## Files that change

Nine files. Two carry all the logic; the rest are one edit each.

- `tests/unit/test_artifact_gate.py` (new) — the whole test surface, written
  first and red. Imports `scripts.artifact` plainly (the sibling
  `tests/unit/test_check_reference_citations.py` already proves that import
  works under the hermetic runner), marks every test `unit`, and uses
  `monkeypatch.chdir(tmp_path)` so the gate's relative `docs/tasks` and
  `.claude/active-task` resolve inside the temporary tree.
- `scripts/artifact.py` — `STATUS_RE` and `status_of()` (new); `planned_paths()`
  (new); `import fnmatch`; an `Author: … Status: …` entry added to the `design`
  and `build` stages' `header_rules`; three refusals inside `cmd_gate` (one per
  existing branch). No new command, no fifth `Stage` field, no change to
  `cmd_new`/`cmd_check`. **Amended during the step-10 self-check:** `cmd_status`
  does change — it reported "all stages complete. Ready to commit." for an
  item whose artifacts were valid but unapproved, which the commit gate then
  refuses. It now marks such a stage `valid, awaiting approval`.
- `.claude/hooks/gate-artifact-chain.sh` — `pathre` gains a `tests/…`
  alternation beside `docs/tasks/…` and `src/…`. **Amended after the cold
  review:** not one line. The `tests/` alternation also widened the
  unanchored `python … .write(` fallback into false positives, and
  narrowing that fallback then left `tests/` writable from a shell command —
  the exact hole requirement 7 closes. The command branch is rewritten
  instead: the fallback is anchored on `open(…, 'w')`, and `tail -1` becomes
  a loop, because it gated only the *last* destination in a command.
- `CLAUDE.md` — one new bullet under `## Do not`, exactly as
  `spec.md`'s closing section drafts it. One bullet, not two.
- `.claude/skills/design-skill/SKILL.md` — the `# Spec:` template block gains
  `Author: <name> (<role>). Status: draft.` under the `Intent:` line.
- `.claude/skills/build-skill/SKILL.md` — the `# Plan:` template block gains
  the same line.
- `tests/integration/test_gate_hook.py` (new) — **added during the step-10
  self-check.** `## Risks` below claimed nothing in `make test` could exercise
  a Claude Code hook; that was wrong. The hook runs as a subprocess against a
  throwaway project directory, which turns `spec.md`'s otherwise untested
  acceptance criterion ("the hook's `pathre` matches a heredoc into `tests/`")
  into a real check. `pathre` is nested-quoted bash that fails open.
- `docs/reference/console_fit_plan.md` (new) — six sections, hand-authored and
  tracked like source.
- `docs/reference/cli_shell_blueprint.md` — §7 question 6 only, rewritten in
  the same in-place `**Resolved.**` form question 5 already uses. Nothing else
  in that file moves.

## Order of work

Chosen so the change that can lock the session out of its own tools lands
*after* the thing that tests it exists, and so the documents land before the
lint that checks their citations runs.

1. **`tests/unit/test_artifact_gate.py`, red.** Every assertion written against
   functions and messages that do not exist yet. Run it, confirm it fails on
   `ImportError`, and leave it failing. This is the only step whose write is
   still governed by today's gate, which is the reason it is first.
2. **`scripts/artifact.py`.** In one edit: `import fnmatch`; `STATUS_RE` and
   `status_of()` next to `PLACEHOLDER` and `norm()`; `planned_paths()` next to
   `sections_of()`; the two `header_rules` entries; then the three `cmd_gate`
   refusals in branch order — artifact-write, source-write, commit. Run
   `bash scripts/run_tests.sh tests/unit/test_artifact_gate.py`; it must go
   green here, before anything else changes.
3. **`.claude/hooks/gate-artifact-chain.sh`.** Add the alternation. Prove it by
   hand with the throwaway work item in step 8 — nothing in `make test` can
   exercise a Claude Code hook.
4. **`CLAUDE.md`** — the one bullet.
5. **The two skill templates.** `Author: <name> (<role>). Status: draft.`
   inside each fenced template block.
6. **`docs/reference/console_fit_plan.md`.** Written from the supplied eleven
   promises and eleven step titles verbatim, the four blueprint rows quoted
   verbatim from `docs/reference/cli_shell_blueprint.md`, and the eight
   settled decisions as given. §3's steps 1–13 map to the block lines in
   `docs/audits/2026-09-10/blocks.md`.
7. **`docs/reference/cli_shell_blueprint.md` §7 question 6.**
8. **Stage this work item's files, then `make verify`.** **Amended after the
   cold review:** not `git add -A`. `docs/audits/2026-09-10/findings.md` is
   untracked, predates this work item and is not gitignored, so `-A` would
   sweep it into a commit the approval never covered — in the one place the
   guarded set does not reach. Staged by name instead. The `add` is not optional and not
   cosmetic: `pre-commit` stashes unstaged changes and lints `HEAD`, and
   `scripts/check_reference_citations.py` resolves citations against
   `git ls-files` — an untracked `console_fit_plan.md` fails the lint that
   `spec.md` already cites it from.
9. **The manual proof.** A throwaway work item, three transcripts — draft
   blocks with the approval sentence, approved-but-unlisted blocks with the
   ownership sentence, listed allows — then delete the throwaway item. It is
   never committed.
10. **`/ponytail-review` and `/simplify`** against the diff, sort what comes
    back into the three buckets, apply the first bucket, re-run `make verify`,
    and backfill this plan's `## Files that change` if the self-check touched a
    file this list does not name.

## Risks

**What this could break, by name.** `make chain` validates only the *active*
work item, and no `spec.md` or `plan.md` in the fifty-eight closed items
carries an `Author: … Status: …` line — checked, not assumed. So the moment
`.claude/active-task` is pointed at any older item, `make chain` goes red on a
header rule that item predates, and `make verify` fails for a reason unrelated
to whatever is being worked on. CLAUDE.md already forbids editing a closed
item's artifacts, so the mitigation is not a backfill: it is that
`.claude/active-task` only ever points at the item in flight, which is already
how `cmd_new` sets it. Named here because the failure would be baffling
otherwise. The second named breakage is deliberate and permanent: from this
commit on, `review.md` cannot be written for any work item until its `plan.md`
reads approved, so every deploy stage has a human in it by construction —
including this one's. Third: the hook's new `tests/` alternation means an
ordinary shell redirect into `tests/` is now gated, so a session with no active
task can no longer scribble a fixture there; that is the point, but it is a
behaviour change for commands that used to pass silently.

**The riskiest step is 2, and it is risky in an unusual direction.** The gate is
loaded fresh by the hook on every write, so a defect in `cmd_gate` takes effect
immediately and can refuse the very writes needed to fix it — including
`review.md` and any further edit to `artifact.py` itself. Two things contain
it. The ordering: step 1 puts the test file on disk *before* the gate can
refuse it, so step 2 is verifiable the second it is typed. And the guarded set:
`scripts/` is not in it, so a broken gate never blocks the edit that repairs
the gate. Step 6 is the largest by volume but carries almost no risk — a new
document with no callers, whose only mechanical check is that its path is
tracked.

**Where this plan could drift back into something `spec.md` rejected.** Three
temptations, all in step 2. Writing `planned_paths()` against bullets and
commas — a layout parser — when the spec chose a token scan precisely because
four incompatible formats are already in the tree. Reaching for
`importlib.util.spec_from_file_location` in step 1 because `artifact.py` is a
script — the spec rejected it, and `model_access._discover`'s reason for it
(hyphenated directory names) does not apply here. And normalising the target
with `Path.resolve()` before matching, which the spec rejected because it turns
an odd path from a refusal into an allow. None is reinstated; this paragraph
exists so a reviewer can check that claim against the diff rather than take it.

**What is left uncontained.** ~~Nothing in `make test` can exercise a Claude
Code hook, so step 3's correctness rests entirely on step 9's manual
transcripts.~~ **Wrong, and corrected during the step-10 self-check:** the
hook runs as a subprocess, and `tests/integration/test_gate_hook.py` does
exactly that. Believing otherwise is what left two bypasses in the shell half
for the cold review to find.
And the `fnmatch` `*`-crosses-`/` looseness and the prose-path over-permissive
parse are both accepted ceilings from `spec.md § Concerns`, not oversights.

## Proof

- `tests/unit/test_artifact_gate.py` covers, one test each: `status_of` on
  `draft`, on `approved.` with trailing punctuation, on `APPROVED`, and on a
  file with no header line; `planned_paths` on the comma-separated form, on the
  bulleted form, on `(new)`/`(edit)` annotations, and on a glob entry;
  `cmd_gate("write", "docs/tasks/T/spec.md")` refused while `intent.md` reads
  draft and allowed once it reads approved; `cmd_gate("write",
  "src/sadana/x.py")` refused with the approval sentence while `plan.md` reads
  draft, refused with the ownership sentence once approved but unlisted,
  allowed once listed, allowed when a listed glob covers it; `cmd_gate("write",
  "scripts/whatever.py")` allowed unchanged; and `cmd_gate("commit", "-")`
  refused while any of the three reads draft and allowed when all three read
  approved. Each refusal test asserts on the sentence, via `capsys`, not only
  on the exit code — the sentences are the interface `spec.md` pinned.
- The file's module docstring states why the literal word `approved` appears in
  it: the tests write it as *data* into fixture artifacts under `tmp_path`,
  which is the one place CLAUDE.md's rule does not reach, and nowhere in this
  repository's own `docs/tasks/` does an agent write it.
- `make verify` ending `VERIFY OK`, pasted whole.
- Three pasted transcripts from step 9's throwaway work item: the approval
  refusal, the ownership refusal, and the allow.
- `wc -l docs/reference/console_fit_plan.md`.
- `git diff --stat docs/reference/cli_shell_blueprint.md` showing one hunk.
