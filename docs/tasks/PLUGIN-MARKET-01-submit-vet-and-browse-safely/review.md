# Review: A plugin nobody can find is a plugin nobody uses (from plan.md 2026-09-10)

Reviewed: 1648681 (HEAD, `main`) — `git diff --staged` / `git diff HEAD` — 27 files, +2319/-121
Reviewer context: fresh session — no prior context beyond the diff and the three artifacts (intent.md, spec.md, plan.md). This session did not write any of the code under review; this satisfies the skill's "review cold" requirement, so no same-session caveat is needed.
Second opinion: none — build-skill's own self-check (`/ponytail-review` + `/simplify`) ran during build; not repeated here by design.

## Evidence

`make verify`, run fresh after the Security and Bugs findings below were
fixed (superseding the pre-fix run this review originally pasted here —
520 passed now, two more than the pre-fix 518: the git-argument-injection
regression test and the concurrent-claim regression test):

```
$ make verify
docs/tasks/PLUGIN-MARKET-01-submit-vet-and-browse-safely: all present artifacts valid
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
Success: no issues found in 32 source files
TYPES OK
▶ hermetic run: TZ=UTC LANG=C.UTF-8 PYTHONHASHSEED=0, empty env
........................................................................ [ 13%]
........................................................................ [ 27%]
........................................................................ [ 41%]
........................................................................ [ 55%]
........................................................................ [ 69%]
........................................................................ [ 83%]
........................................................................ [ 96%]
................                                                         [100%]
520 passed in 10.85s
TESTS OK
VERIFY OK
```

Per plan.md's own `## Proof` (last item) and CLAUDE.md's "prove a block's first real external round trip with a standalone script" rule, `scripts/prove_plugin_marketplace.py` was also run against a genuine public repository, live, by this reviewer:

```
$ python3 scripts/prove_plugin_marketplace.py
submit('https://github.com/psf/requests.git', 'v2.31.0') -> InvalidManifest(plugin_name=None, detail='plugin.toml does not parse: no plugin.toml at /tmp/sadana-marketplace-p1hcerxj/release/plugin.toml')  (8.66s)
OK for today's purposes: the real network round trip (resolve tag, clone, verify HEAD) completed successfully against a genuine remote, and the fetched repository's own code was never imported or executed — it only stops short of Pending because no real published repository yet has a plugin.toml in this project's own format. Pass a real plugin repository URL and tag as argv to prove the full path once one exists.
exit 0
```

## Findings

Six Important findings below (one Security, one Bugs, four Compliance) and one Nit, from a fresh-context cold review — no prior context beyond the diff and the three artifacts. The headline finding: `check_bodies=False` is not sufficient for "zero execution" — a crafted `repo_url` gets arbitrary command execution through `git` itself, independent of manifest validation, and I reproduced it live against this repo's own code below.

**Post-review**: the Security and Bugs findings (the two with a real code defect, as opposed to a documentation/coverage gap) were fixed by explicit instruction ("fix the severe things only before commit"); the four Compliance findings were left as-is, by the same instruction, and remain open — see each finding's own text for what's fixed and what isn't.

### Important

