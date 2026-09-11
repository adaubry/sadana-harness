# Review: Settings and secrets a plugin owns (from plan.md 2026-09-11)

Reviewed: 91b3000..working tree — 14 files, +1223/-136 (12 tracked, 2 new)
Reviewer context: cold — delegated to a subagent with no context beyond the
diff and the three artifacts, per deploy-skill § 1. The fixes below were then
applied in the build session, and `make verify` re-run after them.
Second opinion: `/ponytail-review` and `/simplify` ran during Build
(self-check), not repeated here by design.

## Evidence

```
$ make verify
docs/tasks/PLUGIN-CONFIG-01-settings-and-secrets-a-plugin-owns: all present artifacts valid
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
Success: no issues found in 41 source files
TYPES OK
▶ hermetic run: TZ=UTC LANG=C.UTF-8 PYTHONHASHSEED=0, empty env
........................................................................ [  9%]
........................................................................ [ 19%]
........................................................................ [ 29%]
........................................................................ [ 39%]
........................................................................ [ 48%]
........................................................................ [ 58%]
........................................................................ [ 68%]
........................................................................ [ 78%]
........................................................................ [ 87%]
........................................................................ [ 97%]
..................                                                       [100%]
738 passed in 25.46s
TESTS OK
VERIFY OK
```

## Findings

The cold review raised five Important defects, six compliance gaps and five
nits. All five defects and five of the six compliance gaps were fixed in this
branch before this file was written; each is recorded below with what changed,
per deploy-skill § 9. Two of the Important defects were regressions this work
item introduced, and one was a security exposure it introduced.

### Important — fixed in this branch

- **[Bugs] `validate()` raised `TypeError` instead of returning an outcome for
  a non-string name.** `plugin_manifest.py`'s new name checks ran after the
  `try/except` that catches `TypeError`, so a `plugin.toml` with `name = 123`
  or a `[[setting]]` with `name = 1` escaped as an uncaught exception. This was
  a regression: before this work item `validate()` never touched `manifest.name`
  and returned `Valid`. The consequence was worse than the input suggests —
  `discover_plugins()` has no handler, so one malformed plugin anywhere under
  the plugins root would take out *every* plugin and crash whatever built the
  tool surface, the exact opposite of that function's documented "silently
  excluded" contract. It was also reachable from untrusted input:
  `marketplace.submit()` calls `validate()` on a just-cloned third-party repo,
  so submitting a hostile repo ended in a traceback rather than
  `InvalidManifest`. **Fixed:** `isinstance` before each regex, returning
  `InvalidPluginName` / `InvalidSettingName`. Covered by
  `test_validate_returns_an_outcome_for_a_non_string_name_rather_than_raising`
  and `test_validate_returns_an_outcome_for_a_non_string_setting_name`.

- **[Bugs] The missing-settings preflight destroyed a paused run and its
  history on resume.** The preflight returned `paused_node=None`, and
  `plugin_dispatch.resume_paused_run` deletes the pause row on exactly that
  condition. So if a declared setting were blanked — or a plugin update added a
  required one — while a run waited at a `wait` node, the arrival of the
  external answer would delete the pause permanently: the person could then set
  the value and still never resume, and the returned result showed an empty
  trace for a run that had already executed several nodes. Neither `spec.md`
  nor `plan.md` considered this case; the code comment reasoned only about a
  fresh start. **Fixed:** on a resume the preflight now returns the pause
  exactly as it already was — `paused_node=resume.node`, `resume.trace`,
  `resume.artifacts` — so `save_pause_from_result` rewrites it unchanged and
  the refusal is idempotent however many answers arrive before the value is
  set. The test that locked in the old behaviour was rewritten (it was written
  in this same work item and was asserting the defect) into
  `test_a_resume_missing_a_setting_keeps_its_pause_and_its_history`, which now
  asserts the pause is retained and the prior trace survives.

- **[Bugs] A non-boolean `secret` validated, then made the plugin unsavable in
  the editor.** `_parse_manifest` took `s["secret"]` untyped and the new
  validation checked only setting *names*, so a `secret` field holding the
  string `yes` rather than a boolean came back `Valid`. `manifest_from_dict` rejects exactly that value, so the editor would
  load such a plugin, post it back unchanged, and be answered `400` about a
  field it has no UI for — on a plugin the validator had called valid.
  **Fixed:** `_require_bool` raises `TypeError`, which `validate()`'s existing
  `except` already turns into `ManifestParseError` — no new outcome type
  needed. Covered by `test_validate_refuses_a_non_boolean_secret`.

- **[Security] `sadana plugin install` executed a freshly-cloned stranger's
  Python at install time.** The new requirements-reporting line called
  `plugin_manifest.validate(directory)` with `check_bodies` defaulting to
  `True`, and that function's own docstring says that is "the only step that
  imports and executes `plugin_dir`'s own code… Unsafe for anything nobody has
  reviewed yet", citing the CLAUDE.md rule that a caller handling untrusted
  input must use the non-executing mode — which is why `marketplace.submit()`
  already passes `check_bodies=False`. Nothing here needs a body resolved; only
  `.settings` is read. A second consequence: if the import raised,
  `validate()` returned `UnresolvedBody`, the `isinstance(..., Valid)` guard
  swallowed it, and the person was told nothing about the settings the plugin
  needs — defeating requirement 7. **Fixed:** `check_bodies=False`.

