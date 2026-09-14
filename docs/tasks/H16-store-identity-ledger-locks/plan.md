# Plan: Identity, a change log, and room for two people in the store (from intent.md 2026-09-14)

Author: Adam Aubry (product owner). Status: approved.

## Files that change

src/sadana/ids.py (new), src/sadana/ledger.py (new),
src/sadana/conversation_store.py, src/sadana/memory_store.py,
src/sadana/persona_store.py, src/sadana/plugin_install.py,
src/sadana/marketplace.py, src/sadana/observability.py,
src/sadana/stores.py, src/sadana/client_surface.py,
src/sadana/plugin_dispatch.py, src/sadana/scheduling.py,
src/sadana/subcommands/memory.py, src/sadana/subcommands/persona.py,
src/sadana/subcommands/gateway.py, src/sadana/gateway_dispatch.py,
tests/conftest.py, tests/unit/test_ids.py (new),
tests/unit/test_ledger.py (new),
tests/unit/test_state_words.py (new),
tests/integration/test_store_concurrency.py (new),
tests/unit/test_conversation_store.py, tests/unit/test_memory_store.py,
tests/unit/test_persona_store.py, tests/unit/test_plugin_install.py,
tests/unit/test_marketplace.py, tests/unit/test_observability.py,
tests/unit/test_client_surface.py, tests/unit/test_gateway_dispatch.py,
tests/unit/test_scheduling.py, tests/unit/test_plugin_dispatch.py,
tests/unit/test_subcommands_memory.py, tests/unit/test_subcommands_persona.py,
tests/unit/test_subcommands_conversations.py, tests/unit/test_subcommands_chat.py,
tests/unit/test_subcommands_runs.py, tests/unit/test_editor_server.py,
scripts/prove_gateway_webhook_e2e.py, CLAUDE.md

`tests/unit/test_state_words.py` was added during the deploy review, after
approval: the cold compliance pass found spec.md's P1 acceptance criterion
"the state words are enumerated in one place per table and no write sets a word
outside that list" entirely undischarged — no enumeration existed anywhere and
no test asserted it. Flagged out loud in the report.

`src/sadana/subcommands/gateway.py` and `src/sadana/gateway_dispatch.py` were
added during implementation, after approval: both carry a stale comment naming `client_surface.conn_lock`,
the symbol this work item deletes, and the chain gate refused the edit until the plan
named it. Flagged out loud in the report, per CLAUDE.md.

The last six are listed because they are reachable from this change and not
because a diff in each is certain. `test_subcommands_*` and
`test_editor_server.py` read stores whose shape moves; `prove_gateway_webhook_e2e.py`
carries a comment naming `client_surface.conn_lock`, which this work item
deletes, and `CLAUDE.md`'s own scripts-are-unverified rule says a text match is
not enough to assume it is fine. Naming them now is cheaper than amending an
approved plan later.

## Order of work

Each step ends green on its own. Narrow check after each: `make test` filtered
to the touched test file, then `make typecheck`.

**1. `src/sadana/ids.py` + `tests/unit/test_ids.py`.**
`uuid7()`, `make_id`, `parse_id`, `PREFIXES`. Nothing imports it yet.
Layout, most significant bit first: 48 bits of `int(time.time() * 1000)`,
4 bits of version `0b0111`, 12 bits of counter, 2 bits of variant `0b10`,
62 bits from `os.urandom(8)`. The 12-bit field is RFC 9562's fixed-length
dedicated counter: zero on a new millisecond, `+1` per id within one, and on
passing 4095 the millisecond advances by one rather than the counter wrapping,
so sort order never breaks. Counter and last-seen millisecond are module state
behind a `threading.Lock`.
Tests: ids minted across two milliseconds sort in mint order; ids minted inside
one millisecond (with `time.time` monkeypatched to a constant) sort in mint
order; `parse_id(p, make_id(p))` round-trips; `parse_id` returns `None` for a
malformed string; `make_id("nope")` and `parse_id("nope", ...)` raise
`ValueError`; counter overflow past 4095 still sorts.

**2. `src/sadana/ledger.py` + `tests/unit/test_ledger.py`.**
`changes` schema and its `(noun, id)` index, `ensure_schema`, `Change`,
`InventoryRow`, `record_change`, `changes_since`, `ledger_head`, `NOUNS`,
`inventory` and its `_INVENTORY_SQL` noun-to-table map (written complete now —
the map is data, and the tables it names arrive in steps 3–8). A module-level
statement cross-checks every noun's prefix against `ids.PREFIXES`, so a typo is
an import error. Imports `ids` and the standard library; no store.
Tests at this step cover only the writer half against a hand-made store:
`record_change` inside a `write_txn` that commits leaves one row;
inside one that raises leaves none; `changes_since` pages in cursor order;
`ledger_head` on an empty table is `0`. The `inventory` test waits for step 9.

