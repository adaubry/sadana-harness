# Spec: Tuned — config, settings and secrets by reference

Intent: docs/tasks/H14-tuned-config-settings-secrets/intent.md

Author: Adam Aubry (project owner). Status: approved.

## Requirements

1. A behavior value with a user-facing meaning is read through one function,
   `config.get(key, default)`, at the moment it is used — never cached
   beyond what freshness-checking requires — so a change through the door
   takes effect on the next turn. Traces to intent's Proposed outcome
   (no restart) and Constraint (environment always wins).
2. A secret's value is read through one function, `config.secret(name)`,
   which never returns anything the process's own real environment did not
   already contain unless it falls back to the on-disk credential file, and
   which no caller may use to enumerate what secrets exist. Traces to intent's
   Proposed outcome (the box can accept a credential) and Constraint (no read
   path returns a secret's value).
3. `config.py` gains these two functions without importing anything else
   from this package — the existing constraint printed in its own module
   docstring, which this work item must keep true. Traces to intent's
   Constraint (environment always wins is a property of a module nothing
   else can quietly change from underneath).
4. `load_dotenv()` stops copying `state_dir/.env` into `os.environ`. Traces to
   intent's Problem (today's copy-into-environ is the reason a secret's value
   ends up wherever the process environment ends up — a log dump, a child
   process's inherited environment, a stack trace) and to `testing-conventions`'
   "Isolate by process" instruction, which a global environment mutation
   already put at risk once (this module is exactly the kind of shared
   mutable state that instruction exists to keep off the table).
5. A credential can be written to the box through a channel that never
   echoes the value back — write, then only a fingerprint of what was
   written is ever visible again. Traces to intent's Proposed outcome
   verbatim.
6. A person or the console can distinguish a stale credential from a current
   one without reading the file. Traces to intent's Proposed outcome
   ("enough of a fingerprint to recognize it").
7. `provider`, `budget`, `integration` and `secret` become real door nouns,
   reachable through the same six-verb, four-list-param, Problem-Details
   grammar every other noun already answers (P2), so the console's
   settings-and-credentials surface (artifact A20) and plugins surface
   (artifact A23) can be built against them. Traces to intent's Affected
   users and systems.
8. `plugin.set-settings` becomes reachable: a secret-kind plugin setting
   accepts only a reference to an already-stored secret, never a raw value,
   and a non-secret setting is written to the behavior config file under
   that plugin's own namespace. Traces to intent's Proposed outcome and to
   `plugins.Setting`'s own docstring, which named this exact migration as
   owed.
9. Nothing the box logs or answers over the door ever contains a secret's
   value, checked mechanically rather than by review alone. Traces to
   intent's Constraint (no read path — not one API response, not one log
   line).
10. Every command-line flag that already sets one of the values this item
    moves keeps setting it, unchanged. Traces to intent's Constraint
    verbatim.
11. Every environment variable name this item moves off of keeps working —
    "the environment always wins" is a promise about precedence, not a
    promise that the old names still route anywhere once the box no longer
    has an environment-only story. Traces to intent's Constraint.

## Design

**What the reference corpus showed, and what this declines.**
CONFIG-block production code was read: hermes's `hermes_cli/config.py`,
`hermes_cli/env_loader.py`, and (by file size and by cross-reference from
`env_loader.py`) `hermes_cli/config_migrations.py` and `hermes_cli/profiles.py`
were located via `docs/reference/hermes_core_blocks_kind.csv` and
`kind == production-code`.

**Adopted.** `hermes_cli/config.py` keys its parsed-config cache on
`(mtime_ns, size)` and returns a deep copy, never the cached object itself
(`read_raw_config`, `read_raw_config_readonly`). That is precisely
requirement 1's freshness mechanism, and precisely the shape
`config.get`'s docstring already promises ("cached by mtime and size"). The
copy-not-alias detail matters here too: `config.get`'s cache must never hand
back a mutable object a caller could mutate and corrupt for every later
reader in the same process.

