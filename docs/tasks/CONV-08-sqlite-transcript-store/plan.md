# Plan: A conversation survives the process that was running it (from intent.md 2026-09-04)

## Files that change

- `docs/tasks/CONV-08-sqlite-transcript-store/spec.md` — amended before
  this plan was written: schema changed from a single key→JSON-blob table
  to two tables (`conversations`, `messages`), per an explicit instruction
  to conform to `docs/reference/conversation_block_blueprint.md` §4.1/§7
  as closely as possible. `scripts/artifact.py check` re-run and green
  after the edit.
- `src/sadana/conversation_store.py` (new) — the whole module: DDL,
  `write_txn()`, `ConversationAlreadyExists`, `ConversationNotFound`,
  `store_path_from_config()`, `open_store()`, `create()`, `save()`,
  `load()`, `bind_persist()`.
- `tests/unit/test_conversation_store.py` (new) — this module's own unit
  tests, per `testing-conventions`' `tests/unit/test_<module>.py` naming.
- `src/sadana/config.py` — no code change; `config.env_path` already
  exists and is reused as-is for `SADANA_CONVERSATION_STORE_PATH`.
- `CLAUDE.md` — already amended this session (the I/O-gets-its-own-module
  rule); no further change in this stage.

## Order of work

1. **Schema + connection plumbing.** `conversation_store.py`'s DDL (both
   `CREATE TABLE IF NOT EXISTS` statements from spec.md §Schema),
   `open_store(path)` (creates parent dirs if needed, opens the
   connection, runs the DDL, sets `PRAGMA journal_mode=WAL`), and
   `write_txn(conn)` — adapted from `hermes-agent/hermes_cli/sqlite_util.py:31-49`
   (`BEGIN IMMEDIATE` / yield / `COMMIT`, guarded `ROLLBACK` in the
   `except` clause so a SQLite auto-rollback under lock contention can't
   shadow the real exception). Audited before adapting: stdlib-only
   (`contextlib`, `sqlite3`), ~19 lines, no hidden dependency on hermes's
   own state/config machinery — safe to adapt near-verbatim with a
   docstring citation rather than reinvent, per CLAUDE.md's "verbatim
   copy" rule (audit, don't blind-paste).
   No caller yet — same "lands with nothing calling it" posture every
   prior CONV-item used for its first step.
2. **`create()` / `save()` / `load()`.** Built directly on step 1's
   transaction helper. `create()`: one `INSERT` into `conversations`
   inside a `write_txn`, catching `sqlite3.IntegrityError` on the primary
   key and re-raising `ConversationAlreadyExists`; then one `INSERT OR
   IGNORE` per message into `messages`, same transaction. `save()`: same
   shape but `INSERT ... ON CONFLICT(key) DO UPDATE SET ...` for the
   `conversations` row (never raises for an existing key), same `INSERT OR
   IGNORE` loop for `messages`. `load()`: `SELECT` on `conversations`
   (raise `ConversationNotFound` if no row), `SELECT ... ORDER BY msg_seq`
   on `messages`, reassemble the `Conversation` — including undoing the
   `wall_clock_remaining_s` → `WallClockBudget(deadline=now + remaining)`
   conversion, and parsing `tool_surface_json`/`tool_calls_json` back into
   tuples (JSON round-trips a tuple as a list; every such field is
   re-wrapped in `tuple(...)` on the way out, since `Conversation` and
   `Message` both declare tuple-typed fields).
3. **`bind_persist()`.** The adapter matching `run_turn`'s existing
   `persist: Callable[[tuple[Message, ...]], Awaitable[None]]` parameter
   exactly (`conversation.py:708`). Wraps `save()` in `asyncio.to_thread`
   — same wrapping precedent C6's `complete()` already established
   (`conversation.py:501`) for putting a sync call behind an async
   interface. Built last because it's the one piece with a concrete
   existing caller shape to match: `test_run_turn_persistence_failed`
   (`tests/unit/test_conversation.py:733`) is the behavioural contract a
   real backing store must also satisfy, so steps 1-2 need to already work
   before this step's own test can mean anything.