**3. `src/sadana/conversation_store.py` + `tests/unit/test_conversation_store.py`.**
The step everything else depends on, because every store imports `write_txn`
from here.
- `_migrate_columns(conn)` becomes `migrate_columns(conn, columns)`, taking the
  map as a parameter. Its own call site passes `_MIGRATED_COLUMNS`.
- `write_txn` acquires a module-level `_WRITE_LOCK = threading.RLock()` for the
  body of the transaction and releases it after `COMMIT`/`ROLLBACK`. `RLock`,
  not `Lock`: a nested `write_txn` then still raises SQLite's *cannot start a
  transaction within a transaction* instead of hanging the process.
- `open_store` sets `PRAGMA busy_timeout = 5000` and calls
  `ledger.ensure_schema(conn)` — the one place the `changes` table is created,
  since every writing connection comes from here.
- `conversations` gains `id`, `created_at`, `updated_at`, `version`, `state`,
  `tags_json`, `agent`; `messages` gains `id`, `created_at`, `state`,
  `run_id`; both in `_SCHEMA` **and** `_MIGRATED_COLUMNS`. Unique indexes
  `idx_conversations_id` and `idx_messages_id` are created in `_SCHEMA` with
  `IF NOT EXISTS` — SQLite treats NULLs as distinct, so they are safe to create
  before the legacy fill.
- `_CONVERSATION_COLUMNS` is **not touched**. It is the tuple `load()` selects
  and `save()` builds its upsert from; adding identity columns to it would make
  every `save` overwrite `id` and `created_at`. Identity columns get their own
  SQL, and `load()` never selects them, which is what keeps `conversation.py`
  unchanged.
- `create()` takes `agent: str | None`, mints `id`, sets
  `created_at = updated_at = time.time()` (read here, never from the `now`
  parameter — that one is `time.monotonic()`), and records
  `conversations`/`created`.
- `save()`'s upsert lists the identity columns on the INSERT branch only; its
  `DO UPDATE SET` adds `updated_at = ?`, `version = version + 1` and omits
  `id`, `created_at`, `state`, `agent`. Records `conversations`/`changed`.
- `_insert_messages` mints one id per row and stamps `created_at`. Because it
  is `INSERT OR IGNORE`, the ledger records one `messages`/`created` per row
  **actually inserted**, counted from the `c.total_changes` delta across the
  `executemany`, never from the length of the input list.
- `save_pause_from_result` and `delete_pause` each record
  `conversations`/`changed` and bump `updated_at`/`version`. Neither invents a
  new state word; `state` stays `'active'` until H18 owns pauses.
- `_fill_legacy_identity(conn)` runs inside `open_store`, in one `write_txn`:
  for each of `conversations` and `messages`, select the rowids where
  `id IS NULL`, mint an id each, and `executemany` the update with
  `created_at`/`updated_at` set to one `time.time()` read for the whole pass.
  A log line before and after, naming the row count, because on a long-lived
  store this is the one slow start. Its docstring states that a pre-migration
  row's creation time is a floor, not a fact. It writes no ledger rows.
Tests: every new column present on a store built fresh from `_SCHEMA`; the same
on a store built from the pre-migration DDL written into the test by hand, in
the shape `test_conversation_store.py:199` already uses; `create` then `save`
leaves `id` and `created_at` unchanged and `version` at 2; a rolled-back write
leaves no ledger row; the legacy fill is idempotent across two `open_store`
calls and leaves `ledger_head()` unchanged.

**4. `src/sadana/memory_store.py`, `src/sadana/subcommands/memory.py` +
`tests/unit/test_memory_store.py`, `tests/unit/test_subcommands_memory.py`.**
Columns and a migration map; `write_entry` records `created` or `changed`;
`delete_entry` records `memory_entries`/`deleted` before the row goes; new
`forget_entry(conn, account, entry_key, *, now)` sets `state = 'forgotten'` and
records `changed`; `set_rubric_override` records against noun
`memory_policies`. `subcommands/memory.py`'s `cmd_memory_forget` calls
`forget_entry`, and its printed line changes from "forgot" to a sentence saying
the entry is kept but no longer used, because that is now what happens.
Tests: forget leaves the row with `state='forgotten'` and writes a `changed`;
delete removes it and writes a `deleted`; `list_entries` still returns the
forgotten row (nothing in this work item filters it — H26 decides that).

