# Spec: Naming a plugin is enough to get a verified copy of it

Intent: docs/tasks/PLUGIN-INSTALL-01-registry-fetch-verify-place/intent.md

## Requirements

1. A person can register a plugin name against a git repository URL. Only
   the person running this instance can do this — no open submission path.
   (Intent, Proposed outcome ¶2; Constraints "only the instance owner".)
2. A person can install a plugin by giving its registered name and an exact
   released tag. The name resolves to a repository through the registry;
   nothing else resolves it. (Intent, Proposed outcome ¶1.)
3. Only a git tag counts as a "released version." A branch name or a bare
   commit is rejected before anything is fetched. (Intent, Constraints ¶1.)
4. Before anything is placed, the fetched tree is confirmed to genuinely be
   the tagged commit that was asked for. A mismatch is refused, visibly —
   never silently accepted. (Intent, Proposed outcome ¶3.)
5. A plugin lands as one complete, atomic placement under the directory the
   agent already reads installed plugins from (`_plugins_root()`). Nothing
   already on disk is edited in place. (Intent, Proposed outcome ¶1;
   Constraints ¶2.)
6. Installing under a name that already has a plugin on disk is refused
   unless replacement is explicitly requested. Replacement is a fresh
   placement, never a patch. (Intent, Proposed outcome ¶4.)
7. No credential of any kind is used, stored, or plumbed through anywhere in
   this path. Only public repositories are supported. (Intent, Constraints
   ¶3.)
8. Nothing this work item adds ever executes code from a fetched plugin.
   Whether that is safe later is explicitly out of scope here. (Intent,
   Constraints ¶5.)

## Design

**Where this sits relative to the plugin seam**

The plugin execution seam (D1–D4, E1, F1, G1–G3) is closed. This item is
purely additive on top of it: it produces a directory under
`_plugins_root()` that `plugin_manifest.discover_plugins()` already knows
how to pick up, with no change to `plugins.py`, `plugin_manifest.py`, or
`plugin_dispatch.py`. Guideline 2's caveat ("do not foreclose the seam")
doesn't even apply here — the seam already exists and this item is the kind
of growth it called for: one more way a plugin arrives, not new core
behaviour. Zero already-closed PLUGINS files are touched.

**Consulting the reference corpus**

`hermes_cli/plugins_cmd.py` (PLUGIN-SYSTEM, production-code) is the closest
analogue and the only one read in depth; `hermes_cli/plugin_index.py` and
`hermes_cli/plugin_packs.py` were read for the registry side.

Adopted, with attribution:

- **Clone into a temp dir inside the target root, verify, then
  `os.replace()` into place** (`_install_plugin_core`, lines ~715–825). This
  is exactly requirement 5's atomicity: a failed verification just discards
  a tempdir, never leaves a half-installed directory.
- **The existing-target swap**: `os.replace()` cannot rename a directory
  onto a non-empty existing directory (`ENOTEMPTY` on POSIX). Hermes solves
  this by moving the existing target aside to a sibling `previous-plugin`
  path first, renaming the new clone into place, and only then deleting (or,
  on failure, restoring) the backup (`_install_plugin_core`, the
  `replaced_existing` / `backup` block). Adopted as-is — it is solving an OS
  constraint, not a policy choice, so there is nothing project-specific to
  re-derive.
- **Fetch, checkout, then read `HEAD` back and compare** as the shape of a
  verification step (`_checkout_exact_revision`, `_git_head_revision`).
  Adopted for shape only — see below for what changes.

Declined, with reasons:

