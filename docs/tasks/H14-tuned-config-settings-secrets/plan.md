# Plan: Tuned — config, settings and secrets by reference (from intent.md 2026-09-15)

Author: Adam Aubry (project owner). Status: approved.

## Context

`intent.md` and `spec.md` for this work item are both approved. The
problem: nothing outside this box can change its behavior without shell
access and a restart, and there is no channel to hand it a credential at
all — only hand-editing `state_dir/.env`. `spec.md` already made every
design decision (cache-by-mtime-and-size, the identity-shim-table pattern
for four new door nouns backed by flat files rather than natural rows, the
`read_setting(..., secret: bool)` signature change, the exact moved-keys
table). This plan sequences the implementation against real line numbers,
checked against the current source — it does not re-derive the design.

Reference-corpus check for this stage: `spec.md`'s own Design section
already did the CONFIG-block reading (`hermes_cli/config.py`'s
`(mtime_ns, size)` cache, adopted; `env_loader.py`'s external-secret-source
layer and `config_migrations.py`/`profiles.py`, declined by name). Nothing
new to re-read here — this plan implements what was already decided.

One correction against `spec.md`, found while pinning down exact call
sites: `spec.md`'s Design frames the four new nouns' own-table pattern as
new, but `door/nouns/inspections.py` already *is* the first `door/nouns/
*.py` module to own its own table (H24) — its own docstring says so
verbatim. This plan's four new nouns are the second through fifth, not the
first; `inspections.py` is the concrete template to copy the shape from
(`_SCHEMA`, `ensure_schema(conn)`, `write_txn` + `ids.make_id` +
`ledger.record_change` inline in the noun module itself), not a novel
pattern this item invents. This doesn't change any decision in `spec.md`,
only which file is the precedent.

## Files that change

