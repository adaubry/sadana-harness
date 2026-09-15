# Plan: Plugins on the console (from intent.md 2026-09-15)

Author: adam aubry (project owner). Status: approved.

## Context

H24 exposes plugin management to the console through the door: seeing what's
installed (with a computed diagram of each plugin's steps), installing from a
git tag, inspecting a tag before installing, saving edits, and disable/enable
that actually takes effect. `intent.md` and `spec.md` are approved; this is
the implementation plan for spec.md's design, which builds entirely on four
already-closed work items (`plugin_install.py`, `plugin_manifest.py`/
`plugins.py`, `marketplace.py`, `editor_server.py`/`editor_layout.py`) plus
`schedules.py`'s noun-module pattern. No hermes-agent file is analogous
(checked against `PLUGIN-SYSTEM`'s block during design); this plan reuses
in-repo prior art exclusively, per spec.md's own guideline-1 finding.

Two pre-existing, uncommitted breakages sit on this branch unrelated to H24
(a bad merge's duplicate `memory_context` kwarg in `client_surface.py`, and a
duplicate `door.nouns` import in `subcommands/door.py`) — both in files this
plan already has to edit, both fixed as this plan's own first step, per the
user's decision to fold them in rather than block on them separately.

## Files that change

- `src/sadana/client_surface.py` — fix duplicate `memory_context` kwarg
  (delete the stale, pre-H26 duplicate at the old line 524); promote
  `_enabled_plugin_set` to public `enabled_plugin_set`, fixing its filter to
  compare `p.directory.name` (matches `plugin_state.name`) instead of
  `p.manifest.name`.
- `src/sadana/subcommands/door.py` — land the already-staged duplicate-import
  consolidation (or redo it if it's reverted by then); register the five new
  noun modules in the `nouns={...}` dict.
- `src/sadana/editor_server.py` (only edit: promote three helpers) —
  `_assess`→`assess`, `_waiting`→`waiting`, `_list_plugins`→`list_plugins`;
  update this file's own internal call sites; one-line docstrings kept as-is
  or trimmed to one line each.
- `src/sadana/plugin_install.py` — add `install_from_git()` and `set_state()`
  (spec.md § Interface); no change to `install()`'s own contract.
- `src/sadana/marketplace.py` — extract `inspect_tag()`; `submit()` calls it
  instead of repeating fetch+validate.
- `src/sadana/door/nouns/plugins.py` (new)
- `src/sadana/door/nouns/workflows.py` (new)
- `src/sadana/door/nouns/nodes.py` (new)
- `src/sadana/door/nouns/tools.py` (new)
- `src/sadana/door/nouns/inspections.py` (new) — owns the new `inspections`
  table.
- `src/sadana/door/nouns/_plugin_manifests.py` (new) — **found while writing
  `nodes.py`'s own tests**: `plugin_manifest.discover_plugins()`'s
  `check_bodies=True` excludes a plugin entirely the moment any one step
  still needs code — exactly the state `needs_code`/`layout` exist to show.
  `plugins.py`, `workflows.py` and `nodes.py` all need the same
  `check_bodies=False` (`editor_server.assess()`-based) lookup instead, so
  it is one shared leaf module — not itself a noun, matching
  `door/nouns/__init__.py`'s own "the one thing under `nouns/` that isn't a
  noun" precedent — rather than three copies that could drift.
- `src/sadana/door/capabilities.py` — append `plugins.install`,
  `plugins.inspect`, `plugins.write` to `DECLARED`.
- `src/sadana/stores.py` — one new line in `ensure_schemas()` for
  `door.nouns.inspections.ensure_schema`.
- `src/sadana/ledger.py` — **found while writing `inspections.py`'s own
  test**: `ledger.record_change()` and `ledger.inventory()`/`GET
  /v1/inventory` both key off the closed `_NOUNS` dict, which the plan
  didn't know about. One new entry, `"inspections": _Noun("insp", (a real
  `SELECT id, updated_at, version FROM inspections ...`,))` — the same shape
  every other real-table noun already takes. `workflow`/`node`/`tool` need
  no entry here: they write no ledger row (spec.md's own design), and
  `inventory()` iterates `_NOUNS` independently of `ctx.nouns`, so omitting
  them changes nothing else.
- `docs/console/nouns.md` — append `plugin`/`workflow`/`node`/`tool`/
  `inspection` sections, in that order, at the end of the file.
- `docs/console/capabilities.md` — three new rows.
- `tests/unit/test_client_surface.py` (or wherever the existing suite for
  this file lives) — regression test for the disable-filter fix.
- `tests/unit/test_plugin_install.py` — `install_from_git`/`set_state`
  coverage, including the bad-tag-is-400 path and a local bare-repo fixture.
- `tests/unit/test_marketplace.py` — `inspect_tag()` coverage (never imports
  the plugin — asserted via `sys.modules`), `submit()` unchanged behavior.
- `tests/unit/test_editor_server.py` — updated for the renamed helpers (same
  external HTTP behavior).
- `tests/contract/nouns/test_plugin_noun.py` (new) — layout, save-with-errors,
  install-from-git, disable/enable, the dual-sided capability-gating proof.
  **Path corrected from `tests/unit/test_door_nouns_plugins.py`**: found
  during step 6 that every existing per-noun conformance proof
  (`schedules`, `agents`, `approvals`, ...) already lives at
  `tests/contract/nouns/test_<noun>.py`, built directly against
  `router.handle()` with a `_door(tmp_path, capabilities=...)` fixture that
  already supports the exact dual-sided capability-gating proof this item
  needs — that is "the conformance run" this project's own convention
  means, not an addition to `test_console_grammar.py`. **Named
  `test_plugin_noun.py`, not `test_plugins.py`**: found at `make verify`
  time that pytest's rootdir import mode requires a globally unique
  basename across the whole `tests/` tree with no `__init__.py` markers
  anywhere in it, and `tests/unit/test_plugins.py` (for `sadana.plugins`,
  the pure module) already holds that name.
- `tests/contract/nouns/test_workflows.py`, `test_nodes.py`, `test_tools.py`
  (new) — same corrected location, one file per noun matching every
  existing noun's own 1:1 file convention (not the plan's original guess of
  one combined file).
- `tests/contract/nouns/test_inspections.py` (new) — create→get, a
  structurally-reachable-node fixture proving `needs_code`/`external_refs`
  are computed structurally, the 7-day sweep, checksum determinism, the
  capability-gating proof.
- `tests/contract/test_console_grammar.py` is **not** touched — superseded
  by the per-noun files above, consistent with H27/H26/H20's own precedent
  of extending their own `tests/contract/nouns/test_*.py` file rather than
  the framework-only conformance file.
- A standalone proof script (`scripts/prove_*.py` or ad hoc, per CLAUDE.md's
  "prove a block's first real external round trip... outside make test")
  for the curl transcript, run once and pasted as Deploy-stage evidence —
  not part of `make test`.

## Order of work

1. **Fix the two pre-existing breakages.** `client_surface.py`'s duplicate
   kwarg; `subcommands/door.py`'s duplicate import. Run `make verify` once
   to confirm a green baseline before any new code lands.
2. **`client_surface.enabled_plugin_set()`** — promote + fix the filter, in
   the same edit as step 1 (same file, same reason). Add/adjust its
   regression test (a plugin whose folder name differs from its manifest
   name, disabled by directory name, must be excluded from
   `build_plugin_set`'s catalog).
3. **`editor_server.py` promotions.** Rename the three helpers, fix internal
   call sites. Run `test_editor_server.py` — must be green unchanged (same
   external behavior, only names inside the module moved).
4. **`plugin_install.install_from_git()` / `set_state()`.** Add with unit
   tests against a local bare-repo git fixture (reusing whatever fixture
   `test_plugin_install.py` already sets up for `install()`/
   `fetch_verified_tag()`, if one exists — else a new one, minimal). Covers:
   discovers name from the manifest; `expect_name` mismatch refuses; bad tag
   syntax is refused before any clone; no second `git` invocation path
   introduced (reads as: only `_git`/`fetch_verified_tag` called).
5. **`marketplace.inspect_tag()`.** Extract from `submit()`; `submit()`'s own
   existing tests must stay green unchanged. New test: inspecting a plugin
   whose body would raise on import never imports it (`sys.modules`
   assertion, per spec.md's own acceptance criterion).
6. **`door/nouns/inspections.py`.** New table, `ensure_schema` wired into
   `stores.py`, `create`→operation over `inspect_tag()`, `get`, the 7-day
   sweep (`door/idempotency.py`'s `_sweep_if_new_hour` pattern, day-grained).
   Unit tests: create→get round trip, checksum stable under key reordering,
   sweep deletes rows older than 7 days on the first write of a new day.
7. **`door/nouns/plugins.py`.** The core noun: `list`/`get` (layout via
   `editor_layout.positions` + the promoted `editor_server` helpers),
   `create`/`act("install-from-git")` over `install_from_git()`,
   `act("disable"/"enable")` over `set_state()`, `act("save")` over
   `plugins.manifest_from_dict` → `editor_server._write_manifest` (imported
   directly, per spec.md's cross-module-private-import precedent) with
   `_manifest_error_to_problem()`, `act("set-settings")` as a pure
   capability-gated stub, `search_doc`. Unit tests per spec.md's Acceptance
   criteria: layout completeness, save-with-a-bad-step returns
   `nodes.<name>.<field>` errors and writes nothing, install-from-git with a
   bad tag is 400.
8. **`door/nouns/workflows.py`, `nodes.py`, `tools.py`.** Built on step 7's
   patterns (`discover_plugins()`, deterministic sha256 ids) and step 2's
   `enabled_plugin_set()`. All effectively read-only; `nouns.unavailable()`
   for every write verb.
9. **`door/capabilities.py`.** Append the three names to `DECLARED`.
10. **Registration.** `subcommands/door.py`'s `nouns={...}` dict gains the
    five modules.
11. **`docs/console/nouns.md`, `docs/console/capabilities.md`.** The five
    sections; the three capability rows.
12. **The conformance run.** Each `tests/contract/nouns/test_*.py` file
    written in steps 6–8 carries its own dual-sided proof, per this
    project's existing per-noun convention: real
    `plugins.install`/`plugins.inspect`/`plugins.write` declared →
    install/inspect/save succeed; a `DoorContext` built with those three
    names stripped → each answers `501` naming the missing one.
13. **The curl-transcript proof script**, run once against a real `sadana
    door serve` + a local git fixture, output pasted at Deploy — not part of
    `make test`.
14. **Self-check** (`/ponytail-review` + `/simplify` against the whole diff),
    sort findings per build-skill's three buckets, apply "worth taking now"
    in this same diff.
15. **`make verify`**, report both the self-check outcome and the verify
    output.

The riskiest step is landed early (step 1: a syntax-broken branch) precisely
so everything after it builds on a green baseline, and the biggest single
piece of new logic (step 7, `plugins.py`) is ordered *after* every function
it depends on already has its own passing unit tests (steps 2–6) — so a
failure in step 7 is isolated to step 7's own new code, not tangled with
whether `install_from_git`/`inspect_tag`/the renamed helpers work.

## Risks

**What could this change break?**
- `client_surface.py`'s two fixes touch the one function (`open_runtime` →
  `enabled_plugin_set`) every `sadana` subcommand and the gateway daemon call
  on startup. Mitigated: `_enabled_plugin_set`'s only caller today is
  `open_runtime` itself (checked during design), and the rename plus filter
  fix is covered by a new regression test before anything else in this plan
  is written, so a break here is caught at step 2, not discovered at step 15.
- `editor_server.py`'s renames touch a closed work item's file. Mitigated:
  it is the one edit spec.md explicitly scoped there, `handle()`'s external
  HTTP behavior does not change (only the private-name surface moves), and
  `test_editor_server.py` is run immediately after the rename (step 3) to
  prove that before anything downstream depends on the new names.
- `marketplace.submit()`'s behavior for reviewers/browsers (`decide()`,
  `latest_approved()`, `pending_releases()`) must not change when its
  fetch/validate logic moves into `inspect_tag()`. Mitigated: `submit()`'s
  own existing test file is run unchanged immediately after the extraction
  (step 5), before `inspections.py` (step 6) is written against the new
  function.
- Existing `sadana door serve` callers (the loopback listener, the
  conformance test's own `door` fixture) pick up three newly-declared
  capabilities the moment `capabilities.py` changes (step 9) — every prior
  noun's own capability-gated behavior is unaffected, since `DECLARED` is
  append-only and nothing removes or reorders an existing entry.

**Which step is the most risky, and why?**
Step 7 (`door/nouns/plugins.py`) — it is the only step combining several
independent pieces of new logic in one file (layout rendering, the
install-from-git dual entry point, the save error-mapping heuristic,
disable/enable). It is ordered last among the new-logic steps specifically
so every piece it calls (`install_from_git`, `set_state`, the promoted
`editor_server` helpers, `editor_layout.positions`) is already unit-tested
in isolation by the time this step's own tests run — a failure here can only
be this file's own wiring, not a dependency.

**Which options did spec.md already reject, and is this plan drifting back
toward one?**
- No second git-fetch path: `install_from_git()` calls the existing
  `fetch_verified_tag()`/`_is_valid_tag_syntax()`, never `subprocess`/`_git`
  directly. Checked at step 4's own review before merging into the noun.
- No stored layout: `plugins.py`'s `get()` computes `editor_layout.positions()`
  fresh every call, per spec.md § guideline 4 — nothing in step 7 adds a
  `layout` column anywhere.
- No cached `PluginSet` on `DoorContext`: step 8's `tools.py` derives it
  fresh via `client_surface.enabled_plugin_set(ctx.conns.reader())` per
  request, never stored on `ctx`.
- No second manifest validator: step 7's `save` action reuses
  `plugins.manifest_from_dict`/`editor_server._write_manifest` verbatim;
  `_manifest_error_to_problem()` only classifies the `ValueError` text
  already produced, it does not re-validate anything itself.
- No pre-registration hack through `plugin_install.register()`/`resolve()`:
  `install_from_git()` is new and narrower, exactly the choice spec.md's
  Rejected alternatives already made.

## Proof

- `make verify` ends `VERIFY OK`, pasted in full.
- `tests/unit/test_plugin_install.py::test_install_from_git_*` (exact test
  names chosen while writing, following this file's existing naming style)
  covers: discovers the plugin's name from the fetched manifest;
  refuses on `expect_name` mismatch; a syntactically invalid tag is refused
  before any clone (assert no directory created under a temp plugins root).
- `tests/unit/test_marketplace.py::test_inspect_tag_*` covers: returns
  `manifest_to_dict` + revision for a valid tag; the plugin's own module
  name is asserted absent from `sys.modules` after the call; `submit()`'s
  existing test file is unchanged and green.
- `tests/unit/test_door_nouns_plugins.py` covers, at minimum: the fixture
  plugin's layout has every node positioned and every edge resolving to a
  declared node name; `needs_code` matches `editor_server.waiting()`
  exactly; saving a manifest with a bad node field returns `errors[].field
  == "nodes.<name>.<field>"` and leaves `plugin.toml` byte-identical to
  before the call; `create`/`install-from-git` with a bad tag is `400`
  before any operation is promoted.
- `tests/unit/test_client_surface.py` (new case) covers: a plugin installed
  under a directory name that differs from its manifest name, disabled by
  directory name, is excluded from a fresh `build_plugin_set`'s catalog and
  tool surface — the Requirement 6 regression test.
- `tests/contract/test_console_grammar.py`'s extension proves both
  capability-gating directions, per spec.md's own Acceptance criteria: with
  the three names declared, install/inspect/save succeed end-to-end against
  a real `DoorContext`; with a second `DoorContext` built from
  `capabilities.declared()` minus those three names, the same three requests
  each answer `501 HARNESS_CAPABILITY_MISSING` naming the specific missing
  capability.
- A standalone script's output (curl or direct `handle()` calls) — list →
  get (layout) → create from a local git fixture → poll the operation →
  inspect the same tag → save with an error → save clean — pasted as
  Deploy-stage Evidence, run outside `make test` per CLAUDE.md.
