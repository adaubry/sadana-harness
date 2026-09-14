# Review: Identity, a change log, and room for two people in the store (from plan.md 2026-09-14)

Reviewed: HEAD..working tree — 36 files, +4816/-337
Reviewer context: same session as build — **stated limitation**, mitigated by
delegating all three passes to reviewers with no context beyond the diff and
the three artifacts. See the first Findings entry.
Second opinion: the build stage's own `/ponytail-review` + `/simplify` ran
before this and is not repeated here by design.

## Evidence

```
$ make verify
docs/tasks/H16-store-identity-ledger-locks: all present artifacts valid
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
Success: no issues found in 56 source files
TYPES OK
▶ hermetic run: TZ=UTC LANG=C.UTF-8 PYTHONHASHSEED=0, empty env
......
  two conversations, concurrently: 250 ms for two 200 ms turns
.  one conversation, twice: 444 ms for two 200 ms turns
........................................................................ [100%]
1142 passed in 53.27s
TESTS OK
VERIFY OK
```

The two printed figures are the P8 acceptance evidence. They are measured and
reported; the assertions are the barrier and the disjoint-interval check, per
`spec.md` § Rejected alternatives.

`sqlite3 <store> .schema` of a migrated pre-existing store — a store built from
the pre-H16 DDL by hand, then opened once:

```
CREATE TABLE conversations (
    key TEXT PRIMARY KEY, template_name TEXT NOT NULL, system_prompt TEXT NOT NULL,
    prompt_sha256 TEXT NOT NULL, prompt_epoch INTEGER NOT NULL, tool_surface_json TEXT NOT NULL,
    next_turn_seq INTEGER NOT NULL, iteration_max_total INTEGER NOT NULL, iteration_used INTEGER NOT NULL,
    wall_clock_remaining_s REAL, next_child_seq INTEGER NOT NULL, stable_prompt_len INTEGER,
    context_total_prompt_tokens INTEGER NOT NULL DEFAULT 0,
    context_total_completion_tokens INTEGER NOT NULL DEFAULT 0, id TEXT, created_at REAL,
    updated_at REAL, version INTEGER NOT NULL DEFAULT 1, state TEXT NOT NULL DEFAULT 'active',
    tags_json TEXT NOT NULL DEFAULT '{}', agent TEXT)
CREATE TABLE messages (conversation_key TEXT NOT NULL, msg_seq INTEGER NOT NULL, role TEXT NOT NULL,
    content TEXT, tool_calls_json TEXT NOT NULL, tool_call_id TEXT, id TEXT, created_at REAL,
    state TEXT NOT NULL DEFAULT 'sent', run_id TEXT, PRIMARY KEY (conversation_key, msg_seq))
CREATE TABLE memory_entries (account_key TEXT NOT NULL, entry_key TEXT NOT NULL, content TEXT NOT NULL,
    updated_at REAL NOT NULL, id TEXT, created_at REAL, version INTEGER NOT NULL DEFAULT 1,
    state TEXT NOT NULL DEFAULT 'kept', kind TEXT, source TEXT, conversation_key TEXT,
    PRIMARY KEY (account_key, entry_key))
CREATE TABLE agents (id TEXT PRIMARY KEY, name TEXT UNIQUE NOT NULL, description TEXT NOT NULL DEFAULT '',
    state TEXT NOT NULL DEFAULT 'active', template TEXT, created_at REAL NOT NULL, updated_at REAL NOT NULL,
    version INTEGER NOT NULL DEFAULT 1, content_sha256 TEXT NOT NULL)
CREATE TABLE plugin_state (name TEXT PRIMARY KEY, id TEXT UNIQUE NOT NULL,
    state TEXT NOT NULL DEFAULT 'installed', source_repo TEXT, source_tag TEXT,
    installed_at REAL NOT NULL, updated_at REAL NOT NULL, version INTEGER NOT NULL DEFAULT 1)
CREATE TABLE artifacts (id TEXT PRIMARY KEY, conversation_key TEXT NOT NULL, turn_seq INTEGER NOT NULL,
    seq_in_turn INTEGER NOT NULL, name TEXT NOT NULL, kind TEXT NOT NULL, ref TEXT NOT NULL, mime TEXT,
    size_bytes INTEGER, state TEXT NOT NULL DEFAULT 'ready', created_at REAL NOT NULL,
    updated_at REAL NOT NULL, version INTEGER NOT NULL DEFAULT 1)
CREATE TABLE changes (cursor INTEGER PRIMARY KEY AUTOINCREMENT, noun TEXT NOT NULL, id TEXT NOT NULL,
    kind TEXT NOT NULL, state TEXT, version INTEGER, at REAL NOT NULL)
-- indexes: idx_changes_noun_id, idx_conversations_id, idx_messages_id,
--          idx_turn_runs_unfilled, idx_plugin_runs_unfilled

-- the legacy row, after one open:
('old-chat', 'conv_01a09fa525a570008aa822891d2c6b27', created_at set, version 1, state 'active')
('dog_name', 'mem_01a09fa525c0700094ac2ff8c936162f', state 'kept')
messages with a NULL id: 0    conversations with a NULL id: 0
ledger rows written by the fill: 0
```