- **[Security] — FIXED.** `repo_url` is not sanitized before reaching `git` as a subprocess argument, and this is independently exploitable for arbitrary command execution — the "zero execution" premise this work item exists to guarantee is false regardless of `check_bodies`.
  `plugin_install._resolve_tag_commit()` (src/sadana/plugin_install.py:209-226) calls
  `_git("ls-remote", repo_url, f"refs/tags/{tag}", ...)` and `fetch_verified_tag()`
  (src/sadana/plugin_install.py:242-259) calls
  `_git("clone", "--depth", "1", "--branch", tag, repo_url, str(dest))` — in both
  calls `repo_url` is a bare positional argument, never preceded by a `--`
  separator. `git` interprets a leading-`-` argument as an option, not a
  repository. I confirmed this is live-exploitable in this repo's own
  environment, reproducing with the exact function `marketplace.submit()`
  calls:
  ```
  $ python3 -c "
  import sys, tempfile
  from pathlib import Path
  sys.path.insert(0, 'src')
  from sadana import plugin_install
  with tempfile.TemporaryDirectory() as tmp:
      dest = Path(tmp) / 'release'
      outcome = plugin_install.fetch_verified_tag(
          '--upload-pack=touch /tmp/sadana_poc_marker2;', 'v1.0.0', dest)
      print('outcome:', outcome)
  "
  outcome: FetchFailed(detail="'v1.0.0' is not a tag on '--upload-pack=touch /tmp/sadana_poc_marker2;'")
  $ ls /tmp/sadana_poc_marker2
  /tmp/sadana_poc_marker2   # created by the injected command
  ```
  The call returns an innocuous-looking `FetchFailed` — nothing in the
  return value hints that a command already ran. `marketplace.submit()`
  (src/sadana/marketplace.py:197) passes a caller-supplied `repo_url`
  straight through with no validation, and neither
  `cmd_marketplace_submit` (src/sadana/subcommands/marketplace.py:63-65,
  reachable by "anyone" per intent.md's Affected users — Creators) nor
  `marketplace_webhook.parse_webhook_request()`
  (src/sadana/marketplace_webhook.py:49-71, which only checks `repo_url`
  is a non-empty string) rejects a leading-`-` value before it reaches
  `fetch_verified_tag()`. This means the entire `check_bodies=False`
  analysis in spec.md's Design section — the document's own "load-bearing
  part," per its Concerns section — is answering the wrong question: the
  fetch step itself, not `_check_body`, is where an untrusted submission
  gets code execution, and it happens before any manifest is even read.
  This is technically pre-existing code (`_git`/`_resolve_tag_commit` are
  unchanged, only extracted into `fetch_verified_tag()`), but
  PLUGIN-INSTALL-01's `install()` only ever received an operator-typed URL
  at a local CLI prompt; this work item is the one that hands the exact
  same call an anonymous, network-submitted string for the first time —
  the identical shift in trust that justified `check_bodies` itself
  (CLAUDE.md's newly-added rule this build session wrote: "any caller
  handling input from a source it doesn't already trust" must not let
  that input reach an execution path). The standard fix is a `--`
  separator before `repo_url` in both `_git()` calls.

  **Fix applied**: `--` now precedes `repo_url` in both
  `_resolve_tag_commit()`'s `ls-remote` call and `fetch_verified_tag()`'s
  `clone` call, forcing git to read every argument after it literally,
  never as an option. A regression test
  (`test_fetch_verified_tag_a_repo_url_starting_with_dash_is_never_read_as_a_git_option`,
  `tests/unit/test_plugin_install.py`) reproduces this review's exact PoC
  payload and asserts the sentinel command never runs — verified to fail
  without the fix and pass with it (the fix was temporarily reverted and
  the test re-run to confirm the failure, then restored). This also
  closes the same, lower-severity hole already sitting in merged
  PLUGIN-INSTALL-01, since `fetch_verified_tag()` is the one function
  both paths share.

- **[Bugs] — FIXED.** `marketplace.submit()`'s name-ownership check is not atomic with the insert it guards, so two concurrent submissions for the same brand-new plugin name from different repositories can both pass.
  `submit()` (src/sadana/marketplace.py:190-222) calls `_owning_repo_url()`
  (a plain `SELECT`, no transaction) and only wraps the following
  `_insert_release()` in `write_txn`. Between the `SELECT` and the
  `BEGIN IMMEDIATE` of the insert, a second `submit()` call — on a
  different `sqlite3.Connection`, which is exactly what happens for two
  concurrent requests to `marketplace_webhook`'s `ThreadingHTTPServer`,
  since `cmd_marketplace_serve_webhook`'s `on_release()`
  (src/sadana/subcommands/marketplace.py:163-166) opens a fresh connection
  per request — can read the same "nobody owns this name yet" state and
  also insert. Both rows land under one `plugin_name` with two different
  `repo_url`s, silently violating the "first claim wins" invariant
  spec.md's Design section states as the whole reason `NameOwnedByAnotherRepo`
  exists. No test exercises concurrent submissions, so this is unexercised
  in the diff. Given the marketplace webhook is explicitly the concurrent
  entry point this work item adds, this isn't a hypothetical multi-process
  scenario — it's two threads of the one process this work item stands up.

  **Fix applied**: the ownership check and the insert now run inside one
  `write_txn` (`marketplace._claim_release()`), so `BEGIN IMMEDIATE`'s own
  write lock — held at the SQLite database level, not per-connection —
  serializes two concurrent connections attempting to claim the same name.
  A concurrency regression test
  (`test_claim_release_is_atomic_under_two_concurrent_connections`,
  `tests/unit/test_marketplace.py`) uses two real threads with two real
  connections and a monkeypatched delay to force the actual interleaving a
  race needs, confirms the second connection genuinely blocks on the lock
  (not a hoped-for timing window), and asserts the loser gets
  `NameOwnedByAnotherRepo` deterministically. Verified against the
  pre-fix logic (temporarily restored, test failed non-deterministically;
  fix restored, test passes reliably).

- **[Compliance] `tests/conftest.py` is modified but is not named anywhere in plan.md's `## Files that change`.**
  The diff adds `import sqlite3`, an `open_store`/`store_path_from_config`
  import, and a new `open_conn()` fixture helper (tests/conftest.py:189-196)
  shared by `test_plugin_install.py` and `test_marketplace.py` (replacing
  each file's own private `_conn()` — a real, reasonable dedup, confirmed
  by diffing `test_plugin_install.py`, whose pre-existing test bodies and
  assertions are otherwise untouched). Plan.md's `## Files that change`
  lists every other test file this diff touches, including the two other
  self-check backfills (`subcommands/plugin.py`,
  `describe_fetch_failure()`'s coverage) called out explicitly — but never
  mentions `tests/conftest.py`. Per the skill's explicit rule, a file
  touched that the plan did not name is an Important finding regardless
  of how small or well-justified the change is.

- **[Compliance] `marketplace.latest_pending()` exists in the diff but is absent from both spec.md's `## Interface` and plan.md's `## Files that change` function list for `marketplace.py`.**
  `src/sadana/marketplace.py:303-312` defines `latest_pending()`, used by
  `cmd_marketplace_show --pending` (src/sadana/subcommands/marketplace.py:114)
  and tested in `tests/unit/test_marketplace.py:210,216`
  (`test_a_newer_pending_release_never_replaces_the_still_visible_approved_one`).
  spec.md's Interface section lists exactly five `marketplace.py` functions
  (`submit`, `decide`, `latest_approved`, `pending_releases`,
  `approved_plugin_names`) and plan.md's Files-that-change entry for
  `marketplace.py` repeats that same list verbatim. Unlike
  `describe_manifest_outcome()` and `describe_fetch_failure()` — both
  added during self-check and explicitly backfilled into plan.md's Files
  section with a note explaining why — `latest_pending()` was never
  backfilled anywhere, even though a reviewer's own `show --pending`
  acceptance criterion (spec.md § Acceptance criteria, item 5) needs
  exactly this function to exist.

- **[Compliance] Plan.md's own `## Proof` promises a named test for every `SubmitOutcome` variant, but `TagMismatch` is never exercised at the `marketplace.py` level.**
  Plan.md § Proof: "`marketplace.py`'s tests cover, by name, every
  `SubmitOutcome`/`DecideOutcome` variant spec.md's Acceptance criteria
  lists: `Pending`, `NameOwnedByAnotherRepo`, `AlreadySubmitted`,
  `TagMismatch`, `FetchFailed`, `InvalidManifest` ... and
  `Decided`/`UnknownRelease`/`ReasonRequired`/`AlreadyDecided`."
  `tests/unit/test_marketplace.py` has no test asserting `submit()` ever
  returns a `plugin_install.TagMismatch` — the only fetch-failure test,
  `test_submit_an_unknown_tag_is_tag_mismatch_shaped_fetch_failed`
  (line 102), asserts `isinstance(outcome, FetchFailed)`, not
  `TagMismatch`. `TagMismatch` is tested at the lower `fetch_verified_tag()`
  level (`test_plugin_install.py`) but never proven to actually surface
  through `marketplace.submit()`'s own `SubmitOutcome` union — a
  one-line pass-through (`marketplace.py:198-199`) that is very likely
  correct, but is exactly the kind of claim `VERIFY OK` cannot confirm on
  its own, per the skill's own compliance-pass framing.

- **[Compliance] Acceptance criterion "approving a newer release replaces it without touching the old row's own approved status" has no test where two releases are both actually approved.**
  spec.md § Acceptance criteria: "Approving a release makes it the one an
  ordinary browse returns; approving a newer release replaces it without
  touching the old row's own `approved` status." The closest test,
  `test_a_newer_pending_release_never_replaces_the_still_visible_approved_one`
  (test_marketplace.py:199-217), submits and approves `v1.0.0`, then
  submits `v1.1.0` but never approves it — it proves an approved release
  survives a newer *pending* one, not that approving the newer release
  leaves the older row's `status='approved'` untouched. `latest_approved()`'s
  `ORDER BY decided_at DESC LIMIT 1` (marketplace.py:295-299) makes this
  very likely correct, but the specific two-approved-rows scenario the
  criterion names is unexercised.

### Nits

- spec.md's Design section (`hermes_cli/webhook.py`'s HMAC-over-body
  citation) checks out against the reference file
  (`../hermes-agent/hermes_cli/webhook.py:297`, `X-Hub-Signature-256`) —
  no discrepancy, noted only because the task asked for it to be verified.

## What was checked and found clean

- **`check_bodies=False`'s own claim** (the spec's stated "load-bearing"
  concern): read `_check_schema()`, `_check_skill()`,
  `plugins._validate_skill_text()`, `plugins._parse_manifest()`,
  `plugins._first_unreachable()`, `plugins._first_cycle()` in full
  (src/sadana/plugin_manifest.py, src/sadana/plugins.py). None imports,
  execs, or otherwise runs anything from the plugin directory — every one
  is `tomllib`/`json`/pure string and graph walking. `plugins.py`'s own
  import list has no `importlib`, `subprocess`, or `exec`. The *only*
  execution risk inside `validate()` is `_check_body()` →
  `_load_body_module()`, correctly gated by the new `if check_bodies:`
  (plugin_manifest.py:194-200). This part of the design holds — the gap
  is the fetch step above it, not this function.
