# Plan: Naming a plugin is enough to get a verified copy of it (from intent.md 2026-09-10)

Intent: `docs/tasks/PLUGIN-INSTALL-01-registry-fetch-verify-place/intent.md`
Spec: `docs/tasks/PLUGIN-INSTALL-01-registry-fetch-verify-place/spec.md`

## Context

Plugins in sadana-harness today are folders copied into place by hand under
`SADANA_PLUGINS_DIR`. `plugin_blueprint.md` §9 explicitly left "installation"
(fetch a repo at a tag, verify it, place it) out of the closed PLUGINS
block's scope. This work item builds that: a small registry (`name → git
URL`, instance-owner-only writes) plus a fetch/verify/place pipeline, so
`sadana plugin install <name> <tag>` produces a verified plugin directory
under the same root `plugin_manifest.discover_plugins()` already scans —
with zero changes to any already-closed PLUGINS file.

## Files that change

- `src/sadana/plugin_install.py` (new) — the registry table + `register()`/
  `resolve()`, the git-fetch/verify/place pipeline, and the closed
  `RegisterOutcome`/`InstallOutcome` dataclass sets.
- `src/sadana/subcommands/plugin.py` (new) — `build_plugin_parser()`,
  `cmd_plugin_register()`, `cmd_plugin_install()`. Mirrors
  `subcommands/runs.py`'s shape (one file, its own parser and handlers).
- `src/sadana/cli.py` — one import + one `build_plugin_parser(subparsers)`
  call in `build_parser()`, same shape as every other subcommand already
  wired there.
- `tests/unit/test_plugin_install.py` (new)
- `tests/unit/test_subcommands_plugin.py` (new)
- `scripts/prove_plugin_install.py` (new) — standalone, real-network proof
  script (see `prove_conversation_e2e.py` for the project's existing shape
  of this). Not run by `make test`; run once by hand, output pasted into
  this work item's `review.md` at Deploy.
- `tests/conftest.py` — added `run_git()`/`make_upstream_repo()`, the real
  local-git-repository fixture both new test files need. Not anticipated
  when this plan was first drafted; added during the build-stage self-check
  (`/ponytail-review` caught the two test files defining identical copies
  of both helpers) rather than written independently by each test file.

## Reference corpus, reused directly

From `hermes_cli/plugins_cmd.py` (spec.md already cites line numbers):
clone into a `tempfile.TemporaryDirectory(dir=plugins_root)`, verify, then
`os.replace()` into place; and the existing-target swap (`os.replace()`
can't rename onto a non-empty directory, so an existing target is moved
aside to a sibling backup path first, and restored on any failure during
the swap). Adopted as-is — this is solving an OS constraint, not a policy
choice.

From this project's own code: `plugins._parse_manifest()` is reused
directly to read a fetched `plugin.toml`'s declared name (the same
cross-module reuse `plugin_manifest.py:149` already does) — no second TOML
reader. `plugin_manifest.validate()`'s full §8 checks are deliberately not
re-run here (already closed, already run at catalog-build time).

## Design refinement vs. spec.md's narrative order (not a contract change)

`spec.md`'s Design section describes verification as "clone, then compare
HEAD against a remote query." This plan resolves the tag against the
remote **first** (`git ls-remote <repo_url> refs/tags/<tag>`), before
cloning:

1. Reject a syntactically invalid tag (`git check-ref-format
   --allow-onelevel refs/tags/<tag>`, plus a 40-hex-char bare commit is
   rejected as not a tag) — no network yet.
2. Resolve `name` → `repo_url` via the registry. Miss → `UnknownPluginName`.
3. Refuse early if `(plugins_root / name).exists()` and not `--replace`.
4. **`git ls-remote <repo_url> refs/tags/<tag>`** (preferring the peeled
   `^{}` line for an annotated tag) to get the tag's real commit. Empty
   result → `FetchFailed` (spec.md's Interface already documents
   `FetchFailed` as covering "unknown tag" — no new outcome variant needed).
5. Clone at that tag into a temp dir under `plugins_root`
   (`git clone --depth 1 --branch <tag> <repo_url> <tmp>/plugin`).
6. Read the clone's `HEAD` and compare to step 4's resolved commit.
   Disagreement → `TagMismatch`.
7. Parse the clone's `plugin.toml` via `plugins._parse_manifest()`; its
   `name` must equal the registry `name`. Disagreement → `NameMismatch`. A
   parse failure here is folded into `FetchFailed` (spec.md: "carries
   whatever git's own stderr said" — extended here to "fetched something
   that isn't usable," still the same outcome variant, no new one).
8. Place it: if `--replace` and a prior directory exists, move it aside to
   a sibling backup, `os.replace()` the verified clone into place, then
   remove the backup; restore the backup on any exception. Returns
   `Installed`.

This only reorders steps 4/5 relative to spec.md's prose (resolve-then-clone
instead of clone-then-resolve) so an unknown tag fails before a wasted
clone — cheaper failure, same outcome set, same requirements. It changes no
`## Requirements`, no `## Interface`, and no `## Rejected alternatives`
item in spec.md.

## Order of work

