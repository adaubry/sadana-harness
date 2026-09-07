# Review: D2 manifest validation (from plan.md 2026-09-07)

Reviewed: b1cdd7c..HEAD (working tree) — 8 files, +873/-178 at review time;
the three Bugs findings below were then fixed in this same branch per
Adam's decision ("fix the bugs if not done already, let's not defer that").
Reviewer context: fresh, context-free subagent — no memory of writing this
code, briefed only with intent.md, spec.md, plan.md and the diff. The fixes
below were applied in the same session that wrote the original code, not
re-reviewed cold a second time.
Second opinion: none beyond the cold review above — the build-time
self-check (`/ponytail-review` + `/simplify`) already ran during Build and
is not repeated here by design (see `deploy_build_methodology_change` note).

## Evidence

```
$ make verify   (re-run after the bug fixes below)
docs/tasks/D1-dagresult-dispatch: all present artifacts valid
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
Detect secrets...........................................................Passed
docs/reference/ citations resolve to tracked files.......................Passed
LINT OK
Success: no issues found in 10 source files
TYPES OK
▶ hermetic run: TZ=UTC LANG=C.UTF-8 PYTHONHASHSEED=0, empty env
........................................................................ [ 24%]
........................................................................ [ 49%]
........................................................................ [ 74%]
........................................................................ [ 98%]
...                                                                      [100%]
291 passed in 2.66s
TESTS OK
VERIFY OK
```

## Findings

Three passes run (bugs, security, compliance) by the fresh subagent above,
plus the files-touched-vs-plan.md comparison. Three Important bugs and one
Important compliance gap surfaced; all three bugs are now fixed (detail and
regression tests below), the compliance gap is left open by explicit
decision, followed by three nits.

### Important

- **[Bugs] `src/sadana/plugin_manifest.py:71-78` (`_check_skill`) resolved an
  `ask` node's skill through `plugins._plugins_root()`/`SADANA_PLUGINS_DIR`
  joined with `manifest.name`, never through the `plugin_dir` argument
  `validate()` was actually called with.** Reproduced: a plugin directory
  whose basename differs from `manifest.name` (with `SADANA_PLUGINS_DIR`
  unset, or set to a different root) made `validate()` report
  `UnresolvedSkill` for a `SKILL.md` that was sitting right there under
  `plugin_dir`, because the check looked under `_plugins_root()/<manifest
  name>/skills/...` instead.
  **Fixed:** `_check_skill` now resolves `plugin_dir / "skills" / node.skill
  / "SKILL.md"` directly, never through `_plugins_root()`. The shared
  frontmatter-validation logic (name match, description presence/length)
  moved into a new pure `plugins._validate_skill_text()`, called by both
  `load_skill()` (which still legitimately resolves through
  `_plugins_root()` — it's validating the *currently installed* plugin for
  a live conversation) and `_check_skill` (which resolves through the
  `plugin_dir` it was actually given) — one shared check of *is this SKILL.md
  valid*, two different, correct answers to *where is it*. Regression test:
  `test_validate_resolves_skill_against_plugin_dir_not_the_installed_plugins_root`
  (`tests/unit/test_plugin_manifest.py`) builds a plugin directory whose
  name differs from `manifest.name`, points `SADANA_PLUGINS_DIR` at an
  unrelated empty directory, and asserts `validate()` still returns `Valid`.
  Also removed `_write_plugin`'s former `SADANA_PLUGINS_DIR` setup entirely
  — it was never validate()'s dependency to begin with, which is the root
  cause this bug's own test gap came from.

- **[Bugs] `src/sadana/plugin_manifest.py` (`validate`, `_check_schema`) read
  `plugin.toml` and schema files with `read_text(encoding="utf-8")` inside a
  `try` that only caught `tomllib.TOMLDecodeError`/`KeyError`/`TypeError`
  (resp. `json.JSONDecodeError`).** A file containing invalid UTF-8 bytes
  made `UnicodeDecodeError` escape `validate()` uncaught, violating spec.md
  Requirement 3.
  **Fixed:** `UnicodeDecodeError` added to both callers' except tuples (and
  to `_check_skill`'s new direct `SKILL.md` read, and to `load_skill()`
  itself — the same bug existed there, one level removed). Three regression
  tests added: `test_validate_returns_manifest_parse_error_for_invalid_utf8`,
  `test_validate_returns_invalid_schema_for_invalid_utf8_schema_file`,
  `test_validate_returns_unresolved_skill_for_invalid_utf8_skill_md`.