**Declined, by name.** `env_loader.py`'s external-secret-source layer
(Bitwarden, 1Password, per-source provenance labels), its per-profile
`HERMES_HOME` isolation, and its ASCII-sanitization sweep over credential
env vars all solve problems this single-tenant, single-process box does not
have: multiple concurrent user profiles sharing one machine, and a
plaintext-vault integration this project has never been asked to build.
Adopting them would be exactly the "tightly coupled, hidden assumptions"
CLAUDE.md's own methodology warns about — hermes's secret-sourcing exists
because Hermes runs on a developer's own machine across many projects; this
box is one tenant behind one tether. `config_migrations.py` (a versioned
schema-migration engine for `config.yaml`) and `profiles.py` (2,523 lines of
per-profile config layering) are declined for the same reason plus
guideline 2: `config.toml` is new today, has no prior version to migrate
from, and this box has no second profile — building either now is
speculative infrastructure with no consumer.

**`config.py`.** Two additions, both satisfying the module's own existing constraint
("imports nothing from any other part of sadana-harness"):

```
get(key: str, default: T) -> T
```

`key` is a dotted path (`"model_access.timeout_s"`) read from
`config_dir/config.toml` as `[model_access]\ntimeout_s = ...`. Parsed once
per distinct `(mtime_ns, size)` of the file (hermes's proven cache key,
above), a missing file or a missing key both fall through to `default`
exactly like `env`/`env_int` do today, and `SADANA_<UPPER>_<UPPER>` (the
existing convention, unchanged) wins over whatever the file says, checked
after the file lookup so a cache hit never has to know about the
environment variable's name. The type of `default` decides how the raw
TOML/environment string is coerced, the same contract `env_bool`/`env_int`
already keep.

```
secret(name: str) -> str | None
bind_secret_reader(reader: Callable[[str], str | None]) -> None
```

`secret(name)` checks `os.environ.get(name)` first — since `load_dotenv`
no longer copies anything into `os.environ`, anything found there is a real
environment variable, so this check alone *is* "the environment always
wins" for secrets, with no bookkeeping needed to tell the two apart. Failing
that, it calls the bound reader with `name` and returns what it returns.
Calling `secret()` before anything has bound a reader raises — a caller that
reaches this before either real entrypoint (`cli.main`, `client_surface.
open_runtime`) has run is a startup-ordering bug worth hearing about, not a
value worth guessing at.

