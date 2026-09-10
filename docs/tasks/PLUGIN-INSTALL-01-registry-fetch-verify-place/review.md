# Review: Naming a plugin is enough to get a verified copy of it (from plan.md 2026-09-10)

Reviewed: 1d2a41c..HEAD (staged, uncommitted) — 10 files, +1398/-0
Reviewer context: fresh session — this session was launched cold, with no
memory of writing this code, specifically to satisfy deploy-skill's "review
cold" requirement. No caveat needed.
Second opinion: none run independently at this stage — plan.md § Order of
work step 5 records a build-stage self-check (`/ponytail-review` + `/simplify`
against the diff, then `make verify`) before this review; this is the first
review pass since.

## Evidence

`make verify`, run fresh after the Bugs finding below was fixed (superseding
the pre-fix run this review originally pasted here — same 472 passed, same
`VERIFY OK`, now against the fixed code):

```
$ make verify
docs/tasks/PLUGIN-INSTALL-01-registry-fetch-verify-place: all present artifacts valid
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
Success: no issues found in 29 source files
TYPES OK
▶ hermetic run: TZ=UTC LANG=C.UTF-8 PYTHONHASHSEED=0, empty env
........................................................................ [ 15%]
........................................................................ [ 30%]
........................................................................ [ 45%]
........................................................................ [ 61%]
........................................................................ [ 76%]
........................................................................ [ 91%]
........................................                                 [100%]
472 passed in 7.31s
TESTS OK
VERIFY OK
```

`scripts/prove_plugin_install.py`, run fresh in this session against a real,
public repository over the network — the real-external-round-trip proof
spec.md's Acceptance criteria and CLAUDE.md's standing rule both require,
produced here because it had not yet been pasted anywhere for this work item:

```
$ python3 scripts/prove_plugin_install.py
register('prove-plugin-install-e2e', 'https://github.com/psf/requests.git') -> Registered(name='prove-plugin-install-e2e')
install('prove-plugin-install-e2e', 'v2.31.0') -> FetchFailed(detail="plugin.toml is invalid: [Errno 2] No such file or directory: '/tmp/sadana-plugin-install-e2e-plugins-y4oylr1s/.install-daiv43h8/plugin/plugin.toml'")  (2.11s)
OK for today's purposes: the real network round trip (resolve tag, clone, verify HEAD) completed successfully against a genuine remote — it only stops short of Installed because no real published repository yet has a plugin.toml in this project's own format. Pass a real plugin repository URL and tag as argv to prove the full path once one exists.
```

This confirms a real `ls-remote` tag resolution, a real `clone --depth 1
--branch v2.31.0`, and a real `HEAD`-vs-resolved-tag comparison all
succeeded against `github.com` before the (expected) stop at the missing
`plugin.toml` — the honest result the script's own docstring predicts, not a
failure of this work item.

## Scope

`git diff --staged --stat` (nothing is committed yet; base is `1d2a41c`,
current `HEAD`):

```
 docs/tasks/.../intent.md                           |  83 +++++
 docs/tasks/.../plan.md                             | 185 ++++++++++
 docs/tasks/.../spec.md                             | 384 +++++++++++++++++++++
 scripts/prove_plugin_install.py                    |  83 +++++
 src/sadana/cli.py                                  |   2 +
 src/sadana/plugin_install.py                       | 279 +++++++++++++++
 src/sadana/subcommands/plugin.py                   |  77 +++++
 tests/conftest.py                                  |  28 ++
 tests/unit/test_plugin_install.py                  | 190 ++++++++++
 tests/unit/test_subcommands_plugin.py              |  87 +++++
 10 files changed, 1398 insertions(+)
```

Compared against plan.md § Files that change (`plugin_install.py`,
`subcommands/plugin.py`, `cli.py`, `test_plugin_install.py`,
`test_subcommands_plugin.py`, `prove_plugin_install.py`, `tests/conftest.py`
— plus the three artifact files themselves): exact match, both directions.
No file touched outside the plan, no planned file left untouched.

## Findings

Three passes run: Bugs, Security, Compliance. Four Important findings below —
one Bugs/Compliance (an unguarded `os.replace` breaking the closed-outcome
contract), one Security (an unvalidated install-path name), two Compliance
(a message-fidelity promise and a fetch-timing promise, both in spec.md's
Interface/Acceptance criteria, not fully honored) — plus one Nit.

### Important