## Findings

**Important**

- **[Process] This review ran in the session that wrote the code.** The three
  passes were delegated to reviewers given only the diff and the artifacts, and
  every claim below that I act on was reproduced independently before I
  believed it — but the reviewers were briefed by the author, so the questions
  they were pointed at are the author's questions. A genuinely cold reader
  might ask different ones. This is the known weak point of the chain and it is
  recorded rather than hidden.

- **[Bugs] Two concurrent turns could read each other's uncommitted rows, and
  bake a fact that never existed into a permanent system prompt.** A SQLite
  transaction belongs to a *connection*, not a thread. Every read in a turn ran
  on the one writer connection, while `_WRITE_LOCK` covered only writes — so a
  read issued from thread B while thread A sat inside `write_txn` executed
  inside A's transaction. The per-conversation lock does not help: it partitions
  `conversations`, and `memory_entries`/`persona_selections` are shared across
  every conversation an account has. Concretely: thread A's turn writes a memory
  entry in a transaction that then fails; thread B, composing a new
  conversation's system message at `client_surface._create`, reads it and freezes
  it into a prompt that is byte-stable for the life of that conversation. This
  was a regression introduced by this work item — before it, the process-wide
  `conn_lock` made the overlap impossible. **Reproduced standalone**, then fixed:
  every read on the turn path now goes through `Connections.reader()`, which is
  what `scheduling.tick` already did. Pinned by
  `tests/integration/test_store_concurrency.py::test_a_turn_never_reads_another_threads_uncommitted_rows`.
  Note this contradicts `spec.md` requirement 22's "Reads inside a turn may use
  the writer, as today" — that sentence was written before the hazard was
  understood and is wrong.

- **[Security] A forgotten memory came back.** `adopt_scheduled_memories` runs on
  every `open_runtime` and re-inserted rows without naming `state`, so the column
  fell to its `'kept'` default. Forget a fact on a `schedule:` account, restart
  the gateway, and the content is recalled into the system message again — the
  one outcome `forget` exists to prevent, introduced in the same work item that
  added `forget`. Reproduced, then fixed by carrying `state` across the move;
  pinned by `test_adoption_carries_a_forgotten_entry_over_as_forgotten`.

- **[Compliance] `spec.md` § Acceptance criteria's P1 item "the state words are
  enumerated in one place per table and no write sets a word outside that list"
  was entirely undischarged.** No enumeration existed anywhere in `src/`;
  thirteen state words were loose string literals at write sites and DDL
  defaults, with no test. Fixed: eight closed sets declared beside their tables
  (`conversation_store.CONVERSATION_STATES`, `MESSAGE_STATES`,
  `memory_store.ENTRY_STATES`, `persona_store.AGENT_STATES`,
  `plugin_install.PLUGIN_STATES`, `observability.TURN_RUN_STATES`,
  `PLUGIN_RUN_STATES`, `ARTIFACT_STATES`) and `tests/unit/test_state_words.py`,
  which drives a real write through every such table and asserts no word falls
  outside its set, that the ledger reports no word outside them, and that each
  table's DDL default is one of its own words. **`tests/unit/test_state_words.py`
  was added to `plan.md` § Files that change after approval**, said out loud
  here and in the message accompanying this review.

