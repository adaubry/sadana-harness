# Spec: Plugins on the console

Intent: docs/tasks/H24-door-nouns-plugins-layout-install-inspect/intent.md

Author: adam aubry (project owner). Status: approved.

## What the prompt assumed and what is true

The build brief this work item started from predates H15–H21 and was written
against an imagined door. Checked against what actually landed:

- **`door/`'s real shape** (H19: `docs/tasks/H19-door-framework-token-conformance/`).
  `NounSpec`/`ActionSpec`/`NounModule` (`door/nouns/__init__.py`) match the
  brief closely. Two things the brief didn't know: `create`/`update`/`remove`
  are **not** auto-gated by capability — only a declared `action` is
  (`router.py:307-310`) — so `create`'s `plugins.install` check and
  `save`'s `plugins.write` check (an action) are gated two different ways, by
  design, matching `schedules.py`'s existing `_require_write` pattern. And
  every non-`list`/`get` verb already runs through `operations.run_bounded`
  (`router.py:317-323`) — a slow `create`/`act` is promoted to a `202
  Operation` **automatically**; nothing in this noun needs to ask for that.
  `capabilities.declared()` is a literal tuple, not composed from modules —
  confirmed by reading `door/capabilities.py` — so this work item edits it
  directly, per the user's own instruction for that case.
