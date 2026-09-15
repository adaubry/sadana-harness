# Review: Plugins on the console (from plan.md 2026-09-15)

Reviewed: working-tree diff against `HEAD` (566ca21) — 26 tracked files changed (+3024/-87), plus this work item's own untracked `docs/tasks/H24-door-nouns-plugins-layout-install-inspect/` artifact directory.
Reviewer context: fresh session, no prior context on this work item — the cold review the project's deploy-skill asks for.
Second opinion: none — the build stage ran its own self-check (/ponytail-review + /simplify, 4 parallel agents) earlier in a different session; not repeated here by design.

## Evidence

`make verify` (run from `/home/adam/sadana-wip/sadana-harness-lane-b`):

```
docs/tasks/H24-door-nouns-plugins-layout-install-inspect: all present artifacts valid
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
Success: no issues found in 92 source files
TYPES OK
▶ hermetic run: TZ=UTC LANG=C.UTF-8 PYTHONHASHSEED=0, empty env
........................................................................ [  4%]
........................................................................ [  9%]
...
  two conversations, concurrently: 319 ms for two 200 ms turns
.  one conversation, twice: 464 ms for two 200 ms turns
.................................................................... [ 14%]
........................................................................ [ 18%]
........................................................................ [ 23%]
........................................................................ [ 28%]
........................................................................ [ 32%]
........................................................................ [ 37%]
........................................................................ [ 42%]
........................................................................ [ 46%]
........................................................................ [ 51%]
........................................................................ [ 56%]
........................................................................ [ 60%]
........................................................................ [ 65%]
........................................................................ [ 70%]
........................................................................ [ 74%]
........................................................................ [ 79%]
........................................................................ [ 84%]
........................................................................ [ 88%]
........................................................................ [ 93%]
........................................................................ [ 98%]
...........................                                              [100%]
1539 passed in 178.04s (0:02:58)
TESTS OK
VERIFY OK
```

`python scripts/prove_door_plugins_e2e.py` (the CLAUDE.md-required standalone proof of a real external round trip — real socket, real `git` subprocess, real HTTP; not part of `make test`):

```
1) LIST plugins (the pre-seeded fixture plugin)
   200 data=['fixture-plugin']
2) GET the fixture plugin's layout
   200 nodes=['a', 'b'] edges=[{'from': 'a', 'to': 'b'}]
3) CREATE (install-from-git) 'greeter' from a real local git repo
   201 greeter
   -> resolved synchronously (< 2s), never promoted to an operation
4) INSPECT the same tag
   201 state=ok checksum=8fcf1b9763910e9802460387c259ba32017e3ff1fed2c0df7db0bdaf8c036fd0
5) SAVE with a bad field (expect 400, nothing written)
   400 errors=[{'field': 'nodes.a.kind', 'code': 'invalid'}]
6) SAVE a clean edit (expect 200, written)
   200 description=d

ALL STEPS PASSED
```

## Findings

**Important**

None.

**Nits**

1. `src/sadana/door/nouns/inspections.py:172,183,217` — `list()`, `get()` and `create()` each call `ensure_schema(ctx.conns.reader()/.writer)` before touching the table. `stores.Connections.__init__` (`src/sadana/stores.py:121-125`) already calls `ensure_schemas()` — which now includes `door_nouns_inspections.ensure_schema` — on the writer at process start, before any reader connects to the same file; no other noun in `door/nouns/` calls `ensure_schema` per-request (checked `grep -rn "ensure_schema(ctx.conns" src/sadana/door/nouns/`, only this file matches). It is harmless (idempotent `CREATE TABLE IF NOT EXISTS`) but it is unrequested defensive code against a state that `Connections.__init__` already rules out, on every single request to this noun.
2. `src/sadana/door/nouns/plugins.py:213` — the `UnknownPluginName` branch of `_install_outcome_to_response` is marked `# pragma: no cover - install_from_git never registers`, which is accurate (`install_from_git` never calls `resolve()`/`register()`), but it means this branch is truly dead code reachable only if `plugin_install.InstallOutcome`'s union ever grows a variant this function forgets to handle — worth a comment noting it exists only so the `isinstance` chain above it stays exhaustive against the full `InstallOutcome` union for a type checker, not because the branch is live.
3. `docs/tasks/H24-door-nouns-plugins-layout-install-inspect/spec.md`'s and `plan.md`'s own Acceptance-criteria/Proof checklists (`spec.md` `## Acceptance criteria`) are left with every box unchecked (`- [ ]`) even though every item is discharged by a named test or evidence line in this diff — cosmetic only, doesn't affect what shipped.

**Raised, not findings**