- **Pinning by a caller-supplied 40-character commit SHA**
  (`_normalize_exact_revision`, and `plugin_packs.py`'s comment that "tags
  and branch names are rejected — pin the commit for reproducible
  installs"). This project's own `plugin.toml` already commits to the tag
  as a plugin's version identity (`plugin_blueprint.md` §5.2 — `version =
  "0.3.1"  # the git tag`). Requiring a raw SHA at install time would give
  this project two incompatible ideas of "the version" — one in the
  manifest, one at the CLI — for a reproducibility guarantee this design
  gets a different way (see verification below). Declined.
- **Pre-placement security/content scan**
  (`_scan_plugin_tree`/`tools/plugin_guard.py`, run inside
  `_install_plugin_core` before the clone is moved into place). Intent.md's
  interview scoped "confirm it's really what it says it is" to tag
  integrity only, and explicitly declined deciding whether running a
  plugin's code is safe. A content scan is a step toward that later
  question, not this one. Declined as scope creep against the interviewed
  intent, not on its merits.
- **A cached, remotely-fetched community index with a staleness window**
  (`plugin_index.py`: fetches a shared JSON index, 24h cache, seed
  fallback). That machinery solves "many publishers, one shared index,
  fetched over the network." Ours is one owner writing rows to a database
  they already run — no fetch, no cache, no staleness. Declined as solving
  a problem this work item doesn't have.
- **A tracked "currently pinned revision" record per installed plugin**
  (`_read_install_metadata()`/`_write_install_metadata()`, a JSON file
  recording each plugin's source and revision so a bare reinstall can
  retain its prior pin). This project's `install` always takes an explicit
  tag — there is no "reinstall without a tag" case that needs a remembered
  prior pin. Declined; see State inventory below for why nothing replaces
  it.

**New modules**

`src/sadana/plugin_install.py` — the block's only new I/O module (git
subprocess calls, disk placement, and the registry's SQLite access all
live here together; they are one work item's worth of tightly coupled
behaviour, and splitting them now would be a bet against a second consumer
that doesn't exist — guideline 2). It touches disk, network, and a
subprocess, so per CLAUDE.md it is its own file, never sharing one with
`plugins.py` (explicitly pure-data today) or `plugin_manifest.py`.

It depends on `sadana.plugins` (for `_plugins_root()`) and
`sadana.conversation_store` (for the shared SQLite connection). It does not
import `sadana.conversation` — nothing here needs a live conversation, so
this stays consistent with the PLUGINS leaf modules' acyclic-import rule
even though that rule is literally written about `plugins.py` and
`plugin_manifest.py`, not this new file.

`src/sadana/subcommands/plugin.py` — `build_plugin_parser(subparsers)`,
mirroring `subcommands/runs.py`'s shape (one file owning its parser and its
handlers). Two subcommands:

- `sadana plugin register <name> <repo-url>`
- `sadana plugin install <name> <tag> [--replace]`

No new authentication layer gates `register`. "Only the instance owner"
(requirement 1) is satisfied the same way every other admin-shaped command
in this codebase already is — `sadana gateway install` has no separate
permission check either — by requiring local CLI access to the machine
running sadana. Inventing a role system for one command would be exactly
the kind of unrequested infrastructure guideline 2 warns against.

**The registry**

One table, in the same database `conversation_store` already owns
(`conversations.sqlite3`), opened through `conversation_store.open_store()`
— not a second SQLite file. `OBSERVABILITY-01`'s `observability.py` set this
precedent directly: reuse the one connection/file rather than open a second
writer against the same class of storage, per CLAUDE.md's standing warning
about a second writer racing one SQLite WAL file. This work item introduces
no long-lived second writer — `register` and `install` are both short CLI
invocations, the same shape `runs.py`'s `cmd_runs` already has.

```sql
CREATE TABLE IF NOT EXISTS plugin_registry (
    name           TEXT PRIMARY KEY,
    repo_url       TEXT NOT NULL,
    registered_at  REAL NOT NULL
);
```

`name` is a real `PRIMARY KEY`, not an application-level check — CLAUDE.md's
rule that a long-lived name a user returns to needs a database constraint
behind it, applied literally. Table creation is the same idempotent
`CREATE TABLE IF NOT EXISTS` `observability.py` already uses; there is no
column being added to an existing table, so the `PRAGMA table_info` +
guarded `ALTER TABLE` rule doesn't apply here — that rule is specifically
about widening a live table without breaking old rows, and this table has
no rows to break yet.

A failed `register` or `install` write is not the observability case:
`observability.py`'s "catch and log, never raise" rule is about a
best-effort side-recording of a run that already happened. Here the
database write *is* the thing the user asked for — its failure must reach
the caller as a real outcome, not be swallowed.

**Registering**

```python
def register(conn: sqlite3.Connection, name: str, repo_url: str, *, now: float) -> RegisterOutcome
```

