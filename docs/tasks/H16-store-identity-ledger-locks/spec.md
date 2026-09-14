# Spec: Identity, a change log, and room for two people in the store

Intent: docs/tasks/H16-store-identity-ledger-locks/intent.md

Author: Adam Aubry (product owner). Status: approved.

## Requirements

Each traces to a paragraph of `intent.md`. The promise ids are
`docs/reference/console_fit_plan.md` §4; the register ids are the console
project's own pre-mortem, restated in `intent.md`.

**Identity (intent § Proposed outcome ¶1; P1; A5)**

1. `src/sadana/ids.py` provides `uuid7() -> str`: a canonical dashed UUID
   string with a 48-bit millisecond Unix timestamp, version `7` and variant
   `10` bits set, and 74 random bits. Two ids minted inside one millisecond
   sort against each other in the same order they were minted.
2. `ids.make_id(prefix) -> str` returns `f"{prefix}_{hex}"` where `hex` is
   `uuid7()` with its dashes removed. `ids.parse_id(prefix, s) -> str | None`
   returns the hex half when `s` is `make_id(prefix)`-shaped, else `None`.
3. `ids.PREFIXES` is a closed frozenset: `hrn conv msg run trc spn appr sch
   agt tmpl mem rub art plg wfl node tool prv bdg intg sec op insp`. An
   unregistered prefix reaching `make_id` or `parse_id` raises `ValueError`.
   `ledger.py` cross-checks its own noun table against `ids.PREFIXES` in a
   module-level statement, so a noun whose prefix was never registered is an
   import-time failure rather than a first-write failure.
4. Every table named in requirement 5 gains, at minimum, `id TEXT`,
   `created_at REAL` or `updated_at REAL` as listed, `version INTEGER NOT NULL
   DEFAULT 1`, and where listed a `state TEXT NOT NULL DEFAULT <word>`. Each
   new column is declared in its owning module's `_SCHEMA` string **and** in
   that module's migration map. No column is renamed; no table is dropped.
5. The column additions, by table:

   | table | added |
   | --- | --- |
   | `conversations` | `id TEXT` (unique index `idx_conversations_id`), `created_at REAL`, `updated_at REAL`, `version INTEGER NOT NULL DEFAULT 1`, `state TEXT NOT NULL DEFAULT 'active'`, `tags_json TEXT NOT NULL DEFAULT '{}'`, `agent TEXT` |
   | `messages` | `id TEXT` (unique index `idx_messages_id`), `created_at REAL`, `state TEXT NOT NULL DEFAULT 'sent'`, `run_id TEXT` |
   | `memory_entries` | `id TEXT`, `created_at REAL`, `version INTEGER NOT NULL DEFAULT 1`, `state TEXT NOT NULL DEFAULT 'kept'`, `kind TEXT`, `source TEXT`, `conversation_key TEXT` |
   | `memory_rubric_overrides` | `id TEXT`, `created_at REAL`, `version INTEGER NOT NULL DEFAULT 1`, `updated_by TEXT` |
   | `persona_selections` | `id TEXT`, `created_at REAL`, `version INTEGER NOT NULL DEFAULT 1` |
   | `plugin_registry` | `id TEXT`, `updated_at REAL`, `version INTEGER NOT NULL DEFAULT 1` |
   | `turn_runs` | `id TEXT`, `state TEXT NOT NULL DEFAULT 'done'`, `started_at REAL`, `ended_at REAL`, `version INTEGER NOT NULL DEFAULT 1` |
   | `plugin_runs` | `id TEXT`, `state TEXT NOT NULL DEFAULT 'closed'`, `started_at REAL`, `ended_at REAL`, `version INTEGER NOT NULL DEFAULT 1` |
   | `marketplace_releases` | `id TEXT`, `updated_at REAL`, `version INTEGER NOT NULL DEFAULT 1` |

6. `conversations.agent` holds the character **name** selected for the
   creating account at the moment the conversation's voice is resolved, or
   `NULL` when that account has no selection. A name, never a live reference,
   and never the rendered text.
7. `turn_runs.state` is derived by the existing post-hoc insert from
   `exit_reason`: `completed` → `'done'`, every other value → `'failed'`.
   `started_at = recorded_at - duration_s`, `ended_at = recorded_at`. These are
   reconstructed, not measured; H21 makes them live.