`bind_secret_reader` is the one indirection this module needs to stay
import-free: it stores a plain function reference, typed
`Callable[[str], str | None]` (`collections.abc`, stdlib — the module's own
"nothing from this package" promise is about `sadana.*` imports). Bound
exactly twice in production code (`cli.py:main()`, `client_surface.
open_runtime()`, matching `load_dotenv`'s own two call sites one for one)
and once in `tests/conftest.py`'s existing autouse fixture, right next to
where that fixture already redirects `XDG_CONFIG_HOME`/`SADANA_STATE_DIR` —
no new fixture, one added line to the one every test already depends on.

**`env_file.py`.** `read_key(name: str) -> str | None` — opens `env_path()`, scans for `name`
the same way `env_line_key` already does, returns the unquoted value or
`None`. Read fresh on every call, matching this module's existing posture
("touches the disk on every call").

`fingerprint(value: str) -> str` — last four characters of `value`, a
middle dot, first eight hex characters of `sha256(value.encode())`. Stable
for a stable value (a requirement in its own right — a person comparing two
`GET`s a day apart needs the same string back), and by construction never
reversible to the value.

**`load_dotenv()`'s behavior change.** Stops writing to `os.environ`. What it does instead: nothing that any
caller in this codebase currently depends on, once `config.secret` exists —
grep found exactly two call sites (`cli.py:51`, `client_surface.py:162`),
and both are about to sit right next to their matching
`bind_secret_reader()` call. The function keeps its name and its guard
(skip a missing file) so its two callers do not change shape, but its body
becomes a no-op check that the file parses, kept only so a malformed
`.env` still fails at startup the way it does today rather than silently at
first read. No "record what it loaded" bookkeeping is needed: nothing reads
`os.environ` afterward expecting `.env`'s contents to be there once
`config.secret` is the read path, so there is nothing to reconcile against
what an intermediate design might have recorded.

**`redact.py`** (new, pure). A `logging.Filter` subclass. Three substitutions, checked in this order
because the first two are shape-based and must not be shadowed by the
third's name-based one:

1. `sk-[A-Za-z0-9_-]{16,}` → `[REDACTED]`, wherever it appears in the
   formatted message.
2. A JWT shape (three base64url segments joined by `.`, first segment
   decoding to JSON with an `alg` key) → `[REDACTED]`.
3. `record.args`, when it is a mapping: any key matching
   `/secret|key|token|password|credential|authorization/i` has its value
   replaced with `[REDACTED]` before formatting — this is what catches a
   structured log call's own field (`logger.info("set %s", value,
   extra={"key": "OPENROUTER_API_KEY", "value": v})`-shaped calls; the field
   *name* decides, not the value's shape.

Installed on the root logger by `cli.main()` and `client_surface.
open_runtime()` — the same two entrypoints as `bind_secret_reader`, and for
the same reason: anything logging before either has run is early-bootstrap
code that cannot yet be holding a secret worth redacting.

**The four new door nouns, and the identity gap the brief did not name.**
The brief's response shapes (`{ name, kind, fingerprint, updated_at }` for
`secret`, similarly thin shapes for the other three) are illustrative, not
exhaustive — `docs/console/wire.md`'s own "Standard fields" rule is
non-negotiable: `id`, `created_at`, `updated_at`, `state`, `tags`,
`harness_id`, `version`, and `name` where the noun has one, on *every*
resource. None of `provider`, `budget`, `integration` or `secret` has a
natural SQLite row today — their values live in `config.toml`/`.env`, flat
files with no id, no version, no optimistic-concurrency token. Giving each
noun a real `id`/`version` requires a small persisted identity row
somewhere, or `If-Match` (P2) is fiction for these four nouns from day one.

This is resolved the same way `memory_policies` (H26) already resolved an
identical shape: a *synthetic* default row (`id = "<prefix>_default"`,
`version = 0`) rendered whenever nothing has ever been written, and a *real*
row — minted through `ids.make_id`, given a real version that increments —
the first time something is. The value itself never moves into that row;
the row is identity and concurrency-control metadata only, and the file
stays the one thing `config.get`/`config.secret` ever read for the value.

- **`providers`** (prefix `prv`): one row per name in
  `model_access.list_providers()` with `request_fn is not None` — "wired
  providers only" is exactly `ProviderManifest.request_fn is not None`,
  already computable, no new registry needed. A row materializes (`INSERT
  OR IGNORE` keyed on `name`, inside `write_txn`) the first time `list`/`get`
  is asked for it. `model`, `base_url`, `credential_ref` live under
  `[providers.<name>]` in `config.toml`, keyed by provider name rather than
  folded into the existing flat `[model_access]` table — a second registered
  provider (a `model_providers/anthropic/` directory, say) must not collide
  with the first's settings, and `[model_access]` stays what it is today:
  the box's *active selection* (`provider`, `model`, `timeout_s`,
  `max_retries`), which may name a provider whose own `[providers.<name>]`
  table also exists. `providers.update` on a `credential_ref` that names no
  stored secret is `400 VALIDATION`, checked by asking `config.secret` for a
  value under a reader that also reports existence-without-value (see
  `secrets` below — `secrets.exists(name)`, not `config.secret(name) is not
  None`, so this check cannot be tricked by a secret whose value happens to
  be an empty string).
- **`budgets`** (prefix `bdg`): one synthetic-then-real row, box-wide (there
  is exactly one active `[conversation]` budget, not one per account or per
  conversation). `iterations_max` ↔ `conversation.iteration_max`,
  `wall_clock_seconds` ↔ `conversation.wall_clock_s`. `runs_per_day` is
  never stored — always rendered `null`, and `400 VALIDATION` if a `PATCH`
  names it, the same treatment `providers`' read-only fields get.
- **`integrations`** (prefix `intg`): one synthetic-then-real row, box-wide.
  `webhook_url` and `gateway_state` are computed at read time from whether
  the gateway is actually bound (`gateway.webhook_bind`/`.webhook_port`
  config and the running daemon's own state) — never stored, so they cannot
  go stale relative to reality the way a cached value could.
  `webhook_secret_ref` ↔ a new `gateway.webhook_secret_ref` config key,
  validated against `secrets.exists` the same way `providers.
  credential_ref` is. `update`'s optional `webhook_bind`/`webhook_port`
  write `gateway.webhook_bind`/`.webhook_port`.
- **`secrets`** (prefix `sec`): one real row per name that has ever been
  `PUT`, minted on first write (no synthetic-default case — a secret that
  was never written does not exist, full stop, and `GET`/`DELETE` on one
  that doesn't is `404`). `remove` hard-deletes the row and deletes the
  `.env` line (`env_file.drop_key`) — corrected during implementation
  against this project's own real pattern for exactly this shape
  (`memory_store.delete_entry`): the tombstone P3 requires is the ledger's
  own permanent `deleted` `changes` row, written inside the same
  transaction as the delete, never a lingering `state` kept on the noun's
  own table. This document's own first draft had it backwards, citing
  H26's finding as asking for a state-based tombstone; H26's actual
  concern was that `memory_entries.remove`'s hard delete was a capability
  intent.md never asked for — a scope question, distinct from the
  ledger-tombstone mechanism, which `memory_entries.remove` already uses
  the same way `secrets.remove` now does. `fingerprint` is never
  stored: `list`/`get` compute it live from `env_file.read_key(name)` +
  `env_file.fingerprint(value)` on every call, so a value changed by
  hand-editing `.env` outside the door is reflected immediately, with no
  stale cached fingerprint possible by construction. `kind` is an opaque
  display label (default `"generic"`), persisted on the row, never
  validated against a closed set — nothing here branches on it.

All four identity tables are added the way every H16-era table was: `ids.
PREFIXES` gains `prv`/`bdg`/`intg`/`sec`, `ledger._NOUNS` gains the four
table names, and `record_change` validates the id-prefix-matches-noun
invariant it already validates for everything else.

**`door/config_writer.py`** (I/O, its own file — CLAUDE.md's I/O-vs-pure-function
split). Reuses `plugins._toml_string` (promoted to `plugins.toml_string`, dropping
the leading underscore — it is no longer private once a second module needs
the exact escaping it hand-rolled and hardened against two real bugs found
by a self-check; writing a second copy of that escaping logic is precisely
the bet guideline 2 says not to double up on) for every string value, and
adds its own emission for `int`/`float`/`bool` (`json.dumps` already spells
all three correctly for TOML) and one level of `[table]`/`[table.subtable]`
headers — `config.toml`'s shape is `dict[str, dict[str, scalar]]`, so a
dotted header like `[providers.openrouter]` is one string, not a nested
Python structure the emitter has to walk.

`apply(current: dict, key: str, value: object) -> dict | ValueError`
splits `key` on the last dot into `(table, field)`, checks `value`'s type is
one this emitter can express (`400` — "config_writer cannot express a value
of type X" — otherwise), and returns an updated copy. `write(path, data)`
emits, then — the `editor_server._write_manifest` round-trip guard, the
same idiom, not a new one — parses the emitted text back with `tomllib` and
compares it to `data` before touching disk, then writes through a
`path.with_name(f".{path.name}.tmp")` + `os.replace` pair, this codebase's
own existing atomic-rewrite idiom (`persona_store.update_character`,
`plugin_install.py`'s installs). Every successful `apply` is followed by one
`ledger.record_change` on the noun whose `update` triggered it — not a
change entry for "config.toml changed" in the abstract, which no console
noun could ever resolve back to a resource.

**`plugins.py`: `read_setting`/`required_setting` gain `secret: bool`.**
`read_setting(plugin, name)` cannot tell a `secret=True` setting from a
`secret=False` one today — both resolve identically through
`os.environ.get(setting_env_var(...))`, because until this item they were
stored identically. Now they are not: `secret=True` still resolves through
`setting_env_var`'s namespaced key, but via `config.secret(...)`; `secret=
False` resolves through `config.get(f"plugins.{plugin}.{name}", None)`
instead, reading `[plugins.<name>]` in `config.toml`. `read_setting` cannot
discover which branch to take without either a directory scan (too heavy
for a per-call read inside a running node body) or the caller stating it —
and the caller already knows: it is the exact value the plugin's own
`plugin.toml` declared for this exact setting name, a fact fixed at the
call site, not something to look up at runtime. Signature becomes
`read_setting(plugin: str, name: str, *, secret: bool) -> str | None`
(`required_setting` the same). This has exactly four callers in `src/`:
`subcommands/plugin.py:85` (already holds the `Setting` object, passes
`setting.secret`), and the three builtin plugin bodies
(`image-gen/draw.py`, `web-search/search.py`, `browse/browse.py`), each
calling `required_setting(PLUGIN, "api_key", secret=True)` — verified
against each plugin's own `plugin.toml`, all three already declare
`secret = true` for that setting. `missing_settings(manifest)` already
iterates `manifest.settings`, so it passes each one's own `.secret` through
without needing to change its own signature.

**Moved keys — old environment variable → new dotted config/secret key.**

| Old env var | Current site | New key | Kind |
| --- | --- | --- | --- |
| `SADANA_MODEL_ACCESS_PROVIDER` | `client_surface.py:173` | `model_access.provider` | config |
| `SADANA_MODEL_ACCESS_MODEL` | `client_surface.py:174` | `model_access.model` | config |
| `SADANA_MODEL_ACCESS_TIMEOUT_S` | `model_providers/openrouter/provider.py:53` | `model_access.timeout_s` | config |
| `SADANA_MODEL_ACCESS_MAX_RETRIES` | `model_access.py:289` | `model_access.max_retries` | config |
| (none — new) | `model_providers/openrouter/provider.py` `BASE_URL` constant | `providers.openrouter.base_url` | config, default `"https://openrouter.ai/api/v1"` |
| (none — new) | same | `providers.openrouter.credential_ref` | config, default `"OPENROUTER_API_KEY"` |
| `OPENROUTER_API_KEY` | `model_providers/openrouter/provider.py:43` | read via `config.secret(credential_ref)` | secret |
| `SADANA_CONVERSATION_MAX_ITERATIONS` | `conversation.py:300` | `conversation.iteration_max` | config |
| `SADANA_CONVERSATION_RUN_BUDGET_SECONDS` | `conversation.py:335` | `conversation.wall_clock_s` | config |
| `SADANA_CONVERSATION_CHILD_MAX_DEPTH` | `conversation.py:1336` | `conversation.child_max_depth` | config |
| `SADANA_GATEWAY_HOST` | `subcommands/gateway.py:61` | `gateway.webhook_bind` | config |
| `SADANA_GATEWAY_PORT` | `subcommands/gateway.py:62` | `gateway.webhook_port` | config |
| `SADANA_GATEWAY_WEBHOOK_SECRET` | `subcommands/gateway.py:63` | read via `config.secret("gateway_webhook_secret")` | secret |
| `SADANA_SCHEDULING_TICK_SECONDS` | `subcommands/gateway.py:88` | `gateway.scheduler_interval_s` | config |
| `SADANA_MARKETPLACE_WEBHOOK_SECRET` | `subcommands/marketplace.py:158` | read via `config.secret("marketplace_webhook_secret")` | secret |
| `SADANA_APPROVALS_TTL_S` | `conversation_store.py:1408` | `approvals.ttl_s` | config |
| `SADANA_DOOR_WORKERS` | `door/operations.py:170` | `door.workers` | config |
| `SADANA_PLUGIN__<PLUGIN>__<SETTING>` (secret settings) | `plugins.py:370` | unchanged name, read via `config.secret(...)` | secret |
| `SADANA_PLUGIN__<PLUGIN>__<SETTING>` (non-secret settings) | `plugins.py:370` | `plugins.<plugin>.<setting>` | config |

One correction against the original brief, found while locating this table:
the brief attributed the marketplace webhook secret's read to
`subcommands/gateway.py`. It is `subcommands/marketplace.py:158` — a
different file. Every other row above was checked against the current
source, not copied from the brief.

Every row not in this table (`SADANA_DOOR_HOST/PORT/JWKS_URL/HARNESS_ID/ORG`,
`SADANA_EXECUTION_*`, `SADANA_IMAGE_GEN_*`, `SADANA_BROWSE_*`,
`SADANA_CONTEXT_*`, `SADANA_EDITOR_*`, `SADANA_MEMORY_*`,
`SADANA_PERSONA_DIR`, `SADANA_PLUGINS_DIR`) is declined by intent's own
"Declining, on purpose" — either it identifies the box itself before any
config file could be trusted to answer that question (`SADANA_DOOR_
HARNESS_ID`/`.ORG` are needed to construct the auth verifier that would
authorize a console write in the first place), or it has no user-facing
meaning the console has asked for yet.

**`GET /v1/harness`'s new `mirror` field.** Resolved during planning: a sibling field, not a rename or a type change of
`leaves_the_box` (unchanged — the noun-name tuple) or `tether` (unchanged —
H30 owns that field's shape and is mid-flight on it right now). `mirror`
reads `config.get("tether.mirror", "full")` live, one of `full`/`minimal`/
`off` — anything else is a config error surfaced at read time, not
validated at write time since nothing in this item's own grammar writes it
(the console's own per-tenant mirror setting is authoritative; this is
described in intent as the box's own floor, read-only from this box's side
until H30 gives it a write path).

**`docs/console/nouns.md` and `docs/console/wire.md`.** The four stub sections (`provider`, `budget`, `integration`, `secret`) are
filled with: Served by (H14), Fields, States, Actions, Filterable/orderable,
`search_doc`, and "Where it differs from the console's prompt" — the last
one states plainly, for `secret`, that no `GET`/`LIST` response ever carries
`value`, matching wire.md §6's already-anticipated note rather than
introducing a new deviation. `wire.md` §6 gains one paragraph: a
credential's value lives in `state_dir/.env` at `0600`, keyed by whatever
name it was `PUT` under; a `credential_ref`/`secret_ref` anywhere in this
API is that name, never an id pointing at the `secrets` noun's own minted
`id` — the same names-not-pointers posture `console_fit_plan.md` §5(a)
already commits this project to.

## Interface

```
config.get(key: str, default: T) -> T
config.secret(name: str) -> str | None
config.bind_secret_reader(reader: Callable[[str], str | None]) -> None