**5. `src/sadana/persona_store.py`, `src/sadana/subcommands/persona.py` +
`tests/unit/test_persona_store.py`, `tests/unit/test_subcommands_persona.py`.**
`persona_selections` columns and map; `set_selection`/`clear_selection` record
against noun `agents`. New `agents` table in this module's `_SCHEMA`, and
`reconcile_agents(conn, characters_dir, *, now)`:
for each `*.md` that parses, `content_sha256 = hashlib.sha256(file bytes)`; no
row → insert and record `created`; hash differs → update, bump
`version`/`updated_at`, record `changed`; a row whose file is gone → record
`deleted`, then delete. Content hash, not `mtime` — the reference's own
file-manifest capture (`tools/skill_ledger.py:157-182`) hashes bytes and never
reads a timestamp, and a restored file with an old `mtime` is exactly the case
a timestamp gets wrong.
`list_characters(characters_dir, *, conn=None)` reconciles when given a
connection and only lists when not, so a caller with no writer is not forced to
acquire one for a read. Called from `open_store`? No — from
`stores.ensure_schemas`, which already has the connection and already runs at
every open.
Tests: a new file inserts a row; editing it bumps `version` and writes
`changed`; deleting it writes `deleted` and removes the row; a malformed
character file is skipped by the listing and leaves no row, matching
`list_characters`' existing posture.