- **`plugin_state`'s real shape** (H16: `docs/tasks/H16-store-identity-ledger-locks/`,
  `plugin_install.py:61-70`). Exactly what the brief assumed: `name TEXT
  PRIMARY KEY` (the plugin's directory name), `id TEXT UNIQUE NOT NULL`,
  `state`, `source_repo`, `source_tag`, `installed_at`, `updated_at`,
  `version`. No divergence to record.
- **`editor_server.py`'s helpers.** `_assess`, `_waiting`, `_list_plugins`
  are still private — nothing has promoted them. The promotion described
  below is this work item's, and the only edit this item makes to that file.

One further, load-bearing gap the brief could not have known about, found
while reading `plugin_install.py`'s own docstrings during this design pass:
`client_surface._enabled_plugin_set()` already filters on
`p.manifest.name not in disabled_names(conn)` — but `disabled_names()` reads
`plugin_state.name`, which is the **directory** name, while
`InstalledPlugin.manifest.name` is the **declared** name inside
`plugin.toml`. `plugin_install.install()` forces these equal at install time
(`NameMismatch` refuses any mismatch), but `reconcile_plugin_state` does not
— a directory renamed after install, or hand-placed with folder name ≠
declared name, would carry a `plugin_state` row keyed by the folder name that
this filter can never match against. Today this is latent: nothing writes
`disabled` yet. This work item is what writes it, so this work item is what
must close the gap — see Requirement 6.

## Requirements

1. A console principal can list and get the plugins installed on this
   harness — including a computed diagram of each plugin's steps and how
   they connect, and which steps still need something — without the harness
   ever importing or running a line of any plugin's own code to answer.
   (Intent § Proposed outcome, § Constraints "nothing... may run a plugin's
   own code.")
2. A console principal can install a new plugin by naming a git repository
   and a tag; the harness fetches it, confirms the fetched tree really is
   that tag, and never executes anything from it before or during that
   confirmation. (Intent § Proposed outcome, § Constraints.)
3. A console principal can ask this harness what a candidate plugin at a
   given repository and tag would look like, before installing it — same
   no-execution guarantee — and get back a checksum of what was declared, so
   a later install can be checked against what was inspected. (Intent
   § Proposed outcome.)
4. A console principal can save an edited plugin structure back to this
   harness, under the exact validation the harness's own local editor
   already enforces on itself, with field-level errors when a step is wrong.
   (Intent § Proposed outcome, § Constraints "the plugin's own file on disk
   stays the one real definition.")
5. A console principal can turn an installed plugin off or back on without
   deleting it, and the harness actually stops offering a disabled plugin's
   tools the next time it decides what a plugin can do. (Intent § Proposed
   outcome.)
6. The name-matching gap between how `plugin_state` indexes a plugin and how
   the "is this plugin switched off" filter looks it up is closed before
   `disable` ships, so turning a plugin off is never a silent no-op. (Intent
   § Changed during planning.)
7. Every one of the above is reachable through the same wire grammar, error
   shape, capability gate and operation-promotion machinery every other
   console-facing noun on this harness already uses — no new machinery.
   (Intent § Proposed outcome, § Affected users and systems.)
8. This work item's files stay inside what it owns today; it adds exactly
   three capability names and touches no file named as closed to it. (Intent
   § Constraints, "someone else is doing related work... at the same time.")

## Design

**1. Learn from the reference / learn from this repository first.**

No hermes-agent file is analogous to a console-facing plugin-management API —
`docs/reference/hermes_core_blocks_kind.csv`'s `PLUGIN-SYSTEM` block has no
install/registry/inspect surface exposed over HTTP; the nearest thing,
`hermes_cli/mcp_catalog.py`, is a CLI-side tool listing, not a web API, and
nothing in it survives contact with this project's own DAG-manifest shape
(`plugin_blueprint.md`'s own design, already built). This work item's real
prior art is **in this repository**: `PLUGIN-INSTALL-01` (git-fetch-and-verify:
`plugin_install.py`), `PLUGIN-MARKET-01` (fetch-validate-never-execute:
`marketplace.py`), `PLUGIN-EDITOR-01` (layout-and-save-with-a-read-back-guard:
`editor_server.py`/`editor_layout.py`), and `H27`'s `schedules.py` (the
`NounSpec`/`ActionSpec`/manual-capability-check pattern this work item's own
noun modules copy directly). Guideline 1 is satisfied by reusing all four,
not by reading hermes-agent — a hermes citation here would be reaching past
better, already-adopted prior art for a worse, unadopted one.

**2. Reduce the number of bets.**

This work item sits squarely past the plugin seam — plugins already exist,
are already installed, saved and vetted by three closed work items. Nothing
here invents infrastructure; every new door noun is an **addition** (a new
`door/nouns/*.py` file plus one registration line), never a modification to
`plugins.py`, `plugin_manifest.py`, `editor_server.py`'s save/write path, or
`marketplace.py`'s claim/reject logic. The two exceptions — the disable-filter
name fix and the pre-existing broken merge — are both **repairs of code that
was never correct**, not new bets; see § Concerns.

**3. Catch the scenario at the least step-cost.**

The five new console-facing capabilities are answered as five **read
projections and two write actions over data that already exists on disk**:
`plugin_state` (H16), the manifest file itself (D2), and `runtime.plugin_set`
(D3) — no new stored state for `plugin`/`workflow`/`node`/`tool`. `inspection`
is the one genuinely new table, because a checked-tag's declared shape is not
derivable from anything already on disk (the tag may never be installed) and
the console's own registry needs it to persist across the 2-second operation
boundary and be pollable afterward — a step the design cannot make cheaper,
since the fact it stores (what a tag looked like when checked) has no other
home. Everywhere else, the cheapest of the three moves (add a step / make a
step heavier / make a step harder) is **add a step that reads what's already
there** — the same move `agent_template`'s and `memory_policy`'s own
synthetic-row precedent already established, not a heavier step (no new
column on `plugin_state`) and not a harder one (no new capability to reach
existing data).

**4. Minimise mutable state.**

Inventory of everything this work item stores:

| State | Why it must be stored | Why nothing else here is |
| --- | --- | --- |
| `inspections` table (new) | The declared shape and checksum of a *tag that was checked*, which may never be installed and has no other record. Retained 7 days per the console's own registry contract. | — |
| `plugin_state.state = 'disabled'` | Already H16's column; this item is only the first writer. | — |
| — | — | A plugin's **layout** (x/y/w/h, edges) is recomputed on every `get`, never stored — `editor_layout.positions()` already proved this is cheap and deterministic; storing it would be a second thing that can go stale against the manifest file, the single source of truth `_write_manifest`'s read-back guard already protects. |
| — | — | `workflow`/`node`/`tool` rows are **not persisted** — each is a pure projection over `discover_plugins()`'s already-parsed `Manifest` / `runtime.plugin_set.tool_specs`, recomputed per request. A `workflow`/`node`/`tool` id is a pure function of `(plugin name, entry.tool | node.name | tool.name)` — `"wfl_" + sha256(...)`/`"node_" + sha256(...)`/`"tool_" + sha256(...)` — the same documented exception `agent_template`'s `"tmpl_" + sha256(name)[:32]` id already takes in `docs/console/nouns.md`, for the same reason: nothing here is a row a ledger could point at. |
| — | — | `declared_settings`, `steps_count`, `needs_code_count` are read off the manifest fresh on every request — the same posture `plugins.missing_settings()` already takes ("Derived on every call, never stored: reading it fresh is what makes 'set it, then run again' work"). |

**The five nouns.**

**`plugin`** (`door/nouns/plugins.py`, prefix `plg`, no parent). Enumerated
from `plugin_state` (the authoritative existence/id/state list, including a
plugin that doesn't validate); manifest-derived fields (`declared_settings`,
`steps_count`, `needs_code_count`, `layout`) are filled from
`door/nouns/_plugin_manifests.assessed_by_directory_name()` — built on
`editor_server.assess()`'s `check_bodies=False` posture, **not**
`plugin_manifest.discover_plugins()` (see § Concerns: that function's
`check_bodies=True` excludes a plugin wholesale the moment any step still
needs code, which this surface exists to show) — and are empty/zero only
for a plugin whose `plugin.toml` doesn't even parse. This is a left join,
not an inner join: a plugin stays visible and listable even when nothing
valid can be said about its graph, matching `editor_server.list_plugins`'s
own reason for not using `discover_plugins()` as its enumeration either.

- `list`/`get`: as specified in the work item prompt (`name`, `version`,
  `source_repo?`, `source_tag?`, `description`, `declared_settings`,
  `steps_count`, `needs_code_count`, `layout` on `get` only, `state`).
  `layout.nodes[].x/y` from `editor_layout.positions()`; `w=160`, `h=56`
  fixed; `edges` from `plugins._successors(node)` for every node (reused
  directly — the same function `editor_layout.py` already imports from
  `plugins.py` for this exact purpose); `needs_code` per node is
  `node.name in {w["step"] for w in editor_server._waiting(directory,
  manifest)}`; `external_refs` is `[node.body]` for a `call` node, `[module
  half of node.body]` for a `compute` node, `[]` otherwise.
- `create` (`{repo_url, tag}`): manually checked against `plugins.install`
  (not auto-gated — see § What the prompt assumed). Calls a new
  `plugin_install.install_from_git()` (§ Interface) inside
  `operations.run_bounded`'s existing machinery — automatic, nothing this
  noun writes. `resource` on the promoted operation is `null`, the same
  documented, accepted `docs/reference/console_fit_plan.md` §6 gap
  `messages.create` already carries ("found during H20... H30... is where a
  create-time resource hint gets added") — the new plugin's id is
  discoverable through `/v1/changes` once the operation settles, the same
  path the console's registry already watches. Not re-litigated here.
- `act("install-from-git", {repo_url, tag})`: declared in `NounSpec.actions`
  with `capability="plugins.install"`, `from_states=()` (any state — this is
  how an already-registered plugin gets a new tag), so router auto-gates it.
  Internally the same `install_from_git()` call, with the target plugin's
  own `name` (read from its `plugin_state` row by `id`) passed as
  `expect_name` so a mismatched repository can never silently rename a
  plugin out from under its existing id.
- `act("disable"/"enable", ...)`: `ActionSpec(from_states=("installed",),
  to_state="disabled", capability="plugins.write", ...)` and its mirror.
  Writes `plugin_state.state` directly (a new, small function in
  `plugin_install.py` — `set_state(conn, id, to_state, now)` — mirroring
  `conversation_store.set_schedule_state`'s shape).
- `act("save", {manifest})`: declared with `capability="plugins.write"`,
  `from_states=()` (any state — fixing an `'error'` plugin's manifest is the
  point). Body: `plugins.manifest_from_dict(body["manifest"])` →
  `editor_server._write_manifest(directory, manifest)` — imported and called
  as the private function it is, the same cross-module reach
  `plugin_manifest.py` already takes into `plugins.py`'s own underscore
  names (`_parse_manifest`, `_node_index`, `_successors`, ...). A `ValueError`
  from either call is mapped to a `Problem` by `_manifest_error_to_problem()`
  (§ Interface) — never a second validation implementation.
- `act("set-settings", ...)`: declared with `capability="settings.write"`
  (H14's name, not in `DECLARED` yet) and no other logic — router's existing
  auto-gate alone produces `501 HARNESS_CAPABILITY_MISSING` naming it. This
  is the entire H14 stub; no code in this noun branches on it.
- `search_doc`: exactly as specified — `{title: name, subtitle: description,
  facets: {state, source_repo}}`.

**`workflow`** (`door/nouns/workflows.py`, prefix `wfl`, `parent=None` for
its own flat list, nested under `plugins` via `parent_id`). One row per
`Manifest.entries` member, across every plugin `discover_plugins()` returns.
`id = "wfl_" + sha256(f"{plugin.name}:{entry.tool}".encode()).hexdigest()[:32]`.
`node_count = len(manifest.nodes)` — the plugin's **whole** declared graph,
not a computed reachable-from-`entry.start` subset (§ Rejected alternatives).
Read-only: `create`/`update`/`remove` and every action answer
`nouns.unavailable()`, the same stub `agent_template.py` already uses for a
noun with no write path.

**`node`** (`door/nouns/nodes.py`, prefix `node`, nested under `workflows`).
One row per `Manifest.nodes` member of the **owning workflow's plugin** —
every node in that plugin's graph, not a per-entry reachable subset, for the
same reason `node_count` above isn't one (§ Rejected alternatives).
`id = "node_" + sha256(f"{plugin.name}:{node.name}".encode()).hexdigest()[:32]`.
`update({label})` renames the step: `plugins.rename_node(manifest, old, new)`
→ `editor_server._write_manifest`, gated `plugins.write` (manually checked,
since `update` is not auto-gated — same posture as `create`).

**`tool`** (`door/nouns/tools.py`, prefix `tool`, no parent, read-only). Over
the **enabled** tool surface, not a raw manifest walk — a disabled plugin's
tools must not appear here, the same rule `client_surface.take_turn` already
enforces for real dispatch. Since `DoorContext.runtime` is `auth.BoxIdentity`
(no `plugin_set` field — the brief's "`runtime.plugin_set.tool_specs`" names
a different `Runtime`, `client_surface.py`'s own dataclass, not this one; see
§ What the prompt assumed), this noun derives the same `PluginSet` fresh per
request from `client_surface.enabled_plugin_set(ctx.conns.reader())` — a
newly **public** function, the exact filter `client_surface._enabled_plugin_set`
already computes, promoted (not duplicated) so this noun and the real turn
path share one enforcement of "which plugins count as installed" (see § What
the prompt assumed's disable-filter fix, same function). `plugin_id` per
`ToolSpec` resolves through `PluginSet.by_tool[name][0].name`.

**`inspection`** (`door/nouns/inspections.py`, prefix `insp`, no parent, its
own table). `create({repo_url, tag})` → `operations.run_bounded` over
`marketplace.inspect_tag()` (§ Interface, extracted from `marketplace.submit`).
`get` returns the row exactly as specified in the work item prompt,
including `checksum` (sha256 of `json.dumps(declared_shape, sort_keys=True)`
— canonical, so the same declared shape always hashes the same regardless of
dict key order). Retention: a `_sweep_if_new_day()` guard, the identical
shape `door/idempotency.py`'s own `_sweep_if_new_hour()` already uses (module
global + lock, gating one `DELETE ... WHERE created_at < now - 7*86400`,
called from the write path) — reused as a pattern, not a shared function,
since the two live in different modules and the existing one is hour- not
day-grained.

## Interface

`plugin_install.py` additions (alongside, never replacing, `install()`):

```python
def install_from_git(
    conn: sqlite3.Connection, repo_url: str, tag: str, *,
    plugins_root: Path, expect_name: str | None = None, replace: bool = False,
) -> InstallOutcome:
    """Like install(), but the plugin's name comes from the fetched
    manifest, not a pre-registered one. `expect_name`, when given, refuses
    a NameMismatch the same way install() already does for its own name
    argument — the act()-path re-install case, where the plugin's identity
    must not silently change. Shares fetch_verified_tag(), _is_valid_tag_syntax()
    and _record_installed() with install() — no second git invocation
    anywhere in this module."""

def set_state(conn: sqlite3.Connection, id: str, to_state: str, *, now: float) -> None:
    """Writes plugin_state.state for one row, found by id — disable/enable's
    one write. Mirrors conversation_store.set_schedule_state's shape."""
```

`marketplace.py`:

```python
@dataclass(frozen=True)
class Inspected:
    manifest: dict[str, object]  # plugins.manifest_to_dict(...)
    revision: str

InspectOutcome = Inspected | plugin_install.TagMismatch | plugin_install.FetchFailed | InvalidManifest

def inspect_tag(repo_url: str, tag: str) -> InspectOutcome:
    """Clone `tag` from `repo_url` to a temp dir (fetch_verified_tag, whose
    `--` rule already covers repo_url), validate(check_bodies=False), and
    discard the clone. Never imports the plugin — check_bodies=False is not
    an optimisation here, it is the whole safety property this function
    exists for."""
```

`submit()` is rewritten to call `inspect_tag()` for the fetch-verify-validate
sequence, then keeps its own `_claim_release`/`_insert_release` persistence
unchanged — the extraction removes duplicated fetch/validate code from
`submit()`, it does not change what `submit()` does or returns.

`client_surface.py`:

```python
def enabled_plugin_set(conn: sqlite3.Connection) -> plugin_dispatch.PluginSet:
    """Promoted from _enabled_plugin_set — same body, one line fixed: the
    disable filter compares plugin_state's own key (directory name) against
    itself, not against the manifest's declared name."""
    disabled = plugin_install.disabled_names(conn)
    return plugin_dispatch.build_plugin_set(
        p for p in plugin_manifest.discover_plugins() if p.directory.name not in disabled
    )
```

`door/nouns/plugins.py`:

```python
_KNOWN_FIELDS = frozenset({"name", "kind", "body", "skill", "next", "ports"})
_STEP_ERROR_RE = re.compile(r"^(?:node|step) '([^']+)': '([^']+)'")

def _manifest_error_to_problem(detail: str) -> problems.Problem:
    """Best-effort field extraction from manifest_from_dict/_write_manifest's
    own prose ValueError text — never a second validator. A match names a
    step and a known field ('kind' failing its type check, e.g.); anything
    else (a value quoted where a field name would be, an entry- or
    settings-level error, the read-back guard's own sentence) falls back to
    `detail` whole, which is always correct, only sometimes less specific."""
    match = _STEP_ERROR_RE.match(detail)
    if match and match.group(2) in _KNOWN_FIELDS:
        return problems.make(
            "VALIDATION", detail, errors=({"field": f"nodes.{match.group(1)}.{match.group(2)}", "code": "invalid"},)
        )
    return problems.make("VALIDATION", detail)
```

**Registration.**

`src/sadana/subcommands/door.py` gains the five new noun modules in its
`nouns={...}` dict (`"plugins": plugins, "workflows": workflows, "nodes":
nodes, "tools": tools, "inspections": inspections`) — the same one line per
noun `schedules`'s own entry already took. **Found already modified,
uncommitted, at the start of this work item**: a duplicate-import merge
artifact (two `from sadana.door.nouns import ...` lines, both naming
`approvals`/`harness`) with a consolidating fix already staged but
uncommitted in the working tree — not this work item's doing, not yet
committed by whoever is mid-fix. This item's own noun registration is added
on top of whatever state that file is in by build time; if the consolidating
fix is still uncommitted, this item's own diff includes it (it is a
prerequisite for `subcommands/door.py` to import at all, the same class of
pre-existing breakage as `client_surface.py`'s duplicate `memory_context`
kwarg — see § Concerns).

`src/sadana/stores.py` gains one line in `ensure_schemas()`:
`door_nouns_inspections.ensure_schema(conn)` — the same "a new block adds
its line here" convention the module's own docstring documents, extended to
a `door/nouns/*.py` table for the first time (no prior noun owned one; see
§ Rejected alternatives).

## Acceptance criteria

- [ ] `plugin`/`workflow`/`node`/`tool`/`inspection` each implement
      `NounModule`; every verb not specified above answers `HARNESS_
      CAPABILITY_MISSING` via `nouns.unavailable()`.
- [ ] `GET /v1/plugins/{id}` for the fixture plugin returns a `layout` with
      every node positioned, every edge resolving to a real node, and
      `needs_code` matching `editor_server._waiting()` exactly.
- [ ] `POST /v1/plugins/{id}/actions/save` with a bad step returns `400
      VALIDATION` with `errors[].field` of the form `nodes.<name>.<field>`
      when the bad field is nameable, and writes nothing to `plugin.toml`
      (the existing read-back guard still the only writer).
- [ ] `POST /v1/plugins` (create) and `POST /v1/plugins/{id}/actions/
      install-from-git` with a syntactically invalid tag both answer `400`,
      never touching disk.
- [ ] `marketplace.inspect_tag()` against a local bare-repo git fixture
      returns the declared shape and revision, and the plugin's own module
      is never present in `sys.modules` afterward (asserted, not inferred).
- [ ] `disable` then a fresh `client_surface._enabled_plugin_set` (renamed
      `enabled_plugin_set`) excludes the plugin from `build_plugin_set`'s
      catalog and tool surface.
- [ ] `client_surface.py` imports and its existing test suite passes (the
      duplicate-kwarg fix, § Concerns, is a prerequisite for this item to
      write any code at all under the project's own gate).
- [ ] The conformance run (`tests/contract/test_console_grammar.py` and/or
      a dedicated `tests/unit/test_door_nouns_plugins.py`, decided at build)
      proves both sides of capability gating: with `plugins.install`/
      `plugins.inspect`/`plugins.write` declared, install/inspect/save
      succeed; with a `DoorContext` built with those three names stripped
      from `capabilities`, each answers `501 HARNESS_CAPABILITY_MISSING`
      naming the missing one.
- [ ] `make verify` ends `VERIFY OK`.
- [ ] A curl transcript: list → get (layout) → create from a local git
      fixture → poll the operation → inspect the same tag → save with an
      error → save clean.

## Non-goals

- `settings.write`/`secrets.write` (H14) and `upgrade` (H30) stay
  undeclared — `set-settings` exists only as a capability-gated stub.
- `provider`/`budget`/`integration`/`secret` nouns: not this work item
  (`docs/console/nouns.md` already says "not yet — no harness id assigned").
- Per-entry reachable-subgraph computation for `workflow.node_count`/nested
  `node` listing: rejected, see below.

## Open questions

None left open. The one live judgment call this design depended on — how far
to fix the disable-filter name mismatch — was resolved with the user before
this document (§ Requirements 5-6): fixed in `client_surface.py` alone, no
schema change, since `InstalledPlugin.directory` is already in hand at the
point the filter runs.

## Rejected alternatives

- **A per-entry reachable subgraph for `workflow.node_count` and nested
  `node` listing**, rather than the plugin's whole node set. Rejected on
  guideline 3: nothing in the work item's own field list asks for it, no
  console screen described anywhere in `docs/reference/console_fit_plan.md`
  needs it, and computing it is a strictly heavier step (a BFS per entry,
  per request) than the one already paid for elsewhere in this file
  (`editor_layout._depths`, itself a BFS over the whole graph). If a console
  screen later needs "only what this workflow reaches," that is new,
  additive logic in `workflows.py`/`nodes.py` alone — it does not require
  touching anything this design already ships.
- **Extending `DoorContext` with a cached `PluginSet` field**, for `tools.py`.
  Rejected on guideline 4: a cached set is state that can go stale within one
  process's lifetime (a plugin installed or disabled mid-process), while
  deriving it fresh per request — the same cost every other noun's `list`/
  `get` already pays reading `ctx.conns.reader()` — always reflects the
  current disabled set and touches no shared framework file a concurrent
  Lane A change might also be mid-editing.
- **A second, hand-rolled manifest validator for `save`'s field-level
  errors**, instead of parsing `manifest_from_dict`'s existing prose.
  Rejected on guideline 2 (one more bet: two validators that must be kept in
  agreement forever) and because `manifest_from_dict`/`_write_manifest` are
  closed work items' own tested code (`PLUGIN-EDITOR-01`) — CLAUDE.md forbids
  editing a closed work item's artifacts to match what this one needs;
  best-effort prose parsing that degrades to `detail` on anything it doesn't
  recognize is the design that needs nothing from that code to change.
- **Calling `plugin_install.install()` itself from the door**, pre-registering
  a plugin's name via `plugin_install.register()` first so the existing
  function's `resolve(conn, name)` has something to find. Rejected: `register()`
  is insert-only (an `IntegrityError`→`NameTaken` on a second call), so a
  re-install of an already-known plugin would have to special-case around its
  own registry — more code, and a fabricated `name` the caller does not have
  until after the very fetch `register()` would need it before. A new,
  narrower `install_from_git()` that discovers the name from the fetch itself
  is the cheaper of the two, and it does not touch `install()`'s own contract
  (still used by the CLI path, unchanged).

## Concerns

- **Superseded during build — not a live concern any more.** This entry
  originally read: "`discover_plugins()`'s `validate(child)` call defaults
  to `check_bodies=True`... this design's `plugin.get()` then shows `state:
  "installed"` with an empty `layout`/`declared_settings`... not fixed in
  this item." Writing `nodes.py`'s own test — a node whose `body` names a
  function that doesn't exist, the exact shape `needs_code`/`waiting`
  exists to surface — turned out to make the *entire plugin* vanish from
  `plugins.py`'s, `workflows.py`'s and `nodes.py`'s own listings, not just
  render one field emptied: `discover_plugins()` excludes a plugin wholesale
  the moment any one step needs code, which is not a cosmetic gap in a
  design meant to show exactly that state. Fixed, not left as a concern:
  `door/nouns/_plugin_manifests.py` (new; see `## Files that change`)
  reads every plugin directory through `editor_server.assess()`'s
  `check_bodies=False` posture instead, and `plugins.py`/`workflows.py`/
  `nodes.py` all use it in place of `plugin_manifest.discover_plugins()`.
  `tools.py` is the one noun that still uses `discover_plugins()` (via
  `client_surface.enabled_plugin_set()`) deliberately — a tool that cannot
  actually run should not be offered as callable, which is a different
  question from "can this plugin's structure be shown and edited."
- **Two pre-existing, uncommitted breakages sit on this branch, unrelated to
  this work item, both blocking `make verify` and therefore this item's own
  gate:** `client_surface.py:520/524`'s duplicate `memory_context` keyword
  argument (a bad-merge artifact at `HEAD`, commit `6b9f912`) and
  `subcommands/door.py`'s duplicate `door.nouns` import (also a bad-merge
  artifact, with a fix already staged uncommitted by someone). Both are fixed
  as this item's own first commits at build time — not because this item
  caused them, but because `client_surface.py` and `subcommands/door.py` are
  both files this item must edit anyway (Requirement 6's filter fix, and the
  five nouns' registration), and the project's plan-gate forbids editing
  either file before now. Flagged here per the design stage's own
  instruction to state tension plainly: this item's diff will contain two
  unrelated-looking one-line fixes, and `review.md` should not read them as
  scope creep.
- **`docs/console/grammar.json`'s `$defs` are all generic** (`StandardFields`,
  `Problem`, `ListParams`, ...) — no noun gets its own schema entry, matching
  every prior noun's own precedent (`schedules`, `agents`, ...). This item
  adds no new `$def`, and — per the "closed to you" list not naming
  `grammar.json` but this item having no reason to touch it either — doesn't.
- **No genuine policy conflict was found.** `testing-conventions` is loaded
  and applied at build time (this item writes no test code yet); no
  `project-structure` or `reference-lookup` skill exists in
  `.claude/skills/` to apply — said explicitly per the design-skill's own
  instruction, rather than left silent. Confidence this doesn't hide a real
  tension: every new file in this design is additive (guideline 2), every
  piece of state is justified against "why can't this be derived"
  (guideline 4), and the two repairs above are both restorations of a
  previously-working state, not new design surface.