- **[Bugs] — FIXED.** `install()`'s existing-target swap was asymmetric in
  what it guarded. `plugin_install.py:266-267` (`if replaced_existing:
  os.replace(target, backup)`) ran with no exception handling, while the very
  next line (`os.replace(tmp_clone, target)`) was guarded and mapped to
  `FetchFailed`. If moving the existing plugin aside failed — permission
  denied, the directory busy, a race where `target` changed between the
  `.exists()` check and the rename — the `OSError` propagated straight out of
  `install()` uncaught, with no broad exception handling anywhere in
  `cmd_plugin_install` or `main()` either, crashing the CLI with a raw
  traceback instead of returning `InstallOutcome`'s closed set. This directly
  contradicted spec.md § Design's own stated contract for `InstallOutcome`
  ("again a closed set, never a raised exception"). Hermes's own
  `_install_plugin_core` has the identical gap, but hermes's contract
  tolerates raising; sadana's does not, so porting the shape verbatim carried
  over a latent hermes bug into a codebase whose contract is the opposite.
  **Fix**: both `os.replace` calls now run inside one `try`, with a
  `moved_existing_aside` flag so the `except OSError` branch only attempts
  the backup-restore when the first move actually succeeded — a failure in
  the first move now returns `FetchFailed` with nothing to unwind, and a
  failure in the second still restores the backup exactly as before. Verified
  by re-running the full suite (472 passed) after the change; no new test was
  added for this exact failure path (would require injecting a real
  filesystem-level `OSError`, judged not worth a new fixture for this pass —
  noted here rather than silently skipped).

- **[Security]** Neither `register()` nor `install()` validates `name`.
  `install()` computes `target = plugins_root / name` (`plugin_install.py:238`)
  and later `os.replace`s a verified clone onto it (`plugin_install.py:269`,
  and the backup swap at `:267`/`:274`). `pathlib.Path` join semantics mean an
  absolute `name` discards `plugins_root` entirely and a `..`-bearing `name`
  escapes it after OS path resolution — confirmed interactively:
  `Path('/tmp/plugins') / '/etc/passwd'` evaluates to `Path('/etc/passwd')`,
  and `Path('/tmp/plugins') / '../../etc/passwd'` resolves outside
  `plugins_root` once the OS normalizes it. `sadana plugin install '/etc/cron.d'
  v1.0.0` (given a matching registry entry, or even just a mistyped name a
  local operator fat-fingers) would attempt to `os.replace` a verified clone
  directly onto an arbitrary path the process can write to — no code path
  rejects this before the destructive rename. Intent.md's single-operator
  trust model narrows who can trigger it, but does not make the write itself
  safe: this is exactly the "widens what the process can reach" shape the
  compliance/security pass exists to catch, and it is cheap to close (reject
  `name` containing `/` or `..`, or resolve and `is_relative_to(plugins_root)`
  before any rename).