**New, small (pure or I/O per CLAUDE.md's split):**
- `src/sadana/redact.py` (new) — `SecretRedactor(logging.Filter)`.
- `src/sadana/door/config_writer.py` (new, I/O) — `apply`/`write`, reusing
  `plugins.toml_string`.
- `tests/unit/test_redact.py`, `tests/unit/test_config_writer.py` (new).

**`config.py` / `env_file.py` (both already exist):**
- `src/sadana/config.py`: add `get`, `secret`, `bind_secret_reader`; change <!-- # pragma: allowlist secret -->
  `load_dotenv()` to stop writing `os.environ`.
- `src/sadana/env_file.py`: add `read_key`, `fingerprint`.
- `tests/unit/test_config.py`, `tests/unit/test_env_file.py` (already
  exist — extend, do not replace).

**Four new door nouns, each owning its own table (copy `door/nouns/
inspections.py`'s shape: `_SCHEMA`, `ensure_schema(conn)`, `write_txn` +
`ids.make_id` + `ledger.record_change` inline):**
- `src/sadana/door/nouns/providers.py` (new)
- `src/sadana/door/nouns/budgets.py` (new)
- `src/sadana/door/nouns/integrations.py` (new)
- `src/sadana/door/nouns/secrets.py` (new)
- `tests/contract/nouns/test_providers.py` (new)
- `tests/contract/nouns/test_budgets.py` (new)
- `tests/contract/nouns/test_integrations.py` (new)
- `tests/contract/nouns/test_secrets.py` (new)

(all four tests follow `test_plugin_noun.py`'s `_door`/`_token` scaffold,
not `_scaffold.py`, which is scoped to H24's own tests per its own
docstring).

**Wiring these four in:**
- `src/sadana/stores.py`: add each noun's `ensure_schema(conn)` call to
  `ensure_schemas()` (next to `door_nouns_inspections.ensure_schema(conn)`,
  line 65).
- `src/sadana/ledger.py`: add four `_NOUNS` entries (`providers`/`prv`,
  `budgets`/`bdg`, `integrations`/`intg`, `secrets`/`sec`) — `ids.PREFIXES`
  already carries all four prefixes (checked: `ids.py` lines 55-58 — no
  change needed there).
- `src/sadana/door/capabilities.py`: move `settings.write`/`secrets.write`
  from `ALL`-only into `DECLARED` (append-only, per that file's own rule).
- `src/sadana/subcommands/door.py`: add the four nouns to the `nouns={...}`
  dict (~line 123) and their imports.

**Call-site migrations** (`spec.md`'s moved-keys table, verified against
current line numbers this session):
- `src/sadana/client_surface.py:173-174` → `config.get("model_access.
  provider"/"model_access.model", ...)`.
- `src/sadana/model_providers/openrouter/provider.py:43,53` and the
  `BASE_URL` constant (line 30) → `config.secret(credential_ref)`,
  `config.get("model_access.timeout_s", 30)`, `config.get("providers.
  openrouter.base_url", "https://openrouter.ai/api/v1")`, `config.get
  ("providers.openrouter.credential_ref", "OPENROUTER_API_KEY")`.
- `src/sadana/model_access.py:289` → `config.get("model_access.
  max_retries", 3)`.
- `src/sadana/conversation.py:300,335,1336` → `config.get("conversation.
  iteration_max", 60)`, `.wall_clock_s`, `.child_max_depth`.
- `src/sadana/subcommands/gateway.py:61-63,88` → `config.get("gateway.
  webhook_bind"/".webhook_port"/".scheduler_interval_s", ...)`,
  `config.secret("gateway_webhook_secret")`.
- `src/sadana/subcommands/marketplace.py:158` → `config.secret
  ("marketplace_webhook_secret")` (the brief's own misattribution to
  gateway.py, already corrected in `spec.md`).
- `src/sadana/conversation_store.py:1408` → `config.get("approvals.
  ttl_s", 86400)`.
- `src/sadana/door/operations.py:170` → `config.get("door.workers", 8)`.
- `src/sadana/door/nouns/harness.py` (`get_harness`, ~line 39-56): add
  `"mirror": config.get("tether.mirror", "full")` as a new sibling key —
  `leaves_the_box`/`tether` untouched.

**`plugins.py` signature change and its four callers:**
- `src/sadana/plugins.py`: promote `_toml_string` → `toml_string`; <!-- # pragma: allowlist secret -->
  `read_setting`/`required_setting` gain `*, secret: bool`; internal branch <!-- # pragma: allowlist secret -->
  to `config.secret`/`config.get`; `missing_settings` passes each <!-- # pragma: allowlist secret -->
  setting's own `.secret` through.
- `src/sadana/subcommands/plugin.py:85` → pass `setting.secret`.
- `src/sadana/builtin_plugins/image-gen/draw.py` → add `secret=True`
  (verified: its `plugin.toml` already declares `secret = true` for the
  one setting called).
- `src/sadana/builtin_plugins/web-search/search.py` → add `secret=True`
  (same verification).
- `src/sadana/builtin_plugins/browse/browse.py` → add `secret=True` (same
  verification).
- `src/sadana/door/nouns/plugins.py`: implement the `set-settings` branch
  (~line 325, currently `unavailable()`), replacing the placeholder
  comment; validate `{settings: {key: value | {secret_ref: name}}}`
  against the plugin's declared settings.

**Test-only wiring:**
- `tests/conftest.py`: one line, `config.bind_secret_reader(env_file.
  read_key)`, next to the existing `_isolated_state` autouse fixture
  (~line 43).

**Docs:**
- `docs/console/nouns.md`: fill the four stub sections (`provider`,
  `budget`, `integration`, `secret`) per `spec.md`'s field lists.
- `docs/console/wire.md` §6: one paragraph on where a credential's value
  lives.
- `tests/contract/test_console_grammar.py`: add the whole-conformance-run
  secret-leak regex check (a fixture wraps every `handle()` call the
  module makes and asserts a planted secret value never appears in any
  response body).

**Existing tests that needed updating, not just extending — missed on the
first pass of this section — added during implementation, named here per
CLAUDE.md's own rule on a path added to this section after approval.**
Each asserted a behavior this item's own approved spec.md deliberately
changes (`load_dotenv` no longer populating `os.environ`, `read_setting`'s
new required `secret` parameter, `settings.write` becoming declared); none
of the fixes weakened what the test actually proves, they update the
assertion to the new, correct contract:
- `tests/unit/test_plugins.py`
- `tests/unit/test_subcommands_plugin.py`
- `tests/unit/test_subcommands_setup.py`
- `tests/unit/test_cli.py`
- `tests/contract/nouns/test_plugin_noun.py`
- `tests/unit/test_model_access.py` — a background code-review pass
  (see below) found and fixed a real gap: `config.secret()` raises
  `RuntimeError` when no entrypoint has bound a reader, and
  `eval_harness.py` (CLAUDE.md's own sanctioned direct caller of
  `take_turn`) never does. Test added: the credential preflight must
  treat an unbound reader as "missing," not crash.
- `tests/unit/test_plugin_manifest.py` — two inline plugin-body fixtures
  called `read_setting('weather', 'api_key')` with the old two-argument
  form; both needed `secret=True` added, the same mechanical fix as every
  other pre-existing `read_setting` call site.

**Entrypoint wiring, missed on the first pass of this section — added
during implementation, named here per CLAUDE.md's own rule on a path
added to this section after approval:**
- `src/sadana/cli.py`: install `redact.SecretRedactor` on the root logger
  and call `config.bind_secret_reader(env_file.read_key)`, both in
  `main()`, before `config.load_dotenv()`.
- `src/sadana/client_surface.py`: the same two calls in `open_runtime()`,
  next to its own existing `config.load_dotenv()` call — a separate edit
  from the `client_surface.py:173-174` call-site migration already listed
  above, same file.
- `src/sadana/channel_webhook.py`: promote its private `_PATH` constant to
  `WEBHOOK_PATH` — `door/nouns/integrations.py`'s own `webhook_url`
  computation needs the real path, and duplicating the literal `"/webhook"`
  string a second time risked the two drifting apart.
- `docs/reference/console_fit_plan.md` §6: the same credential-value
  correction as `docs/console/wire.md` §6 below, kept in sync in the same
  commit per that section's own stated rule.
- `.claude/settings.json`: an unrelated, pre-existing dangling trailing
  comma (present before this work item started) made the file invalid
  JSON and failed `make lint`'s `check-json` hook; removed at the user's
  explicit direction, not part of this item's own design. A background
  review agent separately restored a `Stop` hook it found missing from
  this same file, unasked and outside this diff's own scope; the user's
  own explicit decision was to leave it removed — final state of this
  file is the comma fix only.
- `CLAUDE.md`: the identity-shim-table rule this work item's design stage
  proposed (spec.md's own "Candidate CLAUDE.md rule") and the user
  approved by running spec.md through `scripts/artifact.py approve` — the
  rule text is quoted verbatim in spec.md's own closing section. Missed
  from this section on the first pass; added here per the same
  after-approval-addition rule as the entries above.
- `src/sadana/client_surface.py` (`open_runtime`, a second, separate edit
  from the redact/`bind_secret_reader` wiring already listed above, same
  file): `providers.<name>.model` now feeds `Runtime.model`'s own
  resolution as a fallback under `model_access.model` — found missing
  while writing this item's own promised proof script (below); see
  `## Risks` for the full account of what this does and does not fix.
- `tests/unit/test_client_surface.py`: one new test for that fallback.
- `scripts/prove_h14_no_restart_reconfig.py` (new): the standalone script
  this plan's own Proof section names, proving a `providers.update`
  through the door takes effect on the very next turn of an already-open
  `client_surface.Runtime` — CLAUDE.md's rule to prove a block's first
  real external round trip outside `make test`.
- `tests/unit/test_subcommands_gateway.py`: one new test, added during
  the Deploy-stage cold review's own finding (below) that
  `webhook_secret_ref` was dead configuration — proves `cmd_gateway_run`
  actually follows it.

Not touched, confirmed by re-reading: `docs/console/grammar.json` (no
grammar change — four nouns fit the existing generic CRUD shape),
`src/sadana/model_access.py`'s `ProviderManifest`/`list_providers` (used
as-is, not modified — `request_fn is not None` is already computable from
the existing dataclass).

## Order of work

1. `env_file.py`: add `read_key`, `fingerprint`. Pure additions to an
   existing I/O module; narrow check: `test_env_file.py`. Landing first
   because `config.secret` depends on it.
2. `config.py`: add `get`, `secret`, `bind_secret_reader`; change <!-- # pragma: allowlist secret -->
   `load_dotenv()`. Narrow check: `test_config.py`. This is the riskiest
   step in the whole plan (see Risks) — landing it second, alone, with
   nothing downstream wired to it yet, is deliberate: if the environment-
   precedence or caching behavior is wrong, it fails in its own narrow
   test, not buried under four new door nouns' worth of changes.
3. `redact.py` (new) + wire into `cli.main()`/`client_surface.
   open_runtime()`, alongside `bind_secret_reader`. Narrow check:
   `test_redact.py`.
4. `tests/conftest.py`: add `bind_secret_reader` to the autouse fixture.
   Narrow check: run the full existing suite once here — this is the one
   step that touches every other test's environment, so it needs to be
   proven inert (all existing tests still pass unchanged) before anything
   downstream starts depending on it.
5. `plugins.py`: `toml_string` promotion, `read_setting`/
   `required_setting` signature change, its four call sites (three
   builtin plugin bodies + `subcommands/plugin.py`). Narrow check:
   `test_plugins.py` plus the three builtin plugins' own existing tests.
6. Call-site migrations (client_surface, openrouter/provider.py,
   model_access, conversation.py, gateway.py, marketplace.py,
   conversation_store.py, door/operations.py, harness.py's `mirror`
   field) — mechanical, one `config.get`/`config.secret` per site. Narrow
   check: each touched module's existing test file; these should not
   change behavior when nothing has written to `config.toml` yet
   (defaults match today's env-var defaults).
7. `door/config_writer.py` (new). Narrow check: `test_config_writer.py`
   (round-trip every supported type, refuse an unsupported one, refuse a
   read-back mismatch).
8. Four new door nouns (`providers.py`, `budgets.py`, `integrations.py`,
   `secrets.py`), `ledger.py`'s four `_NOUNS` entries, `stores.py`'s four
   `ensure_schema` calls, `capabilities.py`'s two `DECLARED` entries,
   `subcommands/door.py`'s registration. Landing after `config_writer.py`
   because every one of these nouns' `update` goes through it. Narrow
   check: the four new contract test files, run individually.
9. `plugin.set-settings` in `door/nouns/plugins.py`. Depends on step 8
   (needs `secrets.py`'s existence-check for `secret_ref` validation) and
   step 5 (the `secret: bool` signature). Narrow check: extend
   `test_plugin_noun.py`.
10. Docs: `nouns.md`'s four sections, `wire.md` §6.
11. The whole-conformance-run secret-leak regex in
    `test_console_grammar.py` — landing last on purpose: it is the one
    check that exercises every noun above at once, so it should only be
    written once there is something real for it to catch.
12. `make verify`.

## Risks

What could this break that already works? `load_dotenv()`'s behavior
change (step 2) is the one edit touching something every existing test
already depends on indirectly: any test that currently relies on `.env`
values landing in `os.environ` (there should be none in `src/` per
`spec.md`'s grep, but plugin-setting tests and `test_subcommands_plugin.py`
may assume the old copy-into-environ behavior for non-secret settings
written via `sadana plugin set`). Mitigation: step 4 runs the entire
existing suite right after wiring the test fixture's `bind_secret_reader`,
before any downstream code depends on the new read path — a regression
here is caught before it can hide behind sixteen other changes.

Which step is riskiest? Step 2 (`config.py`). It is small in line count and
large in blast radius: every later step in this plan reads through it, and
a subtle bug in the environment-vs-file precedence or the mtime/size cache
key would silently pass every downstream test that happens to always set
the environment variable (which `testing-conventions`' "blanked credential
environment" makes likely in CI) while still being wrong for a real
deployment that relies on the file. Mitigation already in the ordering: it
lands second, alone, with its own narrow test covering the four
acceptance-criteria cases from `spec.md` (file→default, environment wins,
rewritten-file-seen-next-call, missing key) before anything else in the
codebase calls it.

Is this plan drifting toward anything `spec.md` rejected? Checked against
`spec.md`'s own `## Rejected alternatives`: no file-watcher/reload-endpoint
is introduced (guideline 3 — every read stays pull-based through
`config.get`/`.secret`); no external-secret-source layer, per-profile
isolation, or config-migration engine from the reference corpus
(guideline 1); the four new nouns get identity tables, not a re-derivation
of "maybe they don't need one" (guideline 4's resolved tension, restated in
`spec.md`'s Concerns) — this plan does not reopen that. One thing to watch
during implementation, not a rejection: `providers.py`'s config keys must
land under `[providers.<name>]`, never folded into the flat `[model_access]`
table — that distinction is `spec.md`'s own guideline-2 argument (a second
provider must be an addition, not a modification), and it would be easy for
build-time convenience to blur the two tables back together.

**Post-implementation: what the self-check actually found.** Build-skill's
own closing step (`/ponytail-review` + `/simplify`) found two real
simplifications (applied: `redact.py`'s hand-listed `LogRecord` attribute
set replaced with one derived from `logging.makeLogRecord({})`; the
`budgets.py`/`integrations.py` singleton-row duplication noted but left
for a future work item, since extracting it now was pure line-count
benefit with no removed bet, state, or common-path step — build-skill's own
stated bar for taking a finding immediately).

A `/code-review --fix medium` pass, launched by mistake in place of those
two commands, ran anyway and found eight further issues, all real, all now
fixed and covered by a new regression test each:

1. `providers.update`/`budgets.update`/`integrations.update` and
   `secrets.create`/`update`/`remove` never checked `settings.write`/
   `secrets.write` at all — `router.py` only auto-gates *actions* by
   capability (requirement 25's own dispatch), never the generic verbs;
   `schedules.py`'s own `_require_write` helper already carries this exact
   pattern for exactly this reason, and none of these four new nouns had
   copied it. Declaring the capability in `capabilities.DECLARED` alone did
   nothing to enforce it.
2. `secrets.create`'s `name` reached `env_file.upsert_key` unvalidated — a
   newline in it injects a second, arbitrary `.env` assignment the caller
   never named as a secret. Fixed with an allowlist
   (`^[A-Za-z_][A-Za-z0-9_]*$`), checked before the name ever touches the
   file — CLAUDE.md's own rule on a caller-supplied name becoming a
   filesystem/config value.
3. `budgets`/`integrations`' `isinstance(x, int)` field validation accepted
   `bool` (a subclass of `int` in Python) — `{"iterations_max": true}`
   would have passed validation and written a boolean into `config.toml`.
4. `model_access.send()`'s credential preflight called `config.secret()`
   directly; it raises `RuntimeError` when nothing has bound a reader,
   which is exactly `eval_harness.py`'s own situation (CLAUDE.md's
   sanctioned direct `take_turn` caller, with no client, no store, no
   binding). Now caught and treated as "missing," restoring the
   zero-network, never-raising contract this preflight already promised.
5. `redact.py`'s `_SENSITIVE_NAME` was a bare substring match, contradicting
   its own docstring's claim that `keyboard` does not match `key` — it did.
   Rewritten to split the attribute name into words and match a whole word.
6. `env_file.path_put`'s `0600` mode was only honored by `os.open` when it
   *creates* the file — a pre-existing `.env` at a wider mode kept that
   mode on every subsequent write. Fixed with an unconditional `os.fchmod`
   after every write.
7. `budgets.py`/`integrations.py` had each hand-rolled the `If-Match`
   precondition check inline instead of calling the shared
   `door.nouns.check_if_match` `providers.py` already uses — replaced.
8. `.claude/settings.json`'s `Stop` hook (`stop-verify.sh`) had been
   removed entirely by whatever left the file's JSON broken before this
   session started; this session's own fix (at the user's explicit
   direction) was scoped to the dangling comma alone and did not restore
   it. A review agent restored it on its own initiative, unasked, editing
   a file outside this diff's own scope — flagged to the user rather than
   accepted as part of this work item. The user's own decision, given
   directly: leave it removed. Confirmed neither CLAUDE.md nor any skill
   references the hook, so nothing else needed changing.

All eight are now covered by a dedicated test (contract tests for the four
capability gates and the two `bool`-vs-`int` cases; unit tests for the
name-injection refusal, the unbound-reader preflight, the `fchmod`
narrowing, and the word-boundary redaction) — `make verify` still ends
`VERIFY OK` at 1624 passing tests (was 1613 before this pass).

**A fresh, cold Deploy-stage review (a separate agent, no memory of the
build) found four further Important findings; three are documentation
gaps, fixed in this same pass, and one was a real, live bug:**

9. `secrets.remove`'s own design writeup, in `spec.md`, said the row is
   tombstoned (`state = "removed"`) rather than hard-deleted — the exact
   opposite of what item 2's own correction (above) already shipped, and a
   misreading of H26's own finding on `memory_entries.remove` (that finding
   was about an undeclared *capability*, not a missing ledger tombstone).
   `spec.md` and this plan's own Proof section are corrected to say what
   is actually implemented and why, rather than the code being changed to
   match a spec sentence that was wrong from the design stage.
10. **A real bug, not a paperwork gap: `integrations.update`'s
    `webhook_secret_ref` was write-only in the literal sense — validated,
    persisted, rendered back — but `subcommands/gateway.py`'s
    `cmd_gateway_run` never read it, always resolving the webhook secret
    from the fixed legacy env-var name regardless of what the door had
    been told to use.** Fixed: `cmd_gateway_run` now resolves
    `gateway.webhook_secret_ref` (default: the legacy name, so an
    unconfigured box is unchanged) and follows *that* reference through
    `config.secret`, exactly matching `providers.credential_ref`'s own
    already-correct pattern. `integrations.py`'s own rendered default for
    the field is corrected to match (it said `""`, implying nothing was
    configured, when the box was in fact always resolving the legacy
    name). New test: `test_subcommands_gateway.py` proves `cmd_gateway_run`
    resolves a *different*, explicitly-configured secret name, not just
    the unconfigured default.
11. The CLAUDE.md rule this work item's own design stage added (the
    identity-shim-table rule) had no entry in this section — added below.
12. This plan's own Proof section named a standalone script proving live,
    no-restart reconfiguration of an already-open runtime as Deploy-stage
    evidence; none had been written yet. Writing it honestly surfaced
    finding 13 below — the script now proves the claim that is actually
    true. Added as `scripts/prove_h14_no_restart_reconfig.py`, run, and its
    output pasted into this work item's Deploy-stage evidence.
13. **Writing that script found a third dead-configuration bug, the same
    class as finding 10, and a real architectural limit underneath it.**
    `providers.update`'s own `model` field was accepted, validated, and
    rendered back — but nothing read `providers.<name>.model` anywhere;
    `client_surface.open_runtime()` resolved the active model purely from
    `model_access.model`. Fixed: `open_runtime()` now falls back to the
    active provider's own `providers.<name>.model` beneath
    `model_access.model`'s box-wide override, the same layering
    `credential_ref`/`base_url` already use. But that fix only reaches the
    *next* `open_runtime()` call — `client_surface.Runtime.provider`/
    `.model` are resolved once, at open time, and held for the process's
    life, by `CLIENT-SURFACE-01`'s own already-settled design (its own
    docstring: "what a client holds for the life of its process"),
    unchanged and not reopened by this work item. `credential_ref`/
    `base_url` have no such gap — `_build_request`/`chat_completions_url`
    re-read both fresh on every call, so they reach an already-open
    runtime's very next call, which is what P9's own "applied without a
    restart" promise most concretely means for a credential. `model`
    reaching only the next process start is a real, disclosed limitation,
    most visible on `cmd_gateway_run`'s own daemon, which opens exactly
    one `Runtime` for its whole lifetime and answers every request through
    it — a provider's preferred model set through the door there does not
    take effect until that daemon is restarted. `spec.md`'s own Acceptance
    criteria and this plan's Proof section are corrected to claim exactly
    this, not more.

## Proof

- `test_config.py` covers: file value returned; default on missing
  file/key; a real environment variable wins over the file; a value
  written after the process starts is seen on the very next call (no
  restart); `secret()` returns a real env var without touching disk;
  `secret()` falls back to a freshly-read `.env` when unset; calling
  `secret()` before `bind_secret_reader` raises.
- `test_env_file.py` covers: `read_key` on a present/absent/blank key;
  `fingerprint` is stable for a stable value and the raw value does not
  appear in its output (a literal substring check).
- `test_redact.py` covers: an `sk-...` token, a JWT-shaped string, and a
  name-matched `extra=` field, each replaced; an unrelated log line
  untouched.
- `test_config_writer.py` covers: a string/int/float/bool/one-level-table
  round-trip through `apply`+`write`+`tomllib.load`; an unsupported type
  raises `ValueError` before touching disk; a forced read-back mismatch
  (monkeypatched emitter) raises without writing.
- Each new noun's contract test (`test_providers.py`, `test_budgets.py`,
  `test_integrations.py`, `test_secrets.py`) covers its own CRUD/action
  surface against `router.handle()` directly, including: `providers.update`
  with an unknown `credential_ref` → `400 VALIDATION`; a `secrets` create
  then `get` shows a fingerprint and never the value; `secrets.remove`
  hard-deletes the row and drops the `.env` line, the ledger's own
  `deleted` row carrying the tombstone (corrected against `spec.md`'s own
  first-draft wording — see `## Risks` below); a read-only field write
  (`runs_per_day`, `webhook_url`, `gateway_state`) → `400 VALIDATION`.
- `test_plugin_noun.py`'s extension covers: `set-settings` with a raw
  value against a `secret=True` setting → `400 VALIDATION` naming the key;
  `{secret_ref: name}` against an existing secret succeeds; an unknown
  settings key → `400 VALIDATION`.
- `test_console_grammar.py`'s new case: a secret value is planted through
  the door, the full existing conformance sequence runs, and a regex for
  that literal value is asserted absent from every response body captured.
- A short script (not `make test` — `testing-conventions`' network/real-
  behavior ban doesn't apply here, but a live turn needs a real running
  runtime, which the unit/contract suites deliberately stub) proves
  `providers.update` changing `credential_ref` or `base_url` takes effect
  on the very next call of an already-open `client_surface.Runtime`, with
  no restart — the corrected claim; see `## Risks` items 12-13 for why
  `model` specifically does not reach an already-open runtime the same
  way. Pasted as Deploy-stage evidence.
- `make verify` ends `VERIFY OK`.