**The two file-backed index tables and the artifacts index (intent § Proposed
outcome ¶2; P1)**

8. `agents (id TEXT PRIMARY KEY, name TEXT UNIQUE NOT NULL, description TEXT
   NOT NULL DEFAULT '', state TEXT NOT NULL DEFAULT 'active', template TEXT,
   created_at REAL NOT NULL, updated_at REAL NOT NULL, version INTEGER NOT NULL
   DEFAULT 1, content_sha256 TEXT NOT NULL)`, reconciled against the characters
   directory at store open and on every `persona_store.list_characters` call: a
   file with no row is inserted; a file whose `content_sha256` differs bumps
   `version` and `updated_at`; a row whose file is gone is deleted after a
   `deleted` ledger row.
9. `plugin_state (name TEXT PRIMARY KEY, id TEXT UNIQUE NOT NULL, state TEXT
   NOT NULL DEFAULT 'installed', source_repo TEXT, source_tag TEXT,
   installed_at REAL NOT NULL, updated_at REAL NOT NULL, version INTEGER NOT
   NULL DEFAULT 1)` — one row per directory holding a `plugin.toml`, listed the
   way `editor_server._list_plugins` lists them (validating or not), with
   `state = 'error'` for one that does not validate.
10. `plugin_dispatch.build_plugin_set` excludes a plugin whose `plugin_state`
    row has `state = 'disabled'` from the catalog and the tool specs.
11. `artifacts (id TEXT PRIMARY KEY, conversation_key TEXT NOT NULL, turn_seq
    INTEGER NOT NULL, seq_in_turn INTEGER NOT NULL, name TEXT NOT NULL, kind
    TEXT NOT NULL, ref TEXT NOT NULL, mime TEXT, size_bytes INTEGER, state TEXT
    NOT NULL DEFAULT 'ready', created_at REAL NOT NULL, updated_at REAL NOT
    NULL, version INTEGER NOT NULL DEFAULT 1)`, filled by
    `observability._insert_plugin_run` from `DagResult.artifacts` inside the
    same `write_txn` as the plugin-run row. `mime` from `mimetypes.guess_type`,
    `size_bytes` from `stat` for `kind == "file"`, both best-effort and `NULL`
    on any failure, the whole insert under the recording write's existing
    catch-and-log posture.

**The ledger (intent § Proposed outcome ¶3; P3; B3, B10)**

12. `src/sadana/ledger.py` owns `changes (cursor INTEGER PRIMARY KEY
    AUTOINCREMENT, noun TEXT NOT NULL, id TEXT NOT NULL, kind TEXT NOT NULL,
    state TEXT, version INTEGER, at REAL NOT NULL)` with an index on
    `(noun, id)`.
13. `ledger.record_change(c, *, noun, id, kind, state, version, at)` executes
    on a connection already inside a `write_txn`, so the change row and the
    change it describes commit or roll back together. There is no path that
    writes one without the other.
14. Every write site listed in § Interface calls `record_change`, and the row's
    own `updated_at` and `version = version + 1` are set in the same statement
    that makes the change.
15. `ledger.changes_since(conn, cursor, limit) -> tuple[Change, ...]` and
    `ledger.ledger_head(conn) -> int`.
16. `ledger.inventory(conn) -> dict[str, tuple[InventoryRow, ...]]` — `id`,
    `updated_at`, `version` per row, keyed by plural noun, over every ledgered
    noun. A noun with no table yet returns an empty tuple.
17. `ledger.NOUNS` is the closed list: `conversations messages memory_entries
    memory_policies agents plugins artifacts runs traces schedules approvals
    operations harness`. `schedules`, `approvals` and `operations` are
    registered here and written by H27, H18 and H19; `traces` by H20;
    `harness` by H19.
18. Every delete writes `kind = "deleted"` before the row goes.
    `memory_store.delete_entry` becomes the tombstoned purge. A new
    `memory_store.forget_entry(conn, account, entry_key, *, now)` sets
    `state = 'forgotten'` and records `kind = "changed"`;
    `subcommands/memory.py`'s `forget` calls the new function.
19. `ledger.py` imports no store module. Stores import it.

**Legacy rows (intent § Affected users and systems ¶3; P1(b) of
`console_fit_plan.md` §5)**