4. **Tests, alongside each step above** (not batched at the end):
   - With step 1: `open_store()` creates the file and both tables;
     `write_txn` commits on success and rolls back on a forced failure
     raised inside the `with` block, leaving the file's pre-transaction
     content unchanged (`tests/unit/test_conversation_store.py`, per
     `testing-conventions` — a real tmp-path sqlite file, no network, no
     source-reading).
   - With step 2: `create()` then `create()` again on the same key raises
     `ConversationAlreadyExists` and the original row is untouched;
     `save()` on an owned key succeeds repeatedly; `load()` round-trips
     every `Conversation` field (including message order and count, and a
     `WallClockBudget` saved with N seconds remaining loading back with
     `deadline == load_now + N`); `load()` on an unseen key raises
     `ConversationNotFound`; re-saving with an already-stored `msg_seq` is
     a no-op, not a duplicate row or an error (spec.md's added acceptance
     criterion); a save forced to fail mid-`write_txn` (patch the
     `messages` insert to raise after the `conversations` upsert already
     ran) leaves **both** tables exactly as they were before the save —
     the riskiest single test in this plan, because a two-table write has
     a failure mode (one table committed, the other not) a single-table
     blob write never had; it lands after create/save/load already work
     normally on the happy path, so a failure here is unambiguously about
     atomicity, not a save/load bug wearing an atomicity costume.
   - With step 3: `bind_persist()` wired as `run_turn`'s real `persist`
     argument, using a store whose connection is closed before the call —
     a genuine failure, not a monkeypatched stub — reproduces
     `exit_reason == ExitReason.PERSISTENCE_FAILED` with the tool handler
     never invoked, matching `test_run_turn_persistence_failed`'s existing
     assertion shape but against the real implementation.
5. `/ponytail-review` and `/simplify` self-check against the diff; sort
   findings into worth-taking-now / later work item / already-settled per
   build-skill, act on the first bucket only.
6. `make verify`; paste the full output.

## Risks

**What could this break that already works?** Nothing at the turn-loop
level: `run_turn`/`take_turn` are not touched (spec.md requirement 9), and
`_noop_persist` remains the default for every existing caller that never
opts into a real store — `test_run_turn_persistence_failed` and every
other existing test in `tests/unit/test_conversation.py` keeps using the
fake stub unchanged; this plan only *adds* a second, real-store variant of
that same assertion, in a new test file, never touching the old one. No
other module imports anything from `conversation_store.py` yet (by
design — spec.md's own "growth as addition" reasoning), so nothing
existing can regress from a bad import or a name collision.

**Which step is riskiest, and why that one?** Step 4's forced
mid-transaction-failure test, for the reason stated inline above: this
plan's schema (two tables, per the blueprint-conformance revision) has a
real atomicity hazard a single-blob design would not have had — a
`conversations` upsert that commits while its `messages` inserts do not,
or vice versa. It is ordered after create/save/load already prove
themselves on the untroubled path (step 4's own earlier sub-bullets), so
this specific test isolates the one new risk this revision introduced
rather than re-discovering a save/load bug under a different name.

**Which options did spec.md already reject, and is this plan drifting
toward one?** Re-checking spec.md's current `## Rejected alternatives`
(post-amendment) against this plan: declining hermes's incremental
per-message `append_message()` write pattern in favour of writing the
*whole* `messages` tuple's still-missing rows every `save()`/`bind_persist()`
call — this plan's step 2/3 design does exactly that (an `INSERT OR
IGNORE` loop over every message, not just the newest one), consistent
with the amendment rather than drifting back toward hermes's
one-row-at-a-time model. Declining a re-supplied-template dependency on
`load()` — this plan's `load()` signature (step 2) takes only `conn`,
`key`, `now`, no template argument, consistent. Declining WAL fallback
detection — step 1's `open_store()` sets a bare `PRAGMA journal_mode=WAL`
with no NFS/version-probing logic, consistent.

## Proof

- `scripts/artifact.py check` clean for both the amended `spec.md` and
  this `plan.md`.
- `tests/unit/test_conversation_store.py` passing, covering every bullet
  in step 4 above: schema/transaction primitives, create/save/load
  round-trip and failure modes (unknown key, duplicate create, duplicate
  message no-op), the forced-mid-transaction-failure atomicity test, and
  `bind_persist()` reproducing `PERSISTENCE_FAILED` against a real
  failure.
- The self-check (`/ponytail-review` + `/simplify`) output and what was
  taken from it, reported alongside the `make verify` run.
- `make verify` output pasted in full, ending `VERIFY OK`.