- **`install()`'s regression safety**: diffed `tests/unit/test_plugin_install.py`
  in full — every pre-existing test body and assertion is byte-identical;
  only a private `_conn()` helper moved to `conftest.py` and new tests
  were appended. `install()`'s own control flow
  (src/sadana/plugin_install.py:284-317) is a direct, behavior-preserving
  call to the extracted `fetch_verified_tag()`, still wrapped by the same
  `TemporaryDirectory`/`os.replace` placement logic it always had.
- **`gateway_daemon.run()`'s secret-unset check relocation**: confirmed it
  moved to `cmd_gateway_run` (src/sadana/subcommands/gateway.py:67-69,
  checked *before* the `make_server` closure is even built) and is tested
  by `test_cmd_gateway_run_refuses_to_start_when_secret_is_unset`
  (test_subcommands_gateway.py:27-48), which monkeypatches
  `gateway_daemon.run` to fail the test if reached — proving the guard
  fires first, matching plan.md's Risks section exactly. The parallel
  `SADANA_MARKETPLACE_WEBHOOK_SECRET` check
  (subcommands/marketplace.py:159-161) is symmetrically tested.
- **Two daemons, two lock files**: `gateway_daemon.run()`'s
  `lock_filename` parameter is exercised by
  `test_run_uses_the_given_lock_filename_not_a_hardcoded_one`
  (test_gateway_daemon.py), which holds `other.lock` and proves `run()`
  asked for `gateway.lock` isn't blocked by it. `cmd_gateway_run` passes
  `lock_filename="gateway.lock"`, `cmd_marketplace_serve_webhook` passes
  `"marketplace.lock"` — distinct literals, discharging plan.md's Proof
  item on this point.