20. At `open_store`, one idempotent `write_txn` gives every row with
    `id IS NULL` an `id` and, where the table has them,
    `created_at = updated_at = time.time()` read once at that moment. The
    docstring states that the creation time of a pre-migration row is a floor,
    not a fact. The fill writes **no** ledger rows.

**The connection model (intent § Proposed outcome ¶4; P8; C8)**

21. `src/sadana/stores.py` gains `Connections(path)`: one writer connection
    (`PRAGMA busy_timeout = 5000`), a write lock acquired only inside
    `write_txn`, and thread-local reader connections opened lazily per thread
    on the same path with `PRAGMA query_only = 1`.
22. `stores.conversation_lock(key) -> threading.Lock`, from a registry
    (`dict[str, Lock]` behind one small lock, never evicted).
    `client_surface.conn_lock` is deleted. `take_turn` and `open_conversation`
    hold the named conversation's lock, not the process's. Reads inside a turn
    may use the writer, as today.
23. `client_surface.Runtime` carries `Connections` and exposes `conn` as a
    property returning the writer, so every existing caller keeps compiling.
24. The lock comment in `client_surface.py` and the `open_store` docstring both
    carry this sentence verbatim: *"What this survives: two turns on two
    conversations in two threads, and a reader listing conversations while a
    turn runs. What it does not: two turns on one conversation on one event
    loop; a call node at its approval gate (H18 removes this)."*
25. `CLAUDE.md`'s bullet about revisiting `conversation_store.py`'s single
    connection is rewritten to record that signal (1) arrived in this work
    item, and to state the shape that replaced it.

**Unchanged**

26. `conversation.py` does not change. Ids and timestamps are minted in the
    store on insert and never appear on a `Message` or `Conversation` value.
27. `_SCHEMA` strings stay the single source of each table's shape; the
    migration maps stay the single source of what was added later.

## Design

**Policies applied**

`testing-conventions` is the only policy skill present in `.claude/skills/`
that bears on this work item; `project-structure` and `reference-lookup` do not
exist in this repository, so their questions are answered from `CLAUDE.md`'s
own layout rules instead. `testing-conventions` is applied in
§ Acceptance criteria and is the source of one deliberate deviation from the
intent's original wording, recorded in § Rejected alternatives.

`CLAUDE.md`'s own rules are treated as policy and named where they bind:
real-I/O-module separation, names-not-pointers, the guarded `ALTER TABLE`
shape, the no-registry-for-one-member rule, immutable-value budgets, and the
single-writer revisit condition.

**What the reference did, and what we take**

`../hermes-agent`, `kind == production-code`:

- **`plugins/memory/holographic/store.py:100-160`** — a process-wide connection
  registry keyed by the resolved path, holding one connection, one `RLock` and
  a refcount. Its comment names the failure that bought it: several providers
  in one process each opened their own connection, raced as independent WAL
  writers, and one connection left an open write transaction that pinned the
  write lock until every other connection's write failed with *database is
  locked* for the full busy timeout. **Adopting the decision** — one writer per
  path, one lock, `isolation_level=None` so a raising write cannot leave a
  transaction open. **Declining the registry and the refcount**: they exist to
  let many independently-constructed store objects share one connection, and we
  have exactly one `Connections`, built once by `open_runtime`. A refcounted
  registry of one is the lookup-table-of-one `CLAUDE.md` forbids.
- **`hermes_state.py:4818-4845`** — the read-path scheme they **removed**, and
  the most valuable thing found. A `threading.local` plus a strong set pinned
  one connection per `(store × thread)` for the life of the process; Starlette
  dispatches sync routes on worker threads, so a store that is never closed
  accumulated a connection and two descriptors — the database and its `-wal` —
  per worker thread that ever read, until the process hit the 256-descriptor
  soft limit and every request failed with `EMFILE` while the process stayed
  alive, so the supervisor's restart-on-exit never fired. They replaced it with
  a bounded LIFO pool plus per-path permits (`_READ_POOL_MAX = 8`,
  `hermes_state.py:387`). **We take the warning, not the pool.** Thread-local
  readers are kept — but with **no strong registry of them and no explicit
  close**, which is precisely the half that caused the outage: without a second
  reference, CPython's refcounting closes a reader when its thread's local
  storage dies. The ceiling is named in a `ponytail:` comment and the upgrade
  path is their bounded pool, never a set of strong references.