env_file.read_key(name: str) -> str | None
env_file.fingerprint(value: str) -> str          # "abcd·1a2b3c4d"

plugins.toml_string(value: str) -> str            # promoted from _toml_string
plugins.read_setting(plugin: str, name: str, *, secret: bool) -> str | None
plugins.required_setting(plugin: str, name: str, *, secret: bool) -> str

door.config_writer.apply(current: dict, key: str, value: object) -> dict
    # raises ValueError naming the key, for a type this emitter can't express
door.config_writer.write(path: Path, data: dict) -> None
    # raises ValueError if the round-trip read-back disagrees with data

redact.SecretRedactor  # logging.Filter subclass, no constructor args
```

Door wire shapes (fields beyond the standard set every resource already
carries):

- `provider`: `name` (read-only), `model`, `base_url?`, `credential_ref?`.
- `budget`: `iterations_max`, `wall_clock_seconds`, `runs_per_day`
  (read-only, always `null`).
- `integration`: `webhook_url` (read-only), `webhook_secret_ref`,
  `gateway_state` (read-only).
- `secret`: `name`, `kind`. Never `value`, on any verb, on any state.
  `PUT /v1/secrets/{name} { value, kind? }` → the rendered resource minus
  `value`. `list`/`get` add `fingerprint`.

Errors this item introduces beyond the existing thirteen Problem-Details
codes (none new — all of the below are `400 VALIDATION` or `404 NOT_FOUND`,
already-closed codes):

- `providers.update`/`integrations.update` with a `credential_ref`/
  `webhook_secret_ref` naming no stored secret: `400 VALIDATION`, detail <!-- # pragma: allowlist secret -->
  `"no secret named <ref>; PUT /v1/secrets/<ref> first"`.
- `plugin.set-settings` with a raw value for a `secret=True` setting:
  `400 VALIDATION`, detail naming the setting key.
- `plugin.set-settings` with a key the manifest does not declare:
  `400 VALIDATION`, detail naming the key.
- `budgets.update`/similar naming a read-only field (`runs_per_day`,
  `webhook_url`, `gateway_state`): `400 VALIDATION`, detail naming the
  field.
- `door.config_writer.write` asked to emit a value of a type it cannot
  express: `400 VALIDATION`.

## Acceptance criteria

- [ ] `config.get` returns the file's value, falls back to `default` when
      the file or key is absent, and a real environment variable wins over
      both.
- [ ] `config.get` sees a value written after the process started, on the
      very next call, with no restart.
- [ ] `config.secret` returns a real environment variable's value without
      touching disk; falls back to `state_dir/.env`, read fresh, when unset.
- [ ] `load_dotenv()` no longer adds any key to `os.environ`.
- [ ] `env_file.fingerprint` is stable for a stable value and the value
      cannot be recovered from it by inspection.
- [ ] `redact.SecretRedactor` replaces all three named shapes
      (`sk-...`, a JWT, a name-matched argument) and leaves an unrelated
      log line untouched.
- [ ] A regex for every secret value planted during the door conformance
      run, applied to every response body the run produced, matches
      nowhere.
- [ ] `plugin.set-settings` returns `400 VALIDATION` naming the key for a
      raw value against a `secret=True` setting, and succeeds for
      `{secret_ref: name}` against one that exists.
- [ ] `door.config_writer`'s emitter round-trips a string, an int, a float,
      a bool and a one-level table, and refuses (`ValueError`) a type
      outside that set.
- [ ] `providers.update` changing `credential_ref` or `base_url` takes
      effect on the very next call of an already-open runtime, with no
      restart — `_build_request`/`chat_completions_url` re-read both on
      every call. Corrected during Deploy-stage review: the first draft of
      this criterion named `model` and "the next turn," which is false —
      `client_surface.Runtime.provider`/`.model` are resolved once, at
      `open_runtime()`, and held for the process's life
      (`docs/tasks/CLIENT-SURFACE-01-one-door-in/spec.md`'s own settled
      design, unchanged by this work item). `providers.update`'s own
      `model` field reaches the *next* `open_runtime()` call instead (the
      next CLI invocation, or the next daemon restart) — real, but not the
      no-restart claim originally written here. For `cmd_gateway_run`
      specifically, which opens exactly one `Runtime` for its whole daemon
      lifetime, this means a provider's own preferred model does not
      change what a running gateway calls until that daemon restarts;
      `credential_ref`/`base_url` have no such gap.
- [ ] Every command-line flag this item's moved keys previously backed
      still works, unchanged.
- [ ] `make verify` ends `VERIFY OK`.
- [ ] P9 ("Configuration is a resource read through the door and applied
      without a restart; secrets are write-only.") is answered, in this
      document, for `provider`, `budget`, `integration`, `secret` and
      `plugin.set-settings` by name — done above in Design/Interface.

## Non-goals

- An interactive wizard for creating `config.toml`.
- An enforced credential-rotation cadence or expiry.
- Migrating every environment-configurable value this box has — only the
  keys named in the moved-keys table above.
- A second config-file backend (YAML, JSON) — `config.toml` is the one
  file, as `console_fit_plan.md` §5 already settled for TOML generally.
- Making `tether`'s own shape do anything — H30's, untouched here.

## Rejected alternatives

**Guideline 1 (reference corpus).** Adopted hermes's `(mtime, size)` cache
key with a deep-copy return. Declined its external-secret-source layer,
per-profile isolation, ASCII sanitization sweep, and its versioned
config-migration engine — named above, with the specific reason each is
solving a problem (multi-profile, multi-backend, multi-version) this
single-tenant box does not have, not a bare preference.

**Guideline 2 (reduce bets).** The identity-shim-table pattern (`providers`/
`budgets`/`integrations`/`secrets` each carry a small SQLite row for id/
version only, never the value) is the central bet here, and it is designed
to make a *second* provider an addition, not a modification: dropping a new
`model_providers/<name>/` directory in produces a new `providers` row the
next time anything lists them, with its own `[providers.<name>]` config
table, and nothing about the `providers` noun's own code changes. The
caveat guideline 2 names was checked: this is not inventing a plugin-style
seam ahead of a second consumer — the seam here (a second provider
directory) already exists and is exercised by `model_access.
list_providers()` today; this item is declining to *foreclose* it by
hard-coding "openrouter" into the door noun the way the brief's flat
`model_access.*` reading would have.

**Guideline 3 (least step-cost).** The scenario to catch is "a changed
setting reaches a running process." The three moves available: add a step
(a file-watcher or push-based reload channel), make an existing step
heavier (every already-existing per-call config read gains a freshness
check), or make an existing step harder (require an operator to signal a
reload). This design makes the existing step heavier — `config.get`/
`config.secret` were already the one place each of these values gets read;
this item changes what they read from, not how often anything calls them.
No new daemon, no new file-watcher, no reload endpoint. Cheaper move found:
none — a watcher would add a process and a race (a value read mid-write);
a reload endpoint would add a step nothing else in this API grammar has.

**Guideline 4 (minimize mutable state).** Inventoried: (a) the `(mtime,
size) → parsed dict` cache in `config.py` — derived, invalidated by the
file itself, never a source of truth; (b) the single `bind_secret_reader`
function-reference cell — set once per process, never mutated after
startup, not "state" in the sense that varies; (c) four identity-shim
tables — the minimum needed to give `id`/`version`/`ETag` real meaning for
a file-backed resource, holding no secret and no config value, only
identity and a timestamp. Explicitly not stored: a secret's value, anywhere
but `.env`; a config value, anywhere but `config.toml`; a cached fingerprint
(computed live, every call, so it can never go stale against a hand-edited
file).

## Open questions

None. The two ambiguities found while writing this (the `leaves_the_box`
naming collision with H30, and the identity gap on the four new nouns) were
both resolved above rather than left open — the first during planning, the
second in this document's own Design section.

## Concerns

**The read_setting signature change is the one place this item edits code
outside its own new files for a reason the brief did not ask for.** It is
necessary — without it, a `secret=False` setting cannot be told from a
`secret=True` one at the only place that can decide, which is the call
site — but it is worth a reviewer's attention because it touches three
built-in plugins' bodies plus the CLI's `plugin` subcommand, not just this
item's own new modules. Checked, not just asserted: all four call sites
were read, and all three plugin bodies' own `plugin.toml` files already
declare `secret = true` for the one setting each calls, so the change is
mechanical, but "mechanical" is exactly the kind of claim worth a second
look rather than trust.

**Two policies pull against each other on the identity-shim tables.**
"Names, not pointers" (CLAUDE.md, `console_fit_plan.md` §5(a)) wants
`credential_ref` to be a name, resolved by existence-check, never an id.
P1 ("Named") wants every resource to carry a real minted id anyway. Both
are satisfied here — `secrets`' own `id` exists and is real, but nothing
that *references* a secret from another noun ever carries it, only the
`name` — but only because this document chose to give `secret` (and the
other three) a persisted row at all. A shallower design (compute
`id`/`version` synthetically, always, never persisting anything) would
have kept `config.py` and `.env` as the *only* state this item introduces,
at the cost of `If-Match`/`ETag` being unenforceable — a `PATCH` racing
another `PATCH` on the same provider could not be told apart from one that
isn't. That cost was judged too high against P2's own promise ("read one
noun's docs and you can call them all" — including its concurrency
control), but a reviewer who weighs P8/P2 differently against guideline 4
should look here first.

**Confidence on the rest:** the moved-keys table, the redaction shapes, and
the atomic-write idiom were each checked against running code or an
existing, already-reviewed implementation in this repository, not proposed
from the brief's own wording alone — the marketplace-secret location error
found while doing so is the kind of thing that check exists to catch.

## Candidate CLAUDE.md rule

This document's identity-shim-table resolution is a decision future work
items backed by a flat file (not a SQLite row) will hit again — the next
noun with no natural row needs the same answer, not a fresh argument each
time. Proposed addition, for approval:

> A door noun whose value lives in a flat file (`config.toml`, `.env`, or
> similar) with no natural row of its own gets a minimal identity table —
> id, name-or-fixed-key, timestamps, version, state — holding identity and
> concurrency-control metadata only; the file stays the sole place its
> actual value is read from or written to, and the identity row never
> mirrors that value.