- **`header_value()` extraction**: `channel_webhook.py` now imports it
  from `gateway.py` rather than keeping its own `_header()`; the old
  private function is gone, not duplicated. `test_channel_webhook.py` is
  untouched by this diff (not in `git status`) and `make verify`'s 518
  passing tests confirm it's still green.
- **Rejected alternatives, re-checked against the diff directly**: none of
  spec.md's eight rejected alternatives reappear — `validate()` is not
  reused unchanged for the marketplace path; there is one daemon-lifecycle
  module, not two; the marketplace webhook has its own `/marketplace/webhook`
  path and its own secret header, not `channel_webhook.py`'s; no git-host-
  specific payload parsing exists; no raw fetched files are persisted
  (`submit()`'s `TemporaryDirectory` is discarded at the end of the `with`
  block in every branch); there is no second table for name-ownership
  (`_owning_repo_url()` queries `marketplace_releases` directly); no
  reviewer identity is recorded anywhere in the schema; and
  `marketplace_webhook.py` reuses `channel_webhook.py`'s bare
  shared-secret-header scheme, not HMAC-over-body.
- **`plugin_blueprint.md` §9 tension**: confirmed the document still lists
  "the marketplace: upload, discovery, the release webhook" as explicitly
  out of scope (`docs/reference/plugin_blueprint.md:371`). intent.md's
  "Changed during planning" section records the 2-or-3-item split was
  recommended and declined by the user on the record; spec.md's Concerns
  section flags the tension again without re-arguing it. Handled the way
  both documents say it should be — not re-litigated here.
- **Five design principles**: reference corpus checked and a real
  alternative declined with reasons (principle 1); the daemon
  generalization and `header_value()` promotion both wait for a genuine
  second caller before generalizing rather than building it speculatively
  (principle 2 and 3, matching CLAUDE.md's registry-seam rule verbatim);
  `check_bodies` is the cheapest possible placement for the safety fix —
  one boolean gating one existing loop, not a new validation pass
  (principle 4); state is derived wherever spec.md's own "State inventory"
  table says it should be (owning repo, latest-approved) rather than
  cached as a flag (principle 5). No violations found independent of the
  two Important findings above.

## Decision

Approved by Adam, 2026-09-10, with the Important Security finding (git
argument injection via an unsanitized `repo_url`) and the Important Bugs
finding (the non-atomic name-ownership race) fixed in this branch before
merge, as recorded above.

The four Compliance findings (`tests/conftest.py` and
`marketplace.latest_pending()` both missing from plan.md/spec.md's file
and interface lists; `TagMismatch` never asserted through `submit()`'s own
return type; the two-real-approvals acceptance criterion untested) were
explicitly left unaddressed in this pass, by direct instruction ("fix the
severe things only") — not fixed, and not silently dropped. They remain
valid findings against the merged code.