- **[Security] A secret passed to `sadana plugin set` was exposed in the
  process table.** `--value` was the *only* path when stdin was not a tty, so
  every scripted use necessarily put the credential in `argv`, world-readable
  under `/proc` and recorded in shell history. **Fixed:** with no tty the value
  is read from stdin, so `... | sadana plugin set weather api_key` works and
  `--value` becomes a convenience rather than a requirement. Covered by
  `test_plugin_set_reads_the_value_from_stdin_when_there_is_no_terminal`.

### Important — compliance, fixed in this branch

- **A `plan.md` Proof item was undischarged.** The plan promised a test for
  "exit 1 with nothing written for an unknown plugin"; only `plugin settings`
  had one, and `cmd_plugin_set`'s own not-installed branch had no test at all.
  Added `test_plugin_set_fails_for_a_plugin_that_is_not_installed`.

- **A `plan.md` Proof item was discharged in weakened form.** The plan promised
  "a `call` body at the *second* node reads the value", the case the rejected
  reserved-key design could not serve. The test that existed declared both
  nodes `compute`. The reach past the first node was genuinely proved, but
  `call` — approval-gated and run off the event loop, and the kind a credential
  is actually used in — was not. Added
  `test_a_call_node_two_steps_deep_reads_the_setting`.

- **A `spec.md` acceptance criterion was only partly met.** It required setting
  names `API KEY`, `-x` and the empty string to fail validation; only `API KEY`
  was covered. Now parametrised over all four shapes including a trailing dash.

- **The testable half of "saving in the editor preserves settings" had no
  coverage at all.** `grep -n setting tests/unit/test_editor_server.py` returned
  nothing. Only the JavaScript is genuinely untestable here; the
  GET → post-back → PUT → re-read path is pure Python. Added two tests.
  **The first version of the rename test was vacuous** — it sent the payload
  key `name` where the endpoint reads `step`, so the request 400'd and the
  settings assertion passed against an untouched file. Caught by deliberately
  reintroducing the `rename_node` bug and watching which tests went red: only
  the unit-level one did. Both tests now assert the operation succeeded before
  asserting its effect, and both were re-proved red against the reintroduced
  bug and green after.

- **`spec.md`'s Policies claim was false on three counts.** It asserted the
  `validate()` plugin-name check closed a pre-existing hole. (a) A pattern
  check already existed in `editor_server` and disagreed with the new one.
  (b) `plugin_install.install()` never calls `validate()`, so that check closed
  nothing on the install path — what closes it is `plugins.plugin_dir()` at the
  install site, added during Build. (c) The `.../skills/<skill>` half named in
  the same sentence is still unchecked. `spec.md` now carries a dated
  correction that quotes the original sentence rather than replacing it
  silently; `plan.md`'s Proof sentence about byte-identical dict serialization
  was corrected the same way.

### Important — accepted, not fixed

- **The scope of this work item exceeds one commit's worth of subject.** It
  ships the settings feature *and* a fix for a pre-existing arbitrary-directory
  overwrite in `plugin_install`. The cold reviewer, asked to judge this
  directly, called the expansion legitimate and the `plan.md` amendment honest
  and complete. I agree, and the reason is that the alternative was shipping a
  `spec.md` asserting a hole was closed while it was open. **This is still the
  finding a human should overrule if they want to.** The `plugin_install.py`
  change and its two regression tests are separable as a unit.

### Nits

- `plugins._skill_path` still builds a path from `ref.skill` with no pattern
  check, and `_check_skill` only tests that the resulting file exists. Wholly
  pre-existing, out of scope, and named in `spec.md`'s correction as the first
  line of the maintenance `intent.md` this work item owes.
- `plugin_dispatch` builds `_plugins_root() / pause.plugin` without going
  through `plugins.plugin_dir()`. `pause.plugin` comes from a validated
  `manifest.name`, so this is defence-in-depth only — but it is the one caller
  that skips the shared helper.
- `setting_env_var`'s two `assert`s vanish under `python -O`, and so would the
  test that proves they fire. Every caller's name comes from `validate()`, so
  nothing depends on them today.
- `getpass.getpass(f"{setting.purpose}: ")` prints plugin-authored text to a
  terminal, so a `purpose` containing ANSI escapes is rendered. The same
  exposure every other manifest string this project prints already has.
- The `--value` flag's help text still reads as the primary no-keyboard path
  now that stdin works; a later pass could reword it.

### Raised, not findings

- `intent.md` names the marketplace review surface as an affected system —
  "whoever reviews one needs to see what it asks for". Settings do reach the
  stored `manifest_json`, but nothing in `subcommands/marketplace.py` renders
  them. `spec.md` requirement 9 narrowed this to round-trip survival only, so
  the code matches the spec and not the intent. The narrowing was deliberate;
  it is worth confirming that is what was wanted, and it is a one-line work
  item if not.
- `plan.md` step 6 owed "a manual save-and-reload of a plugin that declares a
  setting" as Deploy evidence. That manual run has **not** been done. It was
  superseded rather than skipped: the two editor tests added above cover the
  whole Python half through the real `handle()` surface, and the JavaScript
  half was settled by reading `editor.js`, which posts back the object the
  server handed it rather than rebuilding it. Recorded so nobody later reads
  `VERIFY OK` as having discharged it.

## Decision

Approved by adam, 2026-09-11, with every Important finding already fixed in
this branch before the decision was given. The one Important finding left
standing — that this work item also ships the `plugin_install` path-traversal
fix and so exceeds one commit's worth of subject — was accepted as read rather
than split out.