`RegisterOutcome = Registered | NameTaken` — a closed set, not an
exception, matching `plugin_manifest.py`'s own `ManifestOutcome` shape
(CLAUDE.md: "a classified outcome whose branches carry meaningfully
different data is a named, closed set of outcome types"). Re-registering an
existing name is `NameTaken`, not a silent overwrite — the same
refuse-by-default posture requirement 6 applies to install applies here too,
for the same reason: a name is address-like, and repointing one silently is
how a person installs the wrong thing without knowing it.

**Installing**

```python
def install(
    conn: sqlite3.Connection,
    name: str,
    tag: str,
    *,
    plugins_root: Path,
    replace: bool = False,
) -> InstallOutcome
```

`InstallOutcome = Installed | UnknownPluginName | AlreadyInstalled | NameMismatch | TagMismatch | FetchFailed`
— again a closed set, never a raised exception, for the same reason as
`RegisterOutcome`. The CLI handler maps each variant to stderr text and a
non-zero exit code; only `Installed` returns 0. This is the concrete
application of the CLI-handler rule ("returns an int for its own exit
code") to a case with several distinct failure shapes instead of one.

Steps, in order — each one is where an existing scenario gets caught,
called out per guideline 3:

1. **Reject a non-tag `tag` argument outright** (requirement 3) — a syntax
   check, no network yet. Cheapest possible catch of that scenario: it
   never reaches a subprocess.
2. **Resolve `name` via the registry** (`SELECT repo_url FROM
   plugin_registry WHERE name = ?`). Miss → `UnknownPluginName`.
3. **Refuse early if already installed and not `--replace`**
   (`(plugins_root / name).exists()`) — requirement 6, checked before any
   network call, not after a wasted clone.
4. **Clone at the tag, into a temp directory inside `plugins_root`**
   (`git clone --branch <tag> --depth 1 <repo_url> <tmp>/plugin`) —
   `tempfile.TemporaryDirectory(dir=plugins_root)` guarantees the later
   `os.replace()` stays on one filesystem.
5. **Verify tag integrity** (requirement 4): read `HEAD` inside the clone
   (`git rev-parse HEAD`) and separately ask the remote what commit `tag`
   actually names right now (`git ls-remote <repo_url> refs/tags/<tag>`,
   preferring the peeled `^{}` entry when the tag is annotated). Disagreement
   → `TagMismatch`, and the temp clone is simply discarded — this is
   guideline 3's "make a step heavier," not "add a step": the single clone
   step now also proves what it fetched, instead of a separate pass added
   after the fact. Verifying here, before placement, is also why cleanup on
   failure costs nothing more than deleting a tempdir, versus verifying
   after placement and needing an un-place step.
6. **Check the fetched `plugin.toml`'s own declared name equals `name`.**
   Not one of intent.md's interviewed checks, but implied by "put it in the
   right place": `_skill_path()` addresses a plugin's skills by its
   directory name, so a directory placed under one name whose manifest
   claims another would silently break skill resolution the first time
   anything tries to use it. Disagreement → `NameMismatch`. (Flagged in
   Concerns — this is plumbing correctness inferred from the intent, not a
   line item that was actually put to the user.)
7. **Place it**: if a prior directory exists (the `--replace` case), move it
   aside to a sibling backup path first — `os.replace()` cannot rename onto
   a non-empty directory — then `os.replace()` the verified temp clone into
   `plugins_root / name`, then remove the backup. On any exception during
   this step, restore the backup before re-raising, exactly as
   `_install_plugin_core` does. Returns `Installed`.

No plugin.toml §8 validation (schema files exist, nodes reachable, etc.)
happens here — that is `plugin_manifest.validate()`'s job, already closed,
already run whenever the catalog is built. Re-running it at install time
would be a second way to do the same check.

**State inventory (guideline 4)**

| State | Why it's stored, not derived |
| --- | --- |
| `plugin_registry` rows (name, repo_url) | This *is* the fact a person recorded — "this name means this repository." Nothing on disk or in git can regenerate it. |
| Whether a name is currently installed | **Not stored.** `(plugins_root / name).exists()` derives it fresh every call, same posture `plugins._plugins_root()` itself already takes ("resolved fresh on every call, never cached"). |
| Which tag/revision a name is currently installed at | **Not stored anywhere**, unlike hermes's per-plugin pin metadata file. Every `install` call requires an explicit tag, so there is never a "reinstall without saying which version" case that would need a remembered prior answer. Removing the whole concept removes the metadata file's failure modes along with it (an install that half-updates the directory but not the metadata, or vice versa) instead of needing to keep the two consistent. |

## Interface

CLI:

```
sadana plugin register <name> <repo-url>
sadana plugin install <name> <tag> [--replace]
```

Exit codes: `0` on success. Non-zero (with a one-line stderr message naming
the outcome) for every other `RegisterOutcome`/`InstallOutcome` variant —
`NameTaken`, `UnknownPluginName`, `AlreadyInstalled`, `NameMismatch`,
`TagMismatch`, `FetchFailed`.

Python surface (`sadana.plugin_install`):

```python
RegisterOutcome = Registered | NameTaken
InstallOutcome = (
    Installed | UnknownPluginName | AlreadyInstalled
    | NameMismatch | TagMismatch | FetchFailed
)

def register(conn, name: str, repo_url: str, *, now: float) -> RegisterOutcome: ...
def install(conn, name: str, tag: str, *, plugins_root: Path, replace: bool = False) -> InstallOutcome: ...
```

`FetchFailed` carries whatever `git`'s own stderr said (git not installed,
network unreachable, unknown tag, unknown repo) — surfaced, never swallowed,
never re-interpreted into a guess about which of those it was.

## Acceptance criteria

- [ ] `register` followed by `install` against a **local** git repository
      fixture (a real `git init` + tag in a tmp dir — real git, no network)
      places a verified plugin directory under a tmp `plugins_root`.
- [ ] A tag argument that is not a valid tag name is refused before any
      subprocess runs.
- [ ] A repository whose tag, at verification time, doesn't match what got
      checked out yields `TagMismatch`, and no directory is left behind
      under `plugins_root`.
- [ ] Installing the same name twice without `--replace` yields
      `AlreadyInstalled`; nothing on disk changes.
- [ ] Installing again with `--replace` succeeds and the directory's
      contents reflect the newly requested tag, not the old one.
- [ ] Installing an unregistered name yields `UnknownPluginName`, not a
      crash or a stack trace.
- [ ] A plugin whose `plugin.toml` name disagrees with the registered name
      yields `NameMismatch`.
- [ ] `plugin_manifest.discover_plugins()` finds the freshly installed
      plugin with no code change on its side — the concrete proof of
      intent.md's "what notices this changed."
- [ ] `make verify` ends `VERIFY OK`.
- [ ] A standalone script (outside `make test`) performs one real
      `register` + `install` against a genuine public git repository over
      the network, and its output is pasted as this work item's Deploy-stage
      Evidence — per CLAUDE.md's rule that a block's first real external
      round trip is proved outside the unit suite, never by relaxing the
      network ban inside it.

## Non-goals

- Executing any code from an installed plugin (requirement 8).
- Private repositories or any credential handling.
- A public registry-submission path, or sharing the registry across
  machines/instances.
- Re-validating `plugin.toml`'s full manifest contract (already
  `plugin_manifest.validate()`'s job).
- `uninstall`/`update` commands. `--replace` on `install` is the only
  lifecycle verb this item adds.

## Open questions

- Carried from intent.md: whether this registry is ever shared across
  machines is left open — it only matters once a marketplace becomes its
  own work item, and this registry is deliberately just that item's seed.

## Rejected alternatives

(Interleaved above, by decision, per guideline 1 — repeated here as a flat
list for a reviewer scanning only this section.)

- Caller-supplied 40-character commit SHA instead of a tag — declined,
  conflicts with `plugin.toml`'s own tag-as-version convention.
- Pre-placement content/security scan — declined as scope creep against
  the interviewed intent.
- A cached, network-fetched shared index — declined, solves a multi-owner
  problem this registry doesn't have.
- A per-plugin "currently pinned revision" metadata file — declined,
  nothing needs it once every install call already takes an explicit tag.
- A second SQLite database file for the registry — declined, reuses
  `conversation_store`'s existing connection per its own precedent.
- A role/permission system gating `register` — declined, no auth layer
  exists anywhere else in this codebase for equivalent admin commands.

## Concerns

- **The real, unresolved tension**: `plugin_blueprint.md` §9 places "the
  marketplace: discovery" outside the PLUGINS block's boundary, in so many
  words. A name→repository registry is discovery, in miniature. This spec
  builds it anyway, because intent.md's own "Changed during planning"
  section records that the user chose this expansion deliberately and on
  the record, and scoped it down (single-owner writes, a database
  constraint, no network fetch) to keep it from becoming the marketplace
  itself. A reviewer who reads `plugin_blueprint.md` without also reading
  this intent's trace could reasonably flag this whole work item as a
  boundary violation. It isn't one, but only intent.md proves that — this
  spec, `plan.md`, and `review.md` should all point back to it rather than
  re-litigate it independently.
- **Requirement 6 ("`NameMismatch`") was inferred, not interviewed.** The
  plan-stage interview never asked whether a plugin's own declared name has
  to match the name it was installed under. I added the check because
  `_skill_path()`'s existing addressing scheme silently depends on it. It's
  a small, one-directional correctness check with an obvious failure mode
  if it's missing (skills silently fail to resolve later), but it is a
  requirement this spec introduced, not one intent.md stated — worth a
  second look at Deploy.
- **No automated test exercises a real network fetch** — `testing-conventions`
  bans it from `make test` outright. All confidence in the real,
  internet-hosted case comes from one pasted script run at Deploy. A git
  hosting quirk that only shows up over a real network (auth redirects,
  LFS pointers, submodules) will not be caught by anything that runs
  routinely after this lands.
- **Verifying a tag by comparing two live remote queries has a narrow,
  accepted race**: if the upstream tag is legitimately moved by its own
  maintainer between the clone and the `ls-remote` check, this reports
  `TagMismatch` on a false positive. Treated as acceptable — install is an
  infrequent, human-driven operation, not a hot path, and retrying is
  cheap. Not treated as acceptable would mean holding the repository still
  across two network calls, which is not something a client controls.