- **[Compliance] `spec.md` requirement 10 is not satisfied at the location it
  names, and the plan's own approved amendment to it was dropped silently.**
  The requirement says `plugin_dispatch.build_plugin_set` excludes disabled
  plugins; `plan.md` § Risks then amended that to `conn: … | None = None`. What
  shipped is neither: `build_plugin_set`'s signature is unchanged and the
  exclusion lives in `client_surface._enabled_plugin_set` over
  `plugin_install.disabled_names`. The **behaviour** is right and the placement
  is better — `plugin_dispatch` was hard-coding another module's table and
  column names and swallowing an `OperationalError`, which is the weakness
  `spec.md` § Concerns names as the ledger's worst property and which
  contradicted the spec's own reason for declining hermes's
  `add_column_if_missing`. But the contract moved twice without either artifact
  being amended. **Your call**: amend the spec, or accept the divergence on the
  record.

- **[Compliance] The spec's stated reason for rejecting SQLite triggers is
  factually false.** § Rejected alternatives and § Design both rest on "a
  trigger cannot see which branch of `INSERT ... ON CONFLICT DO UPDATE` ran."
  Two reviewers independently tested this against this repo's SQLite 3.37.2:
  the `DO UPDATE` branch fires `UPDATE` triggers and the insert branch fires
  `INSERT` triggers, so a trigger *can* distinguish `created` from `changed`.
  **The decision still stands, on a reason the spec does not give**: requirement
  20's legacy fill is an `UPDATE` over every pre-H16 row, so triggers would fire
  on all of them and produce exactly the thousands of noise rows the design
  forbids. Left unamended because the spec is approved; it should be corrected
  before anyone cites it as precedent.

- **[Compliance] `memory_store.list_entries` gained `AND state != 'forgotten'`,
  which `plan.md` step 4 explicitly said it would not.** Without it `forget`
  changes nothing observable — `client_surface._create` composes the system
  message from `list_entries`, so a forgotten fact would go straight back to the
  model. This was announced in the build report but neither artifact was
  amended, so the plan's text now contradicts the code.

- **[Bugs] `observability._size_of` caught only `OSError`.** `Path.stat()`
  raises `ValueError` for a path holding a NUL byte, and `ref` comes from a
  plugin body, not a literal. The recorder above catches only `sqlite3.Error`,
  so a `ValueError` would have reached the run being observed — the exact thing
  CLAUDE.md forbids an observability write from doing. Fixed.

- **[Bugs] `ids.parse_id` raised `AttributeError` for a non-string.**
  `ledger.record_change` calls it inside a write transaction, and four sites
  pass `row["id"]` unguarded where four siblings guard it — so a `NULL` id would
  have rolled back the real write it was only meant to annotate. Unreachable
  today (every writing connection is filled at open), but a one-line invariant
  with four unguarded dependents. Fixed to return `None`, matching what its
  docstring already promised.

**Nits**

- **[Compliance] Requirement 20's letter is not met**, though its behaviour is:
  there are nine `fill_legacy_identity` calls across five modules, each with its
  own transaction and its own `time.time()`, not "one idempotent `write_txn` at
  `open_store`". Forced by table ownership — `memory_entries` does not exist
  when `open_store` runs.
- **[Compliance] Requirement 27's letter is not met**: index DDL moved out of
  `_SCHEMA` into `_INDEXES` constants, because a `CREATE INDEX` over `id` fails
  on a pre-H16 store where `_SCHEMA` runs before `migrate_columns`. The fix is
  correct and documented at the constant; `idx_conversations_id` is named in
  requirement 5 and is now in neither `_SCHEMA` nor a migration map.
- **[Compliance] Requirement 13's "there is no path that writes one without the
  other" is not literally true**: three sites skip the ledger row while
  committing the change, when the row's `id` is `NULL`. All three are documented
  legacy-row guards.
- **[Compliance] Requirement 8's "and on every `persona_store.list_characters`
  call" is not implemented** — reconciliation is `reconcile_indexes`, at open
  only. Deliberate (a read that writes is a trap) and announced, but it means a
  box driven only by `sadana` subcommands never indexes its characters.
  `agents.description` and `agents.template` are also never populated.
- **[Compliance] Pre-migration fixtures cover 3 of 9 tables** — `conversations`,
  `messages`, `memory_entries`. The other six gain their columns through the
  same shared `migrate_columns`, so the risk is low, but the acceptance
  criterion reads as covering all of them.

**Raised, not findings**

- `chat_id` arrives from a webhook body as any non-empty string with no length
  bound, becomes the conversation key, and is retained forever as a key in
  `stores._conversation_locks`. Post-authentication, and the same unbounded key
  already grows the `conversations` table and artifact directory names, so the
  lock dict is not the weak part. The fix is bounding `chat_id` at the parse
  boundary, which belongs with CHANNELS, not here. The `ponytail:` comment that
  claimed "nothing mints keys without limit" has been corrected to say what is
  actually true.
