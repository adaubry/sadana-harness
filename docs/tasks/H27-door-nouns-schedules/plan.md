# Plan: cron schedules replace interval-only triggers (from intent.md 2026-09-14)

Author: adam aubry (operator). Status: approved.

## Files that change

- `CLAUDE.md` — one added "Please do" bullet generalizing the `plugin_id`/
  `conversation_id` names-not-pointers convention project-wide (added
  during implementation, approved by the user in conversation before the
  edit; recorded here because a deploy-stage cold review correctly flagged
  that this file's own edit was never added to this list at the time).
- `src/sadana/cron.py` (new) — pure five-field cron parser + `next_after`.
- `tests/unit/test_cron.py` (new) — every operator, OR day-semantics, one
  DST transition, the four-year search ceiling.
- `src/sadana/conversation_store.py` — `schedules` table DDL (verbatim from
  the work item brief), `ScheduleRow` dataclass, `create_schedule`,
  `get_schedule`, `list_schedules`, `update_schedule`, `set_schedule_state`,
  `remove_schedule`, `due_schedules`, `advance_schedule`,
  `adopt_scheduled_triggers`.
- `tests/unit/test_conversation_store_schedules.py` (new — the existing
  `test_conversation_store.py` is already 1045 lines; schedules is a
  distinct table/concern, so it gets its own suffixed file rather than
  pushing that one past 1100+ lines, matching testing-conventions' own
  "large enough → suffix" rule) — CRUD, account scoping, migration
  idempotency, tombstone-then-delete.
- `src/sadana/client_surface.py` — `_adopt_scheduled_triggers(conn)`, called
  from `open_runtime()` beside `_adopt_scheduled_memories`, same
  try/except-log-continue shape.
- `tests/unit/test_client_surface.py` — one added test proving the
  migration through `open_runtime()` itself, matching this file's own
  `test_open_runtime_*` shape (added during implementation; not in the
  plan's original file list).
- `src/sadana/scheduling.py` — `tick()` gains `now: float | None = None`
  and a second due-loop for `schedules`, alongside the existing
  `due_triggers` loop.
- `tests/unit/test_scheduling.py` — extend with schedule-firing cases: fires
  once per match under an injected `now`, paused never fires, `last_run_at`/
  `last_state` recorded, per-row failure isolation.
- `src/sadana/door/nouns/schedules.py` (new) — the six verbs + `search_doc`.
- `src/sadana/door/capabilities.py` — append `"schedules.write"` to
  `DECLARED`.
- `src/sadana/subcommands/door.py` — import `schedules`, add to
  `nouns={...}`.
- `docs/console/nouns.md` — fill in `## schedule`.
- `tests/contract/nouns/test_schedules.py` (new) — conformance proof against
  `router.handle()`, matching `test_approvals.py`'s shape: create → get →
  pause → resume → list filtered by state → cross-account 404 → bad-cron
  validation.

## Order of work

1. **`cron.py` + its tests.** Zero dependents yet, so it's provable in
   total isolation before anything else touches it. `parse`: tokenize each
   of the five fields into `frozenset[int] | None` (`None` = `*`),
   supporting comma lists, `a-b` ranges, `*/n` and `a-b/n` steps. `Cron`/
   `CronError` as frozen dataclasses. `next_after`: outer loop over
   candidate calendar days (bounded at 4 years), matching
   `day_of_month`/`month`/`day_of_week` with OR semantics when both day
   fields are restricted; inner loop over the day's matching
   `(hour, minute)` pairs; every candidate converted to a `zoneinfo`-aware
   `datetime` and compared by `.timestamp()`. Runnable/testable state after
   this step: a fully correct, independently verified cron engine.

2. **`conversation_store.py` schedule accessors + their tests.** Add the
   DDL to the existing schema block (a comment naming H27, matching the
   GATEWAY-DAEMON-02 comment style already there for `scheduled_triggers`).
   `ScheduleRow` mirrors the table 1:1. Read functions (`get_schedule`,
   `list_schedules`, `due_schedules`) first, then write functions
   (`create_schedule`, `update_schedule`, `set_schedule_state`,
   `remove_schedule`, `advance_schedule`), each wrapped in `write_txn` and
   each writing its own `ledger.record_change` call (created/changed/
   deleted) before returning — requirement 8. `get_schedule`/
   `list_schedules`/`update_schedule`/`remove_schedule`/`set_schedule_state`
   all filter `WHERE id = ? AND account_key = ?` (or the account-only
   equivalent for `list`), the same shape `memory_store.get_entry_by_id`
   already uses — cross-account access simply finds no row, which is what
   makes the door's later 404 nearly free. `create_schedule` takes explicit
   `next_run_at` (computed by the caller via `cron.next_after`, never
   inside this file — `conversation_store.py` stays cron-agnostic) and
   raises `sqlite3.IntegrityError` on a duplicate `name`, uncaught here —
   the door noun catches it. `adopt_scheduled_triggers(conn, *, owner, now)`
   reads every `scheduled_triggers` row, skips any whose `name` already has
   a `schedules` row, and calls `create_schedule` for the rest with
   `cron=None`, `interval_seconds=<carried over>`,
   `next_run_at=<carried over, not recomputed>`, `account_key=owner` — one
   function serving both the door's create path and the migration path.
   Runnable/testable state after this step: every schedule accessor
   provable against a real (tmp) SQLite file, with no cron or door
   dependency yet.

3. **`client_surface.py`'s migration call.** One line
   (`_adopt_scheduled_triggers(connections.writer)`) beside the existing
   `_adopt_scheduled_memories` call in `open_runtime()`, plus the same
   try/except `sqlite3.Error` → log-and-continue wrapper function.
   Runnable/testable state: opening a store with legacy
   `scheduled_triggers` rows now populates `schedules` too, provable via
   `client_surface.open_runtime()` in a test that seeds `scheduled_triggers`
   first.

4. **`scheduling.py`'s second due-loop, and its tests.** `tick` gains
   `now: float | None`; the existing `due_triggers` loop is untouched, and a
   new loop calls `conversation_store.due_schedules(reader, now=now)`,
   fires each via the same `gateway_dispatch.handle_inbound` bridge
   (`account=row.account_key`, per-row try/except matching the existing
   trigger loop's own isolation), computes the next `next_run_at`
   (`cron.next_after` when `row.cron is not None`, else
   `now + row.interval_seconds`), and calls `advance_schedule` with
   `last_run_at=now` and `last_state` mapped from the turn's exit reason
   through whatever mapping H20 already introduced for wire.md §6 —
   **wrong, corrected at Deploy**: no such mapping exists (H20 hasn't been
   built), `handle_inbound` exposes only `(ok, text)`, and the shipped code
   uses `"completed" if ok else "failed"` instead; see spec.md § Design's
   own correction. This is the riskiest step (below) and lands after both
   its dependencies (`cron.py`, the store accessors) are already
   independently proven.

5. **`door/nouns/schedules.py` + its capability + registration + tests.**
   The noun module itself (list/get/create/update/remove/act/search_doc),
   following `memory_entries.py`'s exact shape for account-scoped CRUD and
   `approvals.py`'s exact shape for actions. `create`/`update` manually
   check `"schedules.write" in ctx.capabilities` (the router only auto-gates
   *actions* by capability, not the generic verbs — confirmed by reading
   `door/router.py`'s own capability check, which only fires inside the
   action-dispatch path); `pause`/`resume` get it for free via
   `ActionSpec(capability="schedules.write", ...)`. `create` validates
   `cron` via `cron.parse` and `timezone` via `zoneinfo.ZoneInfo`,
   collecting both as separate `errors[]` entries when both are bad;
   resolves `plugin_id` against `plugin_manifest.discover_plugins()` and
   `conversation_id` against `conversation_store.exists()`, per spec.md's
   "names, not pointers" section. Then: `capabilities.py`'s one-line
   `DECLARED` append, `subcommands/door.py`'s one-line registration,
   `nouns.md`'s fill-in, and the conformance test file. Runnable/testable
   state: the full wire surface, provable end-to-end through
   `router.handle()` with no real socket, matching every other noun's own
   conformance test.

6. **`make verify`.**

## Risks

**What this could break, named.** `scheduling.tick`'s signature gains a
keyword-only `now` with a default, so `run_tick_loop`'s existing call
(`tick(runtime)`) is unaffected — no caller needs to change. The
`scheduled_triggers` table and its accessors (`due_triggers`,
`upsert_scheduled_trigger`, `advance_scheduled_trigger`,
`delete_scheduled_trigger`) are untouched; `test_scheduling.py`'s existing
trigger-loop tests keep passing unmodified since that loop's own code
doesn't move. `client_surface.open_runtime()` gains one more best-effort
migration call in the same try/except shape as the existing one, so a
store that fails to migrate schedules still starts (same posture, same
risk already accepted for memory adoption). The one shared piece of state
two things now touch is `runtime.connections.reader()`/`.writer` inside one
`tick()` call — already safe under H16's model, and this adds a second
read + up to N writes per tick, not a new access pattern.

**The riskiest step is 4 (the tick loop), because it's the one place cron
computation, store writes, and the existing best-effort/per-item-isolation
contract all meet at once — a bug here either fires a schedule twice, never
advances it (so it fires every 30 seconds forever), or lets one broken
schedule's exception escape and kill the whole tick (as it would have for
`due_triggers` before H16 wrapped every direct touch narrower). It lands
after step 1 (cron correctness proven alone) and step 2 (store correctness
proven alone), so by the time it's written, the only genuinely new logic in
it is the wiring — which of the two firing rules applies to a given row,
and the try/except boundary — not cron math or SQL, both already provable
in isolation. Its own tests inject `now` directly (never monkeypatch
`time.time`), matching testing-conventions' "environment as data" rule and
the fake-clock cron-fires-once-per-match requirement literally.

**Where the spec's own Rejected alternatives could quietly creep back in.**
Two watch points, both named in spec.md: (a) `plugin_id`/`conversation_id`
resolution must not grow into a new ledger-id lookup for plugins — spec.md
declined that in favor of treating `plugin_id` as the plugin's own `name`;
step 5's validation must call `plugin_manifest.discover_plugins()` directly
and never invent an id-minting helper for plugins. (b) `next_after`'s
search must not reach for `croniter` even as a "just to double check"
cross-validation in tests — spec.md declined the dependency outright, and a
test importing it would be adopting exactly what the spec's own Design
section argued against.

## Proof

- `tests/unit/test_cron.py`: `parse` accepts `*`, a comma list, a range, and
  a step on each of the five fields (one assertion set per field, not one
  giant expression); rejects an out-of-range value and an empty field with
  a `CronError`, not an exception. `next_after` covers: a plain daily time,
  a weekday-list schedule exercising the OR semantics when both day fields
  are restricted, a schedule computed across one real DST transition
  (`America/New_York`'s spring-forward or fall-back, whichever date is
  simplest to hardcode) asserting monotonically increasing epoch results
  across the boundary, and a calendrically-impossible expression
  (`day_of_month={31}, month={2}`) raising within the four-year bound
  rather than hanging.
- `tests/unit/test_conversation_store_schedules.py`: `create_schedule` then
  `get_schedule` round-trips every column; a duplicate `name` raises
  `sqlite3.IntegrityError`; `get_schedule`/`list_schedules` with a
  mismatched `account_key` return `None`/empty; `set_schedule_state` to
  `paused` then `due_schedules` at any `now` excludes the row;
  `remove_schedule` writes a `ledger` `deleted` row before the row is gone
  (assert via `ledger.changes_since`/direct query) and a second call is a
  no-op; `adopt_scheduled_triggers` called twice against the same
  `scheduled_triggers` rows produces exactly one `schedules` row per name
  each time (idempotent).
- `tests/unit/test_scheduling.py` (extended): a due, active, cron-based
  schedule fires exactly once when `tick(runtime, now=T)` is called with
  `T` at the match instant, and its `next_run_at` after the tick is
  strictly greater than `T`; a paused schedule with `next_run_at <= T`
  does not fire; a schedule whose fire raises is skipped without stopping
  the rest of the tick (mirroring the existing `due_triggers` isolation
  test); `last_run_at`/`last_state` are recorded on a successful fire.
- `tests/contract/nouns/test_schedules.py`: the curl-equivalent create → get
  (asserts `next_run_at` present and ISO-8601) → pause → resume sequence
  this work item's own Deploy-stage evidence requires; a bad `cron` on
  `create` returns `400` with `errors` naming `"cron"`; a bad `cron` and a
  bad `timezone` together return both entries in one `errors` array;
  another account's schedule id on `get`/`update`/`remove`/`pause`/
  `resume` returns `404`; `create`/`update`/`pause`/`resume` without
  `schedules.write` declared return `501 HARNESS_CAPABILITY_MISSING`.
- `make verify` ends `VERIFY OK`, pasted in full.
- A curl transcript against `sadana door serve` (create → get → pause →
  resume), pasted as this work item's own Deploy-stage evidence — the one
  piece of proof that isn't a `make test` assertion, per spec.md's
  Acceptance criteria.