- **[Bugs] `src/sadana/plugins.py`'s `_first_unreachable` never validated
  `Entry.start` against the declared node-name set.** An entry whose `start`
  didn't match any node was silently dropped from the BFS seed list, with no
  error — a plugin with one entry (`start = "no-such-node"`) and one real
  node reported `UnreachableNode` naming the *wrong* node, never the
  entry's own dangling `start`.
  **Fixed:** `validate()`'s dangling-target check (check 5) now also walks
  every `Entry.start` against the declared node-name set, returning
  `DanglingTarget(node=entry.tool, target=entry.start)` — reusing the
  existing outcome type rather than adding a ninth, since an entry's
  `start` is the same class of failure as a dangling `next`/`ports` value
  (a declared edge naming a node that doesn't exist). `_first_unreachable`
  now assumes every `entry.start` already names a real node (true by the
  time it runs, since the dangling-target check precedes it), matching the
  "each check assumes the one before it held" posture the rest of
  `validate()` already has. `DanglingTarget`'s docstring updated to name
  this second source. Regression test:
  `test_validate_returns_dangling_target_for_an_entry_start_with_no_such_node`.

- **[Compliance, not addressed — Adam scoped this round to the Bugs
  findings only] `tests/conftest.py` is touched (new `write_skill` helper) but
  is not listed in plan.md's § Files that change, and the change it makes
  directly reverses a decision plan.md states explicitly.** Plan.md says,
  of `test_plugin_manifest.py`: "their own small `_write_skill` helper (a
  ~15-line duplicate of test_conversation.py's — **not worth sharing across
  two test files for this**)." The actual diff does the opposite: both
  `/ponytail-review` and `/simplify`'s reuse/simplification passes (run
  during Build) flagged the duplication, and the fix extracted `write_skill`
  into `tests/conftest.py`, now imported by both `test_conversation.py` and
  `test_plugin_manifest.py`. The resulting code is reasonable — but
  plan.md, the approved record of what was decided, was never updated to
  reflect the reversal, so the artifact chain currently describes a
  decision the code doesn't make.

### Security

- **[Security] Same root cause as the first Bugs finding above, restated as
  a reach concern.** `_check_skill` resolving through ambient global state
  rather than the passed-in `plugin_dir` meant `validate()`'s trust verdict
  for "is this specific directory's description sound" could depend on
  files outside that directory — exactly the kind of widened reach that
  would undermine intent.md's marketplace/visual-tool motivating scenario.
  **Fixed** by the same change as the first Bugs finding above: `_check_skill`
  resolves only within `plugin_dir` now.

- Checked and clean: the dynamic import of a plugin's `init.py`
  (`plugin_manifest.py`'s `_load_body_module`) executes arbitrary top-level
  plugin code, but this is the one risk spec.md's own Concerns section
  names and accepts explicitly ("every plugin validated today is
  first-party... accepted on the same basis" as `plugin_blueprint.md §10`
  Risk 1) — a disclosed, not a silent, widening. No secrets, credentials,
  or PII appear in any new code, error string, or log line.

### Compliance — checked and discharged

- **plan.md § Proof**, walked item by item: each of the 7 `§8` checks has
  its own named-outcome test in `tests/unit/test_plugin_manifest.py`
  (`test_validate_returns_manifest_parse_error_for_missing_plugin_toml` /
  `_for_malformed_toml`, `_invalid_schema_for_missing_schema_file` /
  `_for_a_structurally_invalid_schema`, `_unresolved_skill_for_a_missing_skill`,
  `_duplicate_node_name_for_two_nodes_sharing_a_name`,
  `_dangling_target_for_a_next_with_no_such_node`,
  `_unresolved_body_for_a_missing_function`,
  `_unreachable_node_for_a_node_no_edge_reaches`), each asserting the
  specific outcome type and its fields, not just truthiness. The `Valid`
  test asserts full `Manifest` field equality. `mypy src` and `make verify`
  were re-run fresh for this review (above) and both end clean.
- **spec.md § Acceptance criteria**, walked item by item: all eight are
  satisfied — `plugins.py` exports `Entry`/`Node`/`Manifest`/
  `ManifestOutcome` and its eight members; the five skill-loading names live
  in `plugins.py` only; `plugin_manifest.py` exports exactly `load_skill`
  and `validate`; `conversation.py`'s `run_child` calls
  `plugin_manifest.load_skill`; `pyproject.toml` declares `jsonschema`; the
  fixture/test criteria above hold; `mypy`/`make verify` are green.
- **spec.md § Rejected alternatives**, checked against the diff: the
  Python-importable-manifest alternative, the batched-outcome alternative,
  and the speculative `each`/`wait`-field alternative are all still
  correctly avoided. The fourth — "minimise mutable state" / "`validate()`
  is a pure function of `plugin_dir`'s contents" — was the one the first
  Bugs finding above contradicted (`validate()` also depended on
  `SADANA_PLUGINS_DIR`/the default state dir, not only on `plugin_dir`); the
  bug fix above removes that dependency, so the guideline-4 argument
  spec.md makes for the chosen design now holds as implemented, not only as
  stated.
- **Files touched vs. plan.md's § Files that change**: every file plan.md
  named is touched; `tests/conftest.py` is touched but not named (see the
  Important compliance finding above). No named file was left untouched.

### Nits

1. `plugin_manifest.py`'s `validate()` reuses the name `seen` (built for
   duplicate-name detection) as the "valid node names" set for the
   dangling-target check — correct, but the name no longer describes its
   second use.
2. `pyproject.toml`'s new `dependencies = ["jsonschema"]` carries no version
   pin, despite spec.md's Design section naming the exact installed version
   (`4.26.0`) by name.
3. `tests/unit/test_plugin_manifest.py` imports the shared helper as `from
   conftest import write_skill as _write_skill` purely for name continuity
   with the old local name — a small, load-bearing-free indirection.

## Decision

Approved by Adam, 2026-09-07. The three Important Bugs findings (skill
resolution bypassing `plugin_dir`, uncaught `UnicodeDecodeError`, and a
dangling `Entry.start` misattributed to an unrelated node) are fixed in
this branch, with regression tests, before merge — see each finding above
for what changed. The Important Compliance finding (`tests/conftest.py`'s
shared `write_skill` helper reversing a `plan.md` decision without
`plan.md` being updated) is left open by explicit decision, not fixed.