1. **`plugin_install.py`, DB layer only**: the six outcome dataclasses
   (`Registered`, `NameTaken`, `Installed`, `UnknownPluginName`,
   `AlreadyInstalled`, `NameMismatch`, `TagMismatch`, `FetchFailed`), the
   `CREATE TABLE IF NOT EXISTS plugin_registry (...)` schema (mirroring
   `observability.py`'s `_SCHEMA` constant + idempotent create), and
   `register()`/`resolve()` against `conversation_store.open_store()`. No
   subprocess yet. Test with `tests/unit/test_plugin_install.py`'s
   registry-only cases (register, re-register → `NameTaken`, resolve
   hit/miss).
2. **`plugin_install.py`, the git pipeline**: `_git()` subprocess helper
   (timeout, `stdin=DEVNULL`, `GIT_TERMINAL_PROMPT=0` in its env so a
   mistyped/private URL fails fast instead of hanging on a credential
   prompt — this project has no credential path here at all, so a hang is
   the only thing to guard against), `_resolve_tag_commit()`,
   `_head_revision()`, and `install()` wiring all eight steps above. This
   is the riskiest step (real subprocess calls, the OS directory-replace
   edge case, a verify comparison whose mismatch branch only happens under
   a real-world race) — it lands *after* step 1's DB layer already works,
   so a rework here never touches the registry contract. Tested against a
   **local** git repository fixture (`git init` + one commit + one tag in
   `tmp_path`, cloned via its local filesystem path) — real `git`
   subprocess calls, zero network, per `testing-conventions`.
3. **`subcommands/plugin.py` + wire into `cli.py`**: thin argparse layer
   once `register()`/`install()` are proven; each outcome variant maps to
   a stderr line and a non-zero exit code, `Installed`/`Registered` map to
   0. Tests mirror `test_subcommands_runs.py`'s shape exactly.
4. **`scripts/prove_plugin_install.py`**: one real `register` + `install`
   against a genuine public git repository, printed output — run by hand,
   not part of `make test`. Its output is this work item's Deploy-stage
   evidence for the real external round trip (CLAUDE.md's rule), not
   proof for this stage.
5. **Self-check**: `/ponytail-review` and `/simplify` against the diff,
   then `make verify`.

## Risks

**What could this break?** Nothing already closed. `cli.py`'s change is one
import and one call, additive, in the same shape every prior subcommand
used — `chat`, `conversations`, `gateway`, `runs`, `setup` are all
untouched. The only shared resource touched is `conversations.sqlite3`,
and only additively: a new `CREATE TABLE IF NOT EXISTS plugin_registry`,
no change to `conversations`/`messages`/`turn_runs`/`plugin_runs`. Every
DB access here opens its own short-lived connection via
`conversation_store.open_store()`, exactly like `runs.py`'s `cmd_runs`
already does — no new concurrent-writer scenario beyond what already
exists and is already accepted (CLAUDE.md's own note: revisit the
single-connection posture only once a second *long-lived* writer actually
shows up; a one-shot CLI command isn't that).

**Riskiest step, and why**: step 2 (the git pipeline). It's the only step
touching a real subprocess, real OS rename semantics, and a verify branch
(`TagMismatch`) that only fires under a genuine race in normal operation —
meaning the happy path won't exercise it, so that branch has to be tested
by monkeypatching `_resolve_tag_commit()` to return a value that disagrees
with a real local clone's real `HEAD`, rather than by manufacturing a real
race. That's a deliberately faked seam for one hard-to-reproduce-for-real
branch, not a fake of the environment — the clone, the tag, and the
mismatch detection itself are all real; only the "what the remote claims
right now" input to the comparison is substituted. Ordering this step
after step 1 means if the git-subprocess logic needs rework mid-build, the
registry's schema and outcome types are already settled and don't move
too.

**Rejected-alternatives drift check** (re-read against spec.md's
`## Rejected alternatives`): this plan does not reintroduce a
caller-supplied 40-char SHA (tag stays the unit of "version" throughout);
does not add a pre-placement content/security scan; does not open a second
SQLite file (everything goes through `conversation_store.open_store()`);
and does not add a per-plugin "currently pinned revision" metadata file —
`install()` always takes an explicit `tag` argument, so there is nothing
to remember between calls. None of these are present in the design above.

## Proof

- `bash scripts/run_tests.sh tests/unit/test_plugin_install.py
  tests/unit/test_subcommands_plugin.py` green, covering: register + resolve
  hit/miss, re-register → `NameTaken`, install happy path against a local
  git fixture, `AlreadyInstalled` without `--replace`, a successful
  `--replace` that lands the new tag's content, `UnknownPluginName`,
  `NameMismatch`, `TagMismatch` (via the monkeypatched seam above), and an
  invalid tag string → `FetchFailed` before any subprocess runs.
- `tests/unit/test_plugin_install.py` also asserts
  `plugin_manifest.discover_plugins()` finds the freshly installed fixture
  plugin with no change on its side — the concrete proof of intent.md's
  "what notices this changed."
- `make verify` ends `VERIFY OK` (pasted in this conversation before
  reporting done).
- `scripts/prove_plugin_install.py`'s manual run against a real public
  repository is *not* part of this stage's proof — it is produced in step 4
  and consumed by the Deploy stage's `review.md`, per CLAUDE.md's rule that
  a block's first real external round trip is proved outside the unit
  suite.