- **`hermes_cli/sqlite_util.py:14-28`** — `add_column_if_missing` swallows
  *duplicate column name* to survive a concurrent migrator (their issue
  #21708). **Declined**, as `_migrate_columns` already declined it once: the
  `PRAGMA table_info` check plus a single writer excludes the race, and
  swallowing that error would also swallow a genuine schema disagreement. Their
  `write_txn` we already adapted at `conversation_store.py:229`.
- **`gateway/delivery_ledger.py`** and **`gateway/lifecycle_ledger.py`** —
  both are called ledgers and neither is one. The first is a per-obligation
  delivery state machine; the second is crash forensics over a JSON sentinel.
  Nothing in the corpus is an ordered change feed with a cursor: a
  `grep` for `AUTOINCREMENT` across their core finds schema files and tests,
  no `changes_since`-shaped reader. **No prior art; this one is ours.** What we
  do take from `delivery_ledger.py:47` is its module-level `threading.Lock`
  guarding every write, and its stated posture that a ledger failure must never
  block the thing it observes — which for us applies only to the
  observability-owned writes, where that posture already exists.

**Where the code goes**

`src/sadana/ids.py` is new. It is **not** pure, despite the intent brief's
label: it reads the clock and holds a process-local counter behind a lock. It
satisfies `CLAUDE.md` by being its own file rather than by being pure — the
rule separates real-I/O code from a block's pure module, and `ids.py` shares a
file with nothing.

`src/sadana/ledger.py` is new, touches `sqlite3`, and is its own file for the
same reason. It imports `ids` and the standard library, and no store.

The shared migration helper: `conversation_store._migrate_columns(conn)` is
generalised to `migrate_columns(conn, columns)` taking the map as a parameter,
so `memory_store`, `persona_store`, `plugin_install`, `marketplace` and
`observability` each keep their own map beside their own `_SCHEMA` and call it.
One function, five new maps, no new module, no registry.

`stores.py` grows from "ensure schemas" into the owner of connections, which is
where the intent's fourth paragraph lands. It is already the module that knows
about every store at once, and it already cannot live inside
`conversation_store` (which `memory_store` and `persona_store` both import).

The write lock is a module-level `threading.RLock` in `conversation_store`,
acquired inside `write_txn`, with `Connections` holding a reference to it.
`write_txn(conn)` is called from roughly twenty sites with a bare connection
and cannot be handed a `Connections`; `sqlite3.Connection` is a C type and
cannot carry an attribute. One writer per process means one lock per process,
which is the same fact stated once. `RLock` rather than `Lock` because a nested
`write_txn` would otherwise hang the process silently, where today it raises
SQLite's own *cannot start a transaction within a transaction*; a hang is the
worse failure and the reference uses `RLock` here for the same reason.

**The central choice, in the three moves**

The scenario uncaught is *an observer cannot tell what changed*. The three
moves available are add a step, make a step heavier, make a step harder.

This design **makes an existing step heavier**: every write already runs inside
`write_txn`, and each one gains one more statement inside the same
transaction. It deliberately does **not** add a step — no trigger, no
after-the-fact scanner, no second write path — because a step that can be
skipped is a step that will be, and the whole value of the ledger is that it
cannot diverge from what it describes.

The cheaper move considered and rejected was SQLite `AFTER INSERT/UPDATE/DELETE`
triggers: genuinely zero call-site cost, and it catches every writer including
one added later that forgets. It is rejected in § Rejected alternatives for a
reason the corpus does not have to teach us — a trigger cannot see which of
`INSERT ... ON CONFLICT DO UPDATE`'s two branches ran, so it cannot distinguish
`created` from `changed`, and half of our writes are that statement.

The weight this puts on every future caller is one line inside a transaction it
already opened, and that is the honest cost.

**Where this sits relative to the plugin seam**

Past it, and this work item is underneath it. Plugins exist and dispatch
through `plugin_dispatch`; nothing here invents a seam. The one registry-shaped
thing added — `ledger`'s noun-to-table map — is a data table inside one module,
not a dispatch seam, and it exists because requirement 19 forbids the import
that would otherwise let each store answer for itself.

**State inventory**

New stored state, each with why it cannot be derived:

- **`id`, `created_at`** — a minted value and a past instant. Nothing in the
  row can reproduce either.
- **`updated_at`, `version`** — derivable in principle from the ledger
  (`MAX(cursor)` per `(noun, id)`), and stored anyway: the inventory read
  exists to be cheap, and computing it from a growing log is the opposite of
  cheap. This is the one deliberate denormalisation, and the ledger row and the
  column are written in one statement so they cannot disagree.
- **`state`** — a fact about the row that no other column implies, except
  `turn_runs.state`, which *is* derived from `exit_reason` (requirement 7) and
  is stored only so H21 can start measuring it live without a schema change.
- **`agents.content_sha256`** — the whole point of the row: the cached hash is
  what makes reconciliation a comparison instead of a rewrite.
- **`artifacts.mime`, `size_bytes`** — derivable from the file, and stored
  because the file may be gone by the time anybody asks.

Deliberately **not** stored: the number of conversations, any count, any
"latest change" pointer per noun (that is `MAX(cursor)`, read on demand), and
any cache of which plugins are disabled — `build_plugin_set` reads the table.

`plugin_state` and `agents` are index tables over files, which is stored state
duplicating a filesystem. They are justified by requirement 16: an inventory
that had to walk two directories would not be cheap to enumerate, which is the
half of P3 they exist to serve. Both are reconciled, never authoritative — the
file wins every time.

**Clocks**

Every timestamp this work item writes is read fresh from `time.time()` at the
moment of writing. The `now` already threaded into `conversation_store.create`
and `save` is `time.monotonic()` — `client_surface.py:325` and `:373` — and
exists to turn a wall-clock budget back into a deadline. The two are never
mixed. `console_fit_plan.md` §5(g) is the decision; the collision is a finding
of the plan stage, recorded in `intent.md`'s trace.

## Interface

**`src/sadana/ids.py`**

```
uuid7() -> str
make_id(prefix: str) -> str          # ValueError on an unregistered prefix
parse_id(prefix: str, s: str) -> str | None   # ValueError on an unregistered prefix
PREFIXES: frozenset[str]
```

`make_id` and `parse_id` distinguish two failures: an unregistered *prefix* is
the caller's bug and raises; a malformed *input string* is data and returns
`None`.

Within one millisecond, the 12-bit `rand_a` field is used as a counter starting
at zero and incrementing per id (RFC 9562's fixed-length dedicated counter). On
overflow past 4095 the millisecond is advanced by one rather than the counter
wrapping, so sort order never breaks. The counter and its last-seen millisecond
are module-level state behind a `threading.Lock`.

**`src/sadana/ledger.py`**

```
record_change(c, *, noun: str, id: str, kind: str, state: str | None,
              version: int | None, at: float) -> None
changes_since(conn, cursor: int, limit: int) -> tuple[Change, ...]
ledger_head(conn) -> int
inventory(conn) -> dict[str, tuple[InventoryRow, ...]]
ensure_schema(conn) -> None
NOUNS: tuple[str, ...]
Change(cursor, noun, id, kind, state, version, at)   # frozen
InventoryRow(id, updated_at, version)                 # frozen
```

`kind` is a closed set of three: `"created"`, `"changed"`, `"deleted"`.

**The noun map**

A noun may be backed by more than one table. `inventory` unions them; every id
carries its own prefix, so no two rows collide.

| noun | prefix | table(s) |
| --- | --- | --- |
| `conversations` | `conv` | `conversations` |
| `messages` | `msg` | `messages` |
| `memory_entries` | `mem` | `memory_entries` |
| `memory_policies` | `rub` | `memory_rubric_overrides` |
| `agents` | `agt` | `agents`, `persona_selections` |
| `plugins` | `plg` | `plugin_state`, `plugin_registry`, `marketplace_releases` |
| `artifacts` | `art` | `artifacts` |
| `runs` | `run` | `turn_runs`, `plugin_runs` |
| `traces` | `trc` | — (H20) |
| `schedules` | `sch` | — (H27) |
| `approvals` | `appr` | — (H18) |
| `operations` | `op` | — (H19) |
| `harness` | `hrn` | — (H19) |

**Every write site, and what it records**

| site | noun | kind |
| --- | --- | --- |
| `conversation_store.create` | `conversations` | `created` |
| `conversation_store.save` | `conversations` | `changed`, plus one `messages`/`created` per newly inserted message |
| `conversation_store.save_pause_from_result` | `conversations` | `changed` |
| `conversation_store.delete_pause` | `conversations` | `changed` |
| `memory_store.write_entry` | `memory_entries` | `created` or `changed` |
| `memory_store.forget_entry` *(new)* | `memory_entries` | `changed`, `state='forgotten'` |
| `memory_store.delete_entry` | `memory_entries` | `deleted` |
| `memory_store.set_rubric_override` | `memory_policies` | `created` or `changed` |
| `persona_store.set_selection` | `agents` | `created` or `changed` |
| `persona_store.clear_selection` | `agents` | `deleted` |
| `plugin_install.register` | `plugins` | `created` |
| `plugin_install.install` | `plugins` | `created` or `changed` |
| `marketplace.submit` | `plugins` | `created` |
| `marketplace.decide` | `plugins` | `changed` |
| `observability._insert_turn_run` | `runs` | `created` |
| `observability._insert_plugin_run` | `runs` | `created`, plus one `artifacts`/`created` per artifact |
| the `agents` reconciliation | `agents` | `created`, `changed`, `deleted` |
| the `plugin_state` reconciliation | `plugins` | `created`, `changed`, `deleted` |

`create` and `save` distinguish `created` from `changed` by which branch of the
upsert ran, read back from `sqlite3.Connection.total_changes` or an explicit
`SELECT` inside the same transaction — never inferred from the caller.

**`src/sadana/stores.py`**

```
class Connections:
    def __init__(self, path: Path) -> None
    path: Path
    writer: sqlite3.Connection
    write_lock: threading.RLock        # the same object write_txn acquires
    def reader(self) -> sqlite3.Connection

conversation_lock(key: str) -> threading.Lock
```

**Changed signatures**

- `conversation_store.create(..., agent: str | None)` — `client_surface._create`
  passes `persona_store.get_selection(conn, account)`. The store never imports
  `persona_store`; that direction is what keeps them acyclic.
- `conversation_store._migrate_columns(conn)` → `migrate_columns(conn, columns)`.
- `plugin_dispatch.build_plugin_set(installed, *, conn)` — one caller,
  `client_surface.open_runtime`.
- `persona_store.list_characters(characters_dir, *, conn=None)` — reconciles
  when given a connection, lists only when not. The connection is optional
  because `list_characters` is also reached from a path that has none, and a
  read that silently requires a writer is worse than one that says so.
- `client_surface.Runtime.conn` becomes a read-only property over
  `Runtime.connections.writer`.
- `tests/conftest.py`'s `make_runtime` takes a `Connections` rather than a bare
  connection.

**Errors**

No new exception type. An unregistered prefix is `ValueError`. Every store
error stays `sqlite3.Error` and keeps its existing handling — in particular the
artifacts insert inherits `observability`'s catch-and-log, so a failure to
record what a run produced never reaches the run.

## Acceptance criteria

The three promises this work item carries, quoted verbatim from
`docs/reference/console_fit_plan.md` §4, each answered for this item's nouns:

> **P1 | Named |** Every resource carries an immutable id, a mutable name,
> wall-clock `created_at` and `updated_at`, a version, and a state from a
> written closed set.

- [ ] Every table in requirement 5 carries `id`, and the times listed for it,
      on a store created fresh from its `_SCHEMA`.
- [ ] The same is true on a store created from the pre-migration DDL, written
      into the test by hand as a fixture the way
      `tests/unit/test_conversation_store.py:199` already does.
- [ ] The state words are enumerated in one place per table and no write sets a
      word outside that list.
- [ ] `conversations.agent` holds the selected character's name for an account
      that has one, and `NULL` for an account that does not.

> **P3 | Logged |** Every change lands in an ordered ledger with a cursor,
> deletes leave tombstones, and the whole inventory is cheap to enumerate.

- [ ] Every write site in § Interface produces exactly **one** ledger row.
- [ ] Wrapping any one of those writes in a transaction that is rolled back
      leaves **no** ledger row behind.
- [ ] `inventory()` returns one row per row of each noun's table(s), and a noun
      with no table returns `()`.
- [ ] `forget_entry` produces a `changed` with `state='forgotten'` and the entry
      is still there; `delete_entry` produces a `deleted` and the row is gone.
- [ ] The legacy fill writes no ledger rows: `ledger_head()` is unchanged
      across an `open_store` that fills a pre-migration store.
- [ ] The `agents` index reconciles a created file, an edited file (version
      bumps) and a deleted file (tombstone, then the row goes).

> **P8 | Shared |** Concurrency at the grain of the thing being changed;
> per-principal state keyed by the principal the door derived.

- [ ] Two turns on two conversations, against a stubbed `model_access.send`
      that blocks on a `threading.Barrier(2)` and then sleeps 200 ms, both
      reach the barrier — which is only possible if they are genuinely in
      flight together. The wall-clock total is measured and printed.
- [ ] Two turns on one conversation do not overlap: the stub records an
      `(enter, exit)` interval per call and the two intervals are disjoint. The
      wall-clock total is measured and printed, and is above 400 ms.
- [ ] A reader thread lists conversations through `Connections.reader()` while a
      turn holds a conversation lock, and returns without waiting for it.

And the whole:

- [ ] `make verify` ends `VERIFY OK`.
- [ ] `sqlite3 <store> .schema` of a migrated pre-existing store, pasted as
      evidence.
- [ ] `CLAUDE.md`'s single-connection bullet is rewritten; the sentence in
      requirement 24 appears in both places verbatim.

Per `testing-conventions`: the concurrency checks touch threads and the wall
clock, so they live in `tests/integration/`, not `tests/unit/`. Everything else
is unit-tier against a `tmp_path` store.

## Non-goals

- Any way in from the network. That is H19.
- Live `started_at`/`ended_at` measurement for runs. H21.
- A verb that sets any of the new state words other than `forgotten`. Nothing
  can close a conversation or disable a plugin until H19 and H24.
- Purge, and any enumeration keyed to one principal. H19 onward.
- Two turns on one conversation concurrently, and a non-blocking approval gate.
  Both are named as surviving limitations; H18 removes the second.
- Touching `scheduled_triggers`. H27 gives it the standard columns.

## Open questions

- Three of the thirteen nouns are backed by more than one table
  (`agents`, `plugins`, `runs`). The union keeps every ledger id resolvable
  against the inventory, which is the property that matters, but a console
  reconciling `plugins` will see `plugin_registry`, `plugin_state` and
  `marketplace_releases` rows under one heading. Whether the console's own
  model wants them split is its plan's question, not answerable here, and
  nothing in this work item depends on the answer.
- `conversations.tags_json` has no reader and no writer in this work item. It
  is added because the column list is settled and adding a column later is the
  one thing the migration shape makes cheap — but it is, today, dead.

## Rejected alternatives

**SQLite triggers instead of `record_change` at each site.** Genuinely the
cheaper step-move: zero call-site cost, and it catches a writer added later
that forgets. Rejected because a trigger cannot see which branch of
`INSERT ... ON CONFLICT DO UPDATE` ran, and six of our eighteen write sites are
exactly that statement — so `created` and `changed` would collapse into one
word, and B10's whole point is that an observer can tell what happened. A
second reason: a trigger is schema the migration map does not describe, which
breaks requirement 27.

**A `Changes` table per noun.** Cheap to query per noun, and it destroys the
single ordered cursor that P3 is about. One table, one `AUTOINCREMENT`.

**Deriving `updated_at` and `version` from the ledger.** Correct, and the right
instinct under guideline 4 — rejected in § Design's state inventory because the
inventory read has to stay cheap and a `MAX(cursor)` group-by over a growing
log is not. The two are written in one statement, so they cannot drift.

**hermes's refcounted per-path connection registry**
(`plugins/memory/holographic/store.py:112`). Declined: it solves many
independently-constructed store objects sharing one connection, and we
construct exactly one. A registry of one is what `CLAUDE.md` names as a seam
that has not earned its cost.

**hermes's bounded LIFO read pool** (`hermes_state.py:387`). Declined *for
now*, and named as the upgrade path rather than dismissed. It is the right
answer once readers are hot; thread-local readers with no strong registry are
the smaller thing that works, and the failure that forced their pool was caused
by the strong registry we are not building.

**A wall-clock upper bound as the concurrency assertion.** The intent brief
originally asked for two turns finishing inside 300 ms. Rejected against
`testing-conventions`: *"timing tests must not assume a quiet machine… no
assertions that depend on something not happening within a short window."* The
barrier proves the same property deterministically and proves more of it — that
the two turns were *simultaneously* inside the model call, not merely fast. The
numbers are still measured and still reported, because they are what a person
reads to believe it; they are no longer what the suite depends on. Put to the
author and agreed before this spec was written.

**Building the disabled-plugin filter later, with H24's disable verb.** Put to
the author and declined: the columns land here, so H24 should add only the
verb. The cost is a filter with no reachable `true` case until then, tested by
writing a `disabled` row by hand.

**Splitting this into three work items.** Put to the author and declined; the
reason is recorded in `intent.md`'s trace.

## Concerns

Five, in the order a reviewer should look at them.

**The noun map is the weakest thing here, and it is load-bearing.** Requirement
19 forbids `ledger.py` importing any store, so the mapping from a plural noun
to the tables behind it is a data table inside `ledger.py` that duplicates
knowledge those modules already have. Nothing checks that the duplicate stays
true: a table renamed in `memory_store.py` leaves `ledger.inventory()`
returning `sqlite3.OperationalError` at runtime, not at import, and not in the
module that changed. The alternative — each store answering for its own
inventory — reverses the import direction the intent's constraint fixed, and is
worse. The mitigation is a test that asserts every noun's SQL actually runs
against a fresh store; that catches a rename the next time the suite runs,
which is the best this shape allows. This is the single place a reviewer should
push hardest.

**Three nouns cover more than one table, and one of them strains.** `plugins`
covers the registry, the installed state and the marketplace releases, because
`NOUNS` is closed and none of the other twelve fits a release. The union keeps
ids resolvable, so nothing breaks; but a console page called "plugins" that
receives three kinds of row is a surprise the console's plan has not agreed to,
and § Open questions records it as theirs to answer. If the closed list is
wrong, this work item is where it is cheapest to say so.

**The legacy fill is a Python loop over every message row, inside one
transaction, at first open.** Each row needs its own minted id, so there is no
single `UPDATE` that does it. A store with tens of thousands of messages pays
tens of thousands of `uuid7()` calls and one `executemany` at the first start
after this lands, holding the write lock throughout. It happens once and the
`PRAGMA table_info` guard makes every later open a no-op, so the cost is
bounded and one-time — but a person upgrading a long-lived store will see a
pause at startup with nothing explaining it, and this project has no
progress-reporting anywhere to explain it with. The honest mitigation is a log
line before and after, which is what is planned.

**`ids.py` is called pure in the intent and is not.** It reads the clock and
carries a locked counter. `CLAUDE.md`'s rule is satisfied — it is its own file
— but the label is wrong and is corrected here rather than silently. The
practical consequence is that its unit test cannot be a pure-value test: the
sort-order-within-one-millisecond case has to either monkeypatch the clock or
mint enough ids fast enough to land in one millisecond, and the second is the
flaky one. Monkeypatching `time.time` inside the module under test is what
`testing-conventions` permits as faking the clock, and is what will be done.

**A tension between two `CLAUDE.md` rules that cannot both be honoured, and
which way it was resolved.** The rule "a module that touches real I/O is its
own file, separate from a block's pure-function module" wants the clock out of
`ids.py`; the rule against "a registry or dispatch seam for a family of
pluggable backends" before a second member wants no injected clock and no
`Clock` protocol. Injecting a clock into `uuid7()` would satisfy the first and
violate the second, and would push the impurity onto eighteen call sites
instead of one. The first rule was followed in its letter — `ids.py` is its own
file, sharing with nothing — and its spirit was traded away for the second.
The cost is that `ids.py` is untestable without faking a module-level clock,
which is a cost paid once, in one test file.

Beyond those five: the design was examined for a tension between
`testing-conventions` and the intent's own acceptance test and found one, which
is resolved in § Rejected alternatives with the author's agreement rather than
silently. That is the only policy conflict found, and it is settled.

---

**A rule this spec would bind beyond this work item**, for approval before it
is written anywhere:

> - A thread-local SQLite reader connection is never also held in a
>   process-lifetime collection: the thread-local is the only reference, so the
>   connection dies with its thread. A set kept "so they can be closed" is the
>   descriptor leak `hermes_state.py:4818-4845` names; the upgrade path is a
>   bounded pool, never a strong registry.

If you approve it, it belongs in `CLAUDE.md`'s `## Please do` list as that one
line.
