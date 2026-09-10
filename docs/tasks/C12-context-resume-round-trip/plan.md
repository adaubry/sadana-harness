# Plan: context state survives a conversation resume (from intent.md 2026-09-10)

## Files that change

- `src/sadana/conversation_store.py` — add three columns to the
  `conversations` table's schema DDL; add a small idempotent migration
  function (adapted from `../hermes-agent/gateway/delivery_ledger.py:130-141`,
  audited: stdlib-only, ~10 lines, no hidden dependency on hermes's own
  state/config machinery — the same "safe to adapt" bar CONV-08's own
  `write_txn()` was already held to) called once from `open_store()`; extend
  `_CONVERSATION_COLUMNS`/`_conversation_row()` to carry the three values;
  rewrite `load()`'s `stable_prompt_len`/`context_state` reconstruction and
  its now-stale explanatory comment.
- `tests/unit/test_conversation_store.py` — two new tests: a real
  save-then-reload-through-a-second-connection round trip, and a
  legacy-row (pre-fix schema) migration-plus-fallback test that also
  exercises the migration running twice without error. New imports:
  `sqlite3` (to build the legacy fixture's raw table), `dataclasses.replace`
  (to vary one fixture's `stable_prompt_len`/`context_state` without
  touching the shared `conftest.conversation()` builder other test files
  also use).

- `src/sadana/context.py` — one docstring fix, no behavior change. Added
  during self-check (`/simplify`'s altitude pass caught it): `ContextState`'s
  own docstring asserted "Not persisted (spec.md's own requirement 7)",
  which this work item makes false for the *values*, if not the dataclass
  instance itself. `context.py` is live source, not a closed work item's
  artifact, so correcting it belongs in this diff rather than being left to
  mislead a future reader into reintroducing the bug this item fixes.
- `CLAUDE.md` — one new "Please do" bullet, no code change. Added at the end
  of the design stage, before this plan was drafted: a reusable rule this
  work item's design established ("A new persisted column on an existing
  SQLite table is added via an idempotent `PRAGMA table_info` + guarded
  `ALTER TABLE ... ADD COLUMN` check, never a backfill migration…"),
  proposed to and approved by the user alongside `spec.md` itself. Missing
  here originally — this plan was drafted after that edit already landed in
  the working tree, and the omission was caught by deploy-stage cold review,
  not self-check. Backfilled in place per this project's own established
  discipline (`feedback_backfill_plan_after_self_check.md`).

Originally scoped to the first two files only, confirmed by grep: nothing
outside them hardcodes the `conversations` table's column list or reads
`stable_prompt_len`/`context_state` off a loaded `Conversation` in a way
that assumes today's reset behavior — `search_conversations()` already
delegates to `load()` per key and needs no direct change; every other
caller (`gateway_dispatch.py`, `subcommands/chat.py`,
`subcommands/conversations.py`) goes through the same public
`create`/`save`/`load` functions, whose signatures do not change.

## Order of work

1. Add `stable_prompt_len INTEGER` (nullable — `NULL` means "written before
   this fix"), `context_total_prompt_tokens INTEGER NOT NULL DEFAULT 0`, and
   `context_total_completion_tokens INTEGER NOT NULL DEFAULT 0` to `_SCHEMA`'s
   `CREATE TABLE conversations` DDL (covers a brand-new store). Add
   `_migrate_columns(conn)`: `PRAGMA table_info(conversations)`, then for
   each of the three columns not already present, `ALTER TABLE conversations
   ADD COLUMN ...` with the same type/default (covers an existing store
   predating this fix; the `PRAGMA` pre-check alone makes a second,
   sequential call a no-op, so no exception guard is needed — self-check
   caught that hermes's own `sqlite3.OperationalError`/"duplicate column"
   catch only earns its keep against a concurrent-race scenario this
   store's Non-goals already exclude, see spec.md's amended Design
   section). Call it from `open_store()`
   right after `conn.executescript(_SCHEMA)`. Run the existing narrow test
   file (`pytest -k conversation_store`, through `make test`, not directly —
   testing-conventions' one-door rule) — every test should stay green
   unchanged, since nothing reads or writes the new columns yet. This is the
   step that touches every caller of `open_store()`, so proving it inert
   before any behavior changes is the point of doing it first and alone.
2. Add the three column names to `_CONVERSATION_COLUMNS`; extend
   `_conversation_row()` to append `conversation.stable_prompt_len`,
   `conversation.context_state.total_prompt_tokens`,
   `conversation.context_state.total_completion_tokens` (both `create()` and
   `save()` pick this up automatically — they already build their row/column
   list off these two constants generically, per `conversation_store.py`'s
   existing pattern for `next_turn_seq`/`iteration_used`/`next_child_seq`).
   Replace `load()`'s two hardcoded reset lines with the real reconstruction
   (`stable_prompt_len` falls back to `len(row["system_prompt"])` only when
   the stored value is `NULL`; `context_state` is built from the two token
   columns directly — no fallback needed, `DEFAULT 0` already matches
   today's reset value exactly). Rewrite the comment block explaining why
   the reset existed (`conversation_store.py:251-259` today) to describe
   what's persisted now and point at this work item instead of C10/C11.
   Run the narrow suite again — `test_create_then_load_round_trips_every_field`
   now genuinely exercises the new columns (its fixture's default values
   happen to equal both the write-path values and the fallback, so it stays
   green either way, but for the right reason from here on).
3. Add `test_load_reconstructs_context_state_and_stable_prompt_len_across_a_second_connection`:
   build a conversation via `_conversation()` then `dataclasses.replace()`
   to set a nonzero `context_state` and a `stable_prompt_len` narrower than
   the full system prompt, `create()` it, close that connection, `open_store()`
   a **second**, independent connection to the same path, `load()`, assert
   both fields round-trip exactly (and the object still satisfies `==`
   against the mutated fixture, matching `test_create_then_load_round_trips_every_field`'s
   own existing "every field" bar).
4. Add `test_load_falls_back_for_a_legacy_row_and_migration_is_idempotent`:
   using a raw `sqlite3.connect()` (not `open_store()`, since that already
   creates the new columns), create `conversations`/`messages` with
   exactly today's pre-fix schema (11 columns) and hand-insert one row with
   `INSERT INTO conversations (...)` naming those 11 columns explicitly,
   close that raw connection. Then `open_store()` the same path (first real
   call — this is what must apply the migration), close it, `open_store()`
   the same path again (second call — must not raise, proving the guard),
   `load()` the row through the second connection, and assert
   `context_state == ContextState()` and `stable_prompt_len ==
   len(system_prompt)` — today's exact pre-fix behavior, preserved for a row
   that predates this fix, per requirement 4.
5. `/ponytail-review` + `/simplify` self-check against the diff, per
   build-skill's own phase two. Apply anything worth taking now in the same
   diff; note anything that would change behavior instead of applying it.
6. `make verify`; paste the output.

## Risks

**What could this change break?** Every real caller of `open_store()` —
`gateway_dispatch.py`'s inbound-message path, `subcommands/chat.py`'s
`--resume` path, `subcommands/conversations.py`'s listing, and every test
in `test_conversation_store.py`, `test_gateway_dispatch.py`,
`test_subcommands_chat.py`, `test_subcommands_conversations.py` that opens a
store at all — since `open_store()` is the one choke point every one of
them shares. Checked directly: the two existing tests that hand-write raw
`INSERT INTO conversations (key, ...)` SQL
(`test_write_txn_commits_on_success`, `test_write_txn_rolls_back_on_failure`,
`conversation_store.py`'s own test file) name their 11 columns explicitly
and never touch the three new ones, which are nullable or `DEFAULT`-backed
— SQLite fills them in without those tests changing at all. Grepped for any
other file hardcoding the `conversations` table's column list or shape:
none outside `test_conversation_store.py` itself.

**Which step is riskiest, and why that one?** Step 1 — the schema and
migration change, because it is the one piece every caller reaches through
`open_store()`, and an idempotency bug in the migration's `PRAGMA
table_info` pre-check would surface as a startup crash for every caller,
not a quiet wrong value. That is exactly why it is the first step
and its own step, proven against the full existing test file with zero
behavior change riding along, before step 2 makes the new columns mean
anything. If step 1 were bundled with step 2, a startup-crash regression and
a wrong-value regression would be indistinguishable from the first failing
test.

**Which options did spec.md already reject, and is this plan drifting back
toward one?** Checked against all four: not storing a template
name/pointer and re-deriving `stable_prompt_len` (rejected alternative 1) —
this plan never reads `template_name` for that purpose, only the persisted
integer; not a JSON blob for `context_state` (alternative 2) — this plan
uses two flat `INTEGER` columns; not a schema-reflection migration engine
(alternative 3) — this plan hand-writes three `ALTER TABLE` statements
behind one `PRAGMA table_info` check, not a diff engine; not backfilling
legacy rows to a computed value (alternative 4) — this plan leaves
`stable_prompt_len` `NULL` on a pre-fix row and lets `load()`'s fallback
handle it, never writes a guessed value into storage. No drift found.

## Proof

- `make verify` output, ending `VERIFY OK`, pasted into the conversation
  (Test stage) and then into `review.md` under `## Evidence` (Deploy stage).
- The two new tests in `test_conversation_store.py` specifically: one
  proving a real save/reload round trip through a **second, independently
  opened connection** (not an in-memory `dataclasses.replace()` — the
  failure mode this item exists to fix only shows up across a real
  reconnect), and one proving a pre-fix-schema row both survives the
  migration and keeps today's exact fallback behavior, with the migration
  itself shown to be safe to run twice.