**6. `src/sadana/plugin_install.py` + `tests/unit/test_plugin_install.py`.**
`plugin_registry` columns and map; `register` records `plugins`/`created`;
`install` writes the `plugin_state` row (`source_repo`, `source_tag`,
`installed_at`) and records `created` or `changed`. New `plugin_state` table in
this module's `_SCHEMA`, and `reconcile_plugin_state(conn, plugins_root, *,
now)` listing every directory holding a `plugin.toml` the way
`editor_server._list_plugins` does — including one that does not validate,
which gets `state = 'error'`. A row whose directory is gone records `deleted`
and goes. A row already marked `'disabled'` keeps that state across a
reconciliation; only `'installed'` and `'error'` are recomputed.
Tests: register then install leaves one registry row and one state row with two
ledger entries; a directory with an invalid `plugin.toml` reconciles to
`'error'`; removing a directory tombstones and deletes; a hand-written
`'disabled'` row survives reconciliation.

**7. `src/sadana/marketplace.py` + `tests/unit/test_marketplace.py`.**
Columns and map; `submit` records `plugins`/`created`, `decide` records
`plugins`/`changed`, each inside the `write_txn` it already opens.

**8. `src/sadana/observability.py` + `tests/unit/test_observability.py`.**
Columns and map for `turn_runs` and `plugin_runs`; the new `artifacts` table in
this module's `_SCHEMA`. `_insert_turn_run` sets `state` from `exit_reason`
(`completed` → `'done'`, everything else → `'failed'`), `started_at =
recorded_at - duration_s`, `ended_at = recorded_at`, and records
`runs`/`created`. `_insert_plugin_run` does the same for its own row and, in
the same `write_txn`, inserts one `artifacts` row per `DagResult.artifacts`
entry — `mime` from `mimetypes.guess_type(ref)`, `size_bytes` from
`artifact_store.for_run(conversation_key, turn_seq, seq_in_turn) / ref` and
`.stat().st_size` for `kind == "file"`, both wrapped so any failure leaves
`NULL` — and records one `artifacts`/`created` each. The whole thing stays
under the existing catch-and-log posture, so none of it can reach the run.
Tests: a `DagResult` carrying one link and one file artifact produces two
`artifacts` rows with the run's key; a file that does not exist leaves
`size_bytes` NULL and still inserts; a raising artifact insert is logged and
the plugin-run row still commits or rolls back as one unit.

**9. `tests/unit/test_ledger.py` — the inventory half.**
Now that every table exists: `inventory()` returns one row per row of each
noun's tables; a noun with no table returns `()`; **and every noun's SQL
actually executes against a fresh store**. That last assertion is the whole
mitigation for this design's main concern — a table renamed in a store module
breaks `inventory()` at runtime, in a module that did not change, and this is
what catches it the next time the suite runs.

**10. The connection model — the risky step.**
`src/sadana/stores.py`: `Connections(path)` holding `path`, `writer` (from
`open_store`), `write_lock` (the same `RLock` object `write_txn` acquires,
exposed for documentation and the concurrency test), and a `threading.local`
for readers. `reader()` opens lazily per thread with `PRAGMA query_only = 1`
and **stores the connection nowhere else** — no set, no list, no registry. A
`ponytail:` comment names the ceiling and the upgrade path.
`conversation_lock(key)` from a `dict[str, threading.Lock]` behind one small
lock, never evicted, with a `ponytail:` comment saying so and naming eviction
as the upgrade.
`ensure_schemas` gains `ledger.ensure_schema`, `plugin_install._ensure_schema`,
`marketplace._ensure_schema`, `reconcile_agents` and
`reconcile_plugin_state`.
`src/sadana/client_surface.py`: `conn_lock` deleted; `Runtime.conn` becomes a
read-only property over `Runtime.connections.writer`; `open_conversation` and
`take_turn` hold `stores.conversation_lock(conversation)`; `_create` passes
`persona_store.get_selection(conn, account)` as `agent`; `open_runtime` builds
a `Connections`. The lock comment is rewritten to carry spec.md requirement
24's sentence verbatim, and the same sentence goes into `open_store`'s
docstring.
`src/sadana/scheduling.py`: `client_surface.conn_lock` usages removed —
`due_triggers` reads through `runtime.connections.reader()`, and
`advance`/`delete` already go through `write_txn`, which now holds the writer
lock. `handle_inbound` stays outside every lock this function holds, exactly as
now, because `take_turn` acquires the conversation's own lock internally.
`src/sadana/plugin_dispatch.py`: `build_plugin_set(installed, *, conn=None)`,
excluding names whose `plugin_state.state` is `'disabled'`.
`tests/conftest.py`: `make_runtime` takes a `Connections`.
Then `tests/unit/test_client_surface.py`, `test_gateway_dispatch.py`,
`test_scheduling.py`, `test_plugin_dispatch.py` follow the new shapes.

**11. `tests/integration/test_store_concurrency.py`.**
The acceptance test, once step 10 works. A stubbed `model_access.send` records
an `(enter, exit)` pair per call.
*Concurrent:* two threads, two conversations, the stub waiting on a
`threading.Barrier(2)` then sleeping 200 ms. Both reaching the barrier is only
possible if the two turns are genuinely in flight together; if they serialize,
the barrier breaks and the test fails saying so. Wall-clock total measured and
printed.
*Serialized:* two threads, one conversation, no barrier (it would deadlock).
Assert the two intervals are disjoint, and that the total exceeds 400 ms.
*Reader:* a third thread calls `search_conversations` through
`Connections.reader()` while a turn holds a conversation lock inside the
barrier, and returns without waiting.
Integration tier, not unit, because it touches threads and the wall clock and
`testing-conventions` forbids a unit test doing either.

**12. `CLAUDE.md` and the stale script comment.**
Rewrite the single-connection bullet to record that signal (1) arrived in this
work item and state the shape that replaced it. Add the reader-connection rule
the user approved at the design stage. Fix
`scripts/prove_gateway_webhook_e2e.py:162`'s comment, which names a lock that
no longer exists.

## Risks

**What this could break, by name.**

`tests/conftest.py`'s `make_runtime` is constructed by three test modules
(`test_client_surface.py`, `test_gateway_dispatch.py`, `test_scheduling.py`)
as `Runtime(conn=...)`. Changing `Runtime` to hold a `Connections` breaks all
three at once. Mitigated by keeping `conn` as a property and changing the
fixture and its three callers inside step 10, never across steps.

`conversation_store.save()` builds its upsert by joining
`_CONVERSATION_COLUMNS`. If the identity columns go into that tuple, every
`save` overwrites `id` and `created_at` with a freshly minted value — silently,
with no test failing unless one asserts stability across two saves. Step 3
leaves the tuple alone and adds exactly that test.

`_insert_messages` is `INSERT OR IGNORE` and is called repeatedly during one
turn with a rising `start_seq`. Counting ledger rows from the input list would
over-report every re-flushed message. Step 3 counts from the `total_changes`
delta.

`persona_store.list_characters` is a read; step 5 gives it the power to write.
Any caller without a writer — and `subcommands/persona.py` has one only
sometimes — would be forced to acquire one. Mitigated by `conn=None` meaning
list-only, which is the same shape spec.md already chose for this function.

`plugin_dispatch.build_plugin_set` has three callers in `scripts/` that pass
positionally against no database at all (`prove_plugin_dispatch_e2e.py:110`,
`prove_conversation_e2e.py:80`, `eval/tasks/plugin_dispatch.py:95`). A required
`conn` breaks all three, and `CLAUDE.md`'s own rule says `scripts/` rot is
invisible to `make verify`. **Stated amendment to spec.md § Interface:** the
parameter is `conn: sqlite3.Connection | None = None`, not required — a caller
with no store cannot know what is disabled, and this is the identical reasoning
spec.md already applied to `list_characters`.

`scheduling.tick` currently wraps its direct `conn` touches in
`client_surface.conn_lock`; a cold review added that after catching it racing
the webhook threads. Deleting the lock without replacing the guarantee
reintroduces exactly that bug. Step 10 replaces it with the writer lock inside
`write_txn` for writes and a thread-local reader for the query, which is
strictly narrower, not looser.

`write_txn` gaining a lock makes any nested call a hang instead of an error.
`RLock` keeps it an error.

The legacy fill runs inside `open_store`, which every test's `open_conn()`
calls. A bug there fails the whole suite rather than one file. It is guarded by
`WHERE id IS NULL`, so a fresh store updates zero rows and the blast radius is
confined to the migration tests.

`observability.make_recorder` has never had a migration step. Adding one means
a developer's real state directory gets altered on the next run. Tests are
isolated by the autouse fixture, so this bites a real machine exactly once, and
it is the same guarded `ALTER TABLE` every other store already survives.

**The most risky step is 10**, and it is already as late as it can be. It is
the only step that changes a public shape rather than adding to a private one;
it touches four test modules simultaneously; and its failure mode under threads
is a hang, which no assertion reports. Steps 1–9 are additive and each green on
its own, so a failure in 10 is unambiguously the connection model rather than
the schema underneath it. It cannot move earlier without leaving the store work
unverified beneath it, and it must precede 11, which exists to prove it.

Second-riskiest is step 3, because every other store imports `write_txn` and
`migrate_columns` from it; it is placed third so that steps 4–8 are each a
small repetition of a shape already proven once.

**Where this plan could drift back into something spec.md rejected.** Three
places, named so a reviewer can check them:

1. `conversation_lock`'s `dict[str, Lock]` is a registry, and the obvious
   next thought in step 10 is to refcount and evict it. Spec.md rejected
   hermes's refcounted per-path connection registry as a seam with one member.
   The dict is never evicted and its comment says so.
2. `Connections.reader()` will invite a list of open readers "so they can be
   closed on shutdown". That list is precisely the strong set that cost
   `hermes_state.py:4818-4845` a descriptor leak the process stayed alive
   through. `Connections` holds no reference to any reader, and step 12 writes
   that rule into `CLAUDE.md` so the next person cannot rediscover it.
3. Step 11 will be tempting to write as a wall-clock upper bound, because the
   number is easier to assert than a barrier. The barrier is the assertion; the
   300 ms figure is printed as evidence and never asserted. `testing-conventions`
   is the reason and spec.md § Rejected alternatives is the record.

## Proof

`tests/unit/test_ids.py` covers sort order across milliseconds, sort order
inside one millisecond with a frozen clock, counter overflow past 4095,
round-trip through `make_id`/`parse_id`, a malformed string returning `None`,
and an unregistered prefix raising on both functions.

`tests/unit/test_ledger.py` covers a committed `record_change` leaving exactly
one row, a rolled-back one leaving none, `changes_since` paging in cursor
order, `ledger_head` on an empty table, `inventory()` matching the tables
row-for-row, a noun with no table returning `()`, and every noun's inventory
SQL executing against a fresh store.

`tests/unit/test_conversation_store.py` covers each new column present on a
fresh store and on one built from the pre-migration DDL written into the test
by hand; `id` and `created_at` unchanged and `version` at 2 after
create-then-save; one `messages`/`created` per genuinely inserted row across a
repeated flush; the legacy fill idempotent across two opens and leaving
`ledger_head()` at 0.

`tests/unit/test_memory_store.py` covers forget leaving the row with
`state='forgotten'` and a `changed` row, and delete removing it behind a
`deleted` row.

`tests/unit/test_persona_store.py` covers the `agents` index reconciling a
created file, an edited file (version bumps), and a deleted file (tombstone
then removal).

`tests/unit/test_plugin_install.py` covers register-then-install producing both
rows and both ledger entries, an invalid `plugin.toml` reconciling to
`'error'`, and a hand-written `'disabled'` row surviving reconciliation and
being excluded from `build_plugin_set`'s catalog.

`tests/unit/test_observability.py` covers two `artifacts` rows from a
`DagResult` carrying a link and a file, a missing file leaving `size_bytes`
NULL, and an artifact-insert failure never reaching the run.

`tests/integration/test_store_concurrency.py` covers two turns on two
conversations both reaching a `threading.Barrier(2)`, two turns on one
conversation producing disjoint intervals and a total above 400 ms, and a
reader listing conversations while a turn holds a conversation lock.

`make verify` ends `VERIFY OK`, pasted in full.

`sqlite3 <store> .schema` of a store created from the pre-migration DDL and
then opened once, pasted, showing every added column and index present.
