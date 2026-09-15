# Review: Tuned — config, settings and secrets by reference (from plan.md 2026-09-15)

Reviewed: HEAD..working tree (nothing committed yet for this work item) — 54
files changed, +4240/-166 (per `git diff --stat HEAD`; 3 of those files are
this work item's own `intent.md`/`spec.md`/`plan.md`).

Reviewer context: fresh session — spawned solely for this Deploy stage, with
no memory of writing this code; intent.md, spec.md, plan.md and the diff
were all read cold for this review.

Second opinion: none run in this stage by design — plan.md's own § Risks
records that build-skill's self-check (`/ponytail-review` + `/simplify`) and
a `/code-review --fix medium` pass already ran during Build and found eight
real issues, all fixed with regression tests before this stage started. This
review does not re-run those tools; it re-derives its own findings from the
diff and the three artifacts.

## Evidence

```
$ make verify
docs/tasks/H14-tuned-config-settings-secrets: all present artifacts valid
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
Success: no issues found in 98 source files
TYPES OK
▶ hermetic run: TZ=UTC LANG=C.UTF-8 PYTHONHASHSEED=0, empty env
........................................................................ [  4%]
........................................................................ [  8%]
.........................................
  two conversations, concurrently: 274 ms for two 200 ms turns
.  one conversation, twice: 443 ms for two 200 ms turns
.............................. [ 13%]
........................................................................ [ 17%]
........................................................................ [ 22%]
........................................................................ [ 26%]
........................................................................ [ 31%]
........................................................................ [ 35%]
........................................................................ [ 39%]
........................................................................ [ 44%]
........................................................................ [ 48%]
........................................................................ [ 53%]
........................................................................ [ 57%]
........................................................................ [ 62%]
........................................................................ [ 66%]
........................................................................ [ 70%]
........................................................................ [ 75%]
........................................................................ [ 79%]
........................................................................ [ 84%]
........................................................................ [ 88%]
........................................................................ [ 93%]
........................................................................ [ 97%]
........................................                                 [100%]
1624 passed in 135.88s (0:02:15)
TESTS OK
VERIFY OK
```

No prior claim of this output was trusted — it was run fresh, in this
session, before any finding below was written.

## Scope reconciliation (diff vs. plan.md § Files that change)

Every file plan.md names was found touched, and every file touched was
named in plan.md — with one exception: `CLAUDE.md` (+1 line) is in the diff
and not listed anywhere in plan.md's § Files that change, including its two
"missed on the first pass, added during implementation" sub-sections that
exist for exactly this kind of post-approval addition. See Important
finding below.

`docs/console/grammar.json` and `scripts/` are untouched, matching plan.md's
own "not touched, confirmed by re-reading" note and its Proof section's
claim about a script respectively (the latter is also a finding — see
below).

## Findings

**Important.**

- **[Compliance] `secrets.remove` hard-deletes instead of tombstoning,
  contradicting both spec.md's Design and plan.md's own Proof claim —
  FIXED (documentation corrected, not the code).** `spec.md`'s Design
  section said the row is tombstoned; the shipped code hard-deletes it
  (`src/sadana/door/nouns/secrets.py`'s `remove()`), matching
  `memory_store.delete_entry`'s own established hard-delete pattern and
  `docs/console/nouns.md`'s own `secret` section, which already stated the
  hard-delete choice with a rationale. `spec.md`'s Design text was wrong
  from the design stage — a misreading of H26's own finding on
  `memory_entries.remove` (that finding was about an undeclared
  *capability*, not a missing ledger tombstone) — and is now corrected to
  say what is actually implemented and why. `plan.md`'s § Proof and
  § Risks item 9 record the correction.

- **[Bugs] `integrations.update`'s `webhook_secret_ref` was write-and-
  validate only — nothing read it, so changing it through the door had no
  effect on the box's actual behavior — FIXED.** `src/sadana/door/nouns/
  integrations.py` validated `webhook_secret_ref` and wrote it to
  `gateway.webhook_secret_ref` in `config.toml`, but `cmd_gateway_run`
  (`src/sadana/subcommands/gateway.py`) hardcoded
  `config.secret("SADANA_GATEWAY_WEBHOOK_SECRET")`, ignoring the door's
  write. Fixed: `cmd_gateway_run` now resolves `gateway.webhook_secret_ref`
  (default: the legacy name, so an unconfigured box is unchanged) and
  follows *that* reference through `config.secret`, matching
  `providers.credential_ref`'s already-correct pattern; it also now
  refuses to start rather than silently running with an empty secret if
  the referenced name resolves to nothing. `integrations.py`'s own
  rendered default is corrected to match (it said `""`, implying nothing
  configured, when the box was in fact always resolving the legacy name).
  New test: `tests/unit/test_subcommands_gateway.py` proves
  `cmd_gateway_run` resolves a *different*, explicitly-configured secret
  name, not just the unconfigured default. `plan.md` § Risks item 10
  records this.

- **[Compliance] `CLAUDE.md` gains a new rule with no entry in plan.md's
  § Files that change — FIXED.** `plan.md` § Files that change now lists
  `CLAUDE.md` (the identity-shim-table rule this work item's design stage
  proposed, `spec.md`'s own § Candidate CLAUDE.md rule) as a file this
  item touches, per CLAUDE.md's own rule that a path added to that section
  after approval is disclosed rather than silently added. `plan.md`
  § Risks item 11 records the correction.

- **[Compliance] The plan.md § Proof item proving live, no-restart
  reconfiguration was undischarged — FIXED.** `plan.md`'s own § Proof
  committed to a standalone script and none existed. Writing it
  (`scripts/prove_h14_no_restart_reconfig.py`, new) surfaced a genuine
  third dead-configuration bug of the same class as finding 2 above:
  `providers.update`'s `model` field was accepted and rendered back but
  nothing ever read `providers.<name>.model` — `client_surface.
  open_runtime()` resolved the model purely from `model_access.model`.
  Fixed: `open_runtime()` now falls back to the active provider's own
  `providers.<name>.model` beneath `model_access.model`'s box-wide
  override, the same layering `credential_ref`/`base_url` already use.
  That fix only reaches the *next* `open_runtime()` call, though —
  `client_surface.Runtime.provider`/`.model` are resolved once, at open
  time, and held for the process's life, by CLIENT-SURFACE-01's own
  already-settled design ("what a client holds for the life of its
  process"), which this work item does not reopen. `credential_ref`/
  `base_url` have no such gap: `_build_request`/`chat_completions_url`
  re-read both fresh on every call, so those two reach an already-open
  runtime's very next call with no restart — the corrected, narrower claim
  the script actually proves, matching `spec.md`'s and `plan.md`'s own
  corrections (`plan.md` § Risks item 13). Script output:

  ```
  1) First turn, before any door reconfiguration
     url='https://openrouter.ai/api/v1/chat/completions' authorization='Bearer sk-original-value'
  2) Through the door: PUT a new secret, then PATCH the provider
     200 credential_ref='rotated_key' base_url='https://example.test/v1'
  3) Second turn, on the SAME already-open Runtime — no restart
     url='https://example.test/v1/chat/completions' authorization='Bearer sk-rotated-value'

  ALL STEPS PASSED — credential_ref/base_url reached an already-open runtime with no restart
  ```

  `model`'s narrower, process-restart-only reach is most visible on
  `cmd_gateway_run`'s own daemon, which opens exactly one `Runtime` for its
  whole lifetime — a provider's preferred model set through the door there
  does not take effect until that daemon restarts. `spec.md`'s Acceptance
  criteria and this plan's Proof section already state exactly this, not
  more.

**Nits.**

- **[Compliance]** `redact.py` implements spec.md's item 3 ("`record.args`,
  when it is a mapping ...") as name-matched `extra=` attributes on the
  `LogRecord` instead of inspecting `record.args` itself — a reasonable
  reading, since spec.md's own worked example already used `extra=`, and
  it's disclosed in the module's own docstring as a deliberate correction.
  But the literal case spec.md's own sentence names (a dict passed as the
  sole positional argument, making `record.args` itself a mapping, e.g.
  `logger.info("%(key)s", {"key": ..., "value": secret})`) is never
  redacted by name under the shipped code — only the two shape-based rules
  would catch a secret there, and no test exercises this shape.
- **[Compliance]** `config.get`'s shipped signature,
  `get(key: str, default: T, *, env_name: str | None = None) -> T`, adds a
  keyword-only parameter beyond spec.md's stated Interface
  (`config.get(key: str, default: T) -> T`). The addition is well-motivated
  (four call sites in `conversation.py`/`budgets.py`/`gateway.py` need it to
  keep an old env-var name whose word order doesn't match the derived-name
  convention) and consistently used, but spec.md's Interface section was
  never updated to show it.
- **[Compliance]** The moved-keys table names the gateway and marketplace
  webhook secrets `"gateway_webhook_secret"` / `"marketplace_webhook_secret"`
  as their new `config.secret()` names; the shipped code instead calls
  `config.secret()` with the literal old env-var names
  (`"SADANA_GATEWAY_WEBHOOK_SECRET"`, `"SADANA_MARKETPLACE_WEBHOOK_SECRET"`).
  For marketplace this is cosmetic (nothing else refers to it by the
  table's name). For gateway it is the same key the second Important
  finding above turns on — noted separately here only because it is also,
  independently, a moved-keys-table mismatch.
- **[Bugs]** `door/nouns/providers.py`'s `update()` calls
  `config_writer.write(path, data)` (a real disk write) before the matching
  `UPDATE providers ...` / `ledger.record_change` inside the same
  `write_txn`. If the SQLite side failed after a successful file write
  (e.g. a `record_change` invariant violation), `config.toml` would hold
  the new value while the noun's own `version`/`updated_at` would not
  advance to match, until the next successful write reconciles them. Same
  shape in `budgets.py` and `integrations.py`. Low impact — the value
  itself is never wrong, only the row's own metadata — but worth a
  one-line comment if this ordering is intentional.

## Decision

Approved by Adam, 2026-09-15, with all four Important findings fixed in
this branch: `secrets.remove` hard-delete documented and reconciled across
spec.md/plan.md; `integrations.update`'s `webhook_secret_ref` wired into
`cmd_gateway_run`; `CLAUDE.md`'s identity-shim-table rule added to plan.md
§ Files that change; and the standalone no-restart-reconfiguration proof
script written, run, and its output pasted above (which itself surfaced
and fixed a third dead-configuration bug, `providers.update`'s `model`
field, scoped to `client_surface.open_runtime()`'s fallback chain without
reopening CLIENT-SURFACE-01's frozen-Runtime-fields design). The four Nits
are left open, as preferences.