- The read-back-then-`record_change` shape is written at seven write sites; two
  independent reviewers proposed one shared helper (~50 lines). It is a refactor
  of green code and the drift it has caused so far is cosmetic — a later item.
- `write_txn`, `migrate_columns` and `fill_legacy_identity` now live in
  `conversation_store` and are imported by five modules that have nothing to do
  with conversations. A `sqlite_util.py` leaf is the natural home; it is a pure
  move and can wait.
- `open_runtime` validates every plugin twice — once in `reconcile_plugin_state`
  (`check_bodies=False`) and once in `discover_plugins` (`check_bodies=True`).
  Different checks now, but the same directory walk.
- The `changes` table has no retention path and is the fastest-growing table in
  the store. A mirror's problem, but nothing prunes it.
- `plugin_state.name` is a directory name while the disable filter matches on
  the manifest name. They are forced equal by `plugin_install.install` and
  `editor_server`, but nothing in either of the two modules that rely on it says
  so. Worth aligning before H24 lands the disable verb.

**Checked and found clean**

- **Rejected alternatives — no drift.** All eight re-read against the diff: no
  `CREATE TRIGGER`; one `changes` table; `updated_at`/`version` are real columns
  written in the same statement; no refcounted connection registry; no strong
  set of reader connections (the `threading.local` holds the only reference, and
  a reviewer measured file descriptors flat across 50 sequential reader threads);
  no wall-clock upper bound asserted; the disabled filter shipped now; one work
  item.
- **`save()`'s `version == 1` derivation** — traced through four cases
  (legacy-filled row, create-then-save, save on an unknown key, version bumped
  by a pause) and demonstrated end-to-end on a hand-built pre-H16 store. No path
  reaches the update branch at version 1.
- **`_insert_messages` cannot report an id for a row that did not land** — the
  probe and the insert are in the same `BEGIN IMMEDIATE`, `start_seq` ranges
  line up, and no other constraint can silently trigger `OR IGNORE`.
- **Every one of the 25 `record_change` call sites passes an id whose prefix
  matches its noun**, including the three nouns backed by several tables.
- **SQL identifier interpolation** — all six f-string sites trace to
  module-level literals; nothing reachable from a tool argument, a payload or a
  filename.
- **Plugin-supplied artifact `ref`s cannot escape the run directory** — gated
  upstream by `artifact_store.contains_active` before reaching the table.
- **Requirement 24's sentence is verbatim in both required places**, and
  requirement 5's column list matches the DDL column-for-column across all nine
  tables.
- **The concurrency tests cannot hang** — every wait is bounded, and a
  regression breaks the barrier rather than blocking.
- **All four hermes citations resolve** and say what the spec claims they say.

## Decision

Approved by Adam, 2026-09-14, after the findings above were presented.

Every Important finding was already fixed in this branch before the decision,
each with a regression test, and the fixes are in the `make verify` run pasted
under `## Evidence`:

- the cross-thread uncommitted read — turn reads moved to
  `Connections.reader()`, pinned by
  `test_a_turn_never_reads_another_threads_uncommitted_rows`
- the resurrected forgotten memory — `adopt_scheduled_memories` carries
  `state`, pinned by `test_adoption_carries_a_forgotten_entry_over_as_forgotten`
- the undischarged P1 state-word criterion — eight closed sets and
  `tests/unit/test_state_words.py`
- `observability._size_of` catching `ValueError`, and `ids.parse_id` returning
  `None` for a non-string instead of raising inside a write transaction

Two are accepted as divergences on the record rather than fixed, because both
are changes to an approved artifact rather than to the code:

- **requirement 10's contract moved** from `plugin_dispatch.build_plugin_set`
  to `client_surface._enabled_plugin_set`. The behaviour is right; the spec and
  the plan both still describe the older arrangement.
- **the spec's stated reason for rejecting SQLite triggers is false.** The
  decision stands on the legacy-fill argument given in the finding. The false
  premise is recorded here so it is not cited as precedent from `spec.md`.

Both should be picked up by the next work item that touches this area, as an
amendment to `spec.md` in its own commit — `console_fit_plan.md` §5's own rule
for reopening a settled decision.