- **[Compliance]** `FetchFailed`'s promised message fidelity is not honored
  on the `ls-remote` path. spec.md § Interface: "`FetchFailed` carries
  whatever `git`'s own stderr said ... surfaced, never swallowed, never
  re-interpreted into a guess about which of those it was." `_resolve_tag_commit`
  (`plugin_install.py:195-212`) catches `_GitError` and returns bare `None`
  regardless of cause (unreachable host, unknown repo, no such tag — its own
  docstring says as much: "all the same 'can't install this' outcome from the
  caller's side"), discarding git's actual stderr. `install()` then manufactures
  a guessed message at `plugin_install.py:244-245`
  (`f"{tag!r} is not a tag on {repo_url!r}"`) regardless of whether the real
  cause was a typo'd URL, a DNS failure, or a genuinely missing tag. A user
  who mistypes a repository host gets "'v1.0.0' is not a tag on
  'https://typo'" instead of git's own "could not resolve host" — precisely
  the re-interpreted guess the Interface section rules out. The direct
  `_git()` failure path (`install()`'s outer `except _GitError as exc: return
  FetchFailed(detail=str(exc))`, `plugin_install.py:278-279`) does preserve
  git's stderr correctly; only the `_resolve_tag_commit` indirection loses it.

- **[Compliance]** spec.md's own promises about when network/subprocess
  activity is allowed to start are not fully met, and the gap is untested.
  Requirement 3: "A branch name or a bare commit is rejected before anything
  is fetched." Acceptance criteria: "A tag argument that is not a valid tag
  name is refused before any subprocess runs." In the actual (and plan.md-
  endorsed) order: (a) a syntactically valid tag string that happens to be
  malformed in a way only `git check-ref-format` catches still runs a
  subprocess (`_is_valid_tag_syntax`, `plugin_install.py:189`) before failing
  — only the bare-40-hex-char-SHA case is caught by regex with zero
  subprocess calls; (b) a real branch name (syntactically valid, present on
  the remote only under `refs/heads/`, not `refs/tags/`) is only rejected
  after a live `git ls-remote` network round trip
  (`_resolve_tag_commit`, `plugin_install.py:202`) — a network fetch, not
  "before anything is fetched." Neither case is exercised by a test:
  `test_install_rejects_a_non_tag_before_resolving_anything`
  (`tests/unit/test_plugin_install.py:165-176`) checks only the resulting
  `FetchFailed` outcome, never that zero subprocesses ran, and no test at all
  installs against a tag that is a real branch name on the fixture repo. This
  matches plan.md § Proof's own claim ("an invalid tag string → `FetchFailed`
  before any subprocess runs") to the same degree it's actually verified —
  which is not fully.

### Nits

- `test_install_twice_without_replace_is_refused`
  (`tests/unit/test_plugin_install.py:93-107`) doesn't assert the literal
  wording of its own acceptance criterion ("nothing on disk changes") — e.g.
  content/mtime of the first install's directory unchanged after the second
  call. Structurally guaranteed today (the `AlreadyInstalled` return at
  `plugin_install.py:239-240` happens before any network or disk write), so
  this is a documentation-of-intent gap in the test, not a live bug.

## Findings not raised

- The registry-as-discovery boundary tension against `plugin_blueprint.md`
  §9 ("Installation... This block assumes plugins are already there") is
  real and spec.md's own § Concerns names it directly, citing intent.md's
  "Changed during planning" as the recorded, deliberate approval to expand
  scope, with the narrowing (single-owner writes, a DB constraint, no
  network-fetched shared index, no role system) that was traded for it. I
  read `plugin_blueprint.md` §9 in full and confirm this item does what
  intent.md says it does, no more: it never touches `plugins.py`,
  `plugin_manifest.py`, or `plugin_dispatch.py`, and it introduces no
  submission path or cross-machine sharing. Not re-litigated here, per
  spec.md's own request that reviewers point back to intent.md rather than
  re-open the question independently.
- Design principles (learn-from-reference, reduce bets, more-plugins-not-
  more-core, cheapest-step-cost, minimal mutable state): checked against
  spec.md § Design and plan.md § Rejected-alternatives drift check. The
  atomic-clone-then-`os.replace()` pattern and the existing-target backup
  swap are adopted from `../hermes-agent/hermes_cli/plugins_cmd.py:855-871`
  with an audited, stated reason (OS constraint, not policy); the four
  declined alternatives (SHA pinning, pre-placement scan, cached shared
  index, pinned-revision metadata file) are absent from the diff, matching
  spec.md § Rejected alternatives with no drift back toward any of them. The
  "installed" and "currently installed tag" state are both derived, never
  stored, matching plan.md's state inventory. No finding.
- Every plan.md § Proof item beyond the one flagged above is discharged:
  registry register/resolve hit-miss and re-register→`NameTaken` are covered
  by `test_register_then_resolve_finds_the_url`,
  `test_register_twice_returns_name_taken_and_keeps_the_first_url`, and
  `test_resolve_unknown_name_returns_none`; the install happy path, replace,
  `AlreadyInstalled`, `UnknownPluginName`, `NameMismatch`, and the
  monkeypatched `TagMismatch` seam are each covered by name in
  `tests/unit/test_plugin_install.py`; `discover_plugins()` picking up the
  freshly installed fixture with no code change on its side is covered by
  `test_discover_plugins_finds_a_freshly_installed_plugin_with_no_code_change`;
  `make verify` ends `VERIFY OK` (pasted above, run fresh in this session).

## Decision

Approved by Adam, 2026-09-10, with the Important Bugs finding (the
unguarded `os.replace` in `install()`'s existing-target swap) fixed in this
branch before merge, as recorded above.

The Security finding (`name` not validated before joining into
`plugins_root`) and the two Compliance findings (`FetchFailed` message
fidelity on the `ls-remote` path; the tag-syntax/branch-name checks not
fully preceding all subprocess/network activity) were explicitly left
unaddressed in this pass, by direct instruction — not fixed, and not
silently dropped. They remain valid findings against the merged code.