- `plugin_install.install_from_git`'s `InvalidManifest`/`NameMismatch`/etc. path shares `_fetch_and_verify_name()`/`_place_atomically()` with `install()` exactly as spec.md's Interface promised, and neither function opens a second `git` subprocess path — confirmed by reading both call sites and `fetch_verified_tag()` itself (`plugin_install.py:314-337`, unmodified by this diff, already uses `--` before `repo_url`/`tag` in every `git` invocation it makes).
- `plugins.plugin_dir()` (`plugins.py:109-124`, unmodified, reused by `install_from_git`) does both halves of CLAUDE.md's path-containment rule — an allowlist regex on the name and a resolved-path containment check — so a plugin name discovered from a fetched manifest (attacker-influenced, since it comes from the repository being installed) cannot escape `plugins_root`.
- Checked every `check_bodies=` call site touched or added by this diff (`_plugin_manifests.assessed_by_directory_name` → `editor_server.assess()`, `marketplace.inspect_tag()` → `plugin_manifest.validate(..., check_bodies=False)`): all `False`. No path in this diff imports or execs a fetched/inspected/listed plugin's own `.py` files; the unit test `test_inspect_tag_never_imports_or_executes_the_fetched_repository` (`tests/unit/test_marketplace.py`) proves this for the inspect path with both a sentinel file and a direct `sys.modules` assertion, matching CLAUDE.md's rule that a caller handling untrusted input must use the no-execution mode.
- The two pre-existing, uncommitted "broken merge" bugs plan.md's Concerns section describes are both present and correctly fixed in this diff: `client_surface.py`'s stale duplicate `memory_context=` keyword argument to `take_turn` (H26-era) is gone, leaving one `dispatch_closure(...)` call; `subcommands/door.py`'s two separate `from sadana.door.nouns import ...` lines (each re-importing `approvals`/`harness`) are consolidated into one parenthesized import block that also adds the five new noun modules. `git log --oneline -10` shows a clean, non-duplicated history with no leftover merge markers.
- Compared the diff's file list against plan.md's `## Files that change` line by line: all 26 changed/added tracked files are named there (nothing touched that plan.md doesn't name, nothing named that isn't touched). `tests/conftest.py`'s `make_upstream_repo`/`run_git` helpers, reused by the new contract tests, already existed pre-H24 and are correctly left untouched.
- spec.md's `## Rejected alternatives` were checked against the diff one by one: no second git-fetch path (confirmed above); `plugins.py`'s `get()` computes `editor_layout.positions()` fresh every call, no `layout` column anywhere in `plugin_state`'s schema; `tools.py` derives its `PluginSet` fresh via `client_surface.enabled_plugin_set(ctx.conns.reader())` per request, `DoorContext` gained no cached field; `plugins.py`'s `_save` calls `plugins.manifest_from_dict`/`editor_server._write_manifest` verbatim, `_manifest_error_to_problem` only classifies the resulting `ValueError` text; `install_from_git` is new and narrower, never calls `plugin_install.register()`/`resolve()`. No drift found back toward any rejected option.
- The five design principles were checked against spec.md's own "Design" section and the diff: (1) learn-from-reference — spec.md documents no hermes-agent analog exists and this work item cites its own in-repo prior art instead (`plugin_install.py`, `marketplace.py`, `editor_server.py`, `schedules.py`), confirmed accurate by reading the four "reused" functions this diff calls; (2) reduce the number of bets — every new noun file is additive, `plugins.py`/`plugin_manifest.py`/`editor_server.py`'s save path/`marketplace.py`'s claim-reject logic are not modified beyond the promotions and extraction spec.md names; (3) least step-cost — layout/workflow/node/tool are all recomputed per request rather than stored, matching the "add a step that reads what's already there" posture; the one new table (`inspections`) is justified in spec.md's own state-inventory table and there is no cheaper place to put a checked-but-uninstalled tag's shape; (4) more plugins not more core — no new core machinery, five noun files plus two narrow additions to `plugin_install.py`/`marketplace.py`; (5) minimise mutable state — spec.md's inventory table (`inspections` table, `plugin_state.state='disabled'`) matches what the diff actually stores, and nothing else in the diff introduces new persisted or process-lifetime state.
- Requirement 6 (the disable-filter directory-name-vs-declared-name gap) is closed exactly where spec.md says it would be: `client_surface.enabled_plugin_set()` filters on `p.directory.name`, not `p.manifest.name`, and `tests/unit/test_client_surface.py::test_a_disabled_plugin_is_excluded_even_when_its_directory_name_differs_from_its_manifest_name` is a real regression test for the folder-name-vs-declared-name case (not a re-implementation of the filter, matching the H16-era lesson the docstring cites).
- Spec.md's "What the prompt assumed and what is true" claims about `router.py` were checked directly: `create`/`update`/`remove` are not auto-gated (only declared `action`s are, `router.py:307-310`), and every non-list/get verb goes through `operations.run_bounded` automatically (`router.py:317-323`) — both match, and both are exercised correctly by `plugins.py`'s manual `plugins.install`/`plugins.write` checks on `create`/`update`(node label rename)/`_save`.
- `ledger.py`'s new `"inspections"` entry and `plugin_install.set_state`'s `ledger.record_change(..., kind="changed", ...)` / `inspections.py`'s `create`'s `kind="created"` both follow the same `kind` vocabulary and query shape every other noun in `_NOUNS` already uses (`grep -n "record_change" src/sadana/**/*.py`); nothing here invents a new ledger `kind` or a synthetic id where a real one already exists.

## Decision

Approved by adam aubry, 2026-09-15. No Important findings; the three Nits
are left as noted, not fixed before merge.
