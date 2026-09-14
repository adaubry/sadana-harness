# Review: cron schedules replace interval-only triggers (from plan.md 2026-09-14)

Reviewed: ee04b63..HEAD (uncommitted working tree) — 18 files, +2754/-7 (after fixing the six Important findings below; the cold review itself was run against the pre-fix 17-file, +2432/-7 diff, and each finding's own reproduction was verified against that state)
Reviewer context: fresh session — this review was run with no prior context beyond this task's own artifacts and diff (a genuinely cold review, not a same-session continuation).
Second opinion: none — ran during build (self-check), not repeated here by design.

## Evidence

```
$ make verify
docs/tasks/H27-door-nouns-schedules: all present artifacts valid
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
Success: no issues found in 77 source files
TYPES OK
▶ hermetic run: TZ=UTC LANG=C.UTF-8 PYTHONHASHSEED=0, empty env
.................................................................
  two conversations, concurrently: 320 ms for two 200 ms turns
.  one conversation, twice: 461 ms for two 200 ms turns
...... [  5%]
........................................................................ [ 10%]
........................................................................ [ 15%]
........................................................................ [ 20%]
........................................................................ [ 25%]
........................................................................ [ 30%]
........................................................................ [ 35%]
........................................................................ [ 41%]
........................................................................ [ 46%]
........................................................................ [ 51%]
........................................................................ [ 56%]
........................................................................ [ 61%]
........................................................................ [ 66%]
........................................................................ [ 71%]
........................................................................ [ 76%]
........................................................................ [ 82%]
........................................................................ [ 87%]
........................................................................ [ 92%]
........................................................................ [ 97%]
...................................                                      [100%]
1403 passed in 140.24s (0:02:20)
TESTS OK
VERIFY OK
```

## Findings

This is a fresh session with no prior context on this work item beyond `intent.md`, `spec.md`, `plan.md` and the diff — that satisfies the cold-review requirement on its own. No other limitation was hit: `make verify` ran to completion, and every file in the diff was read in full, including `cron.py`, `conversation_store.py`'s new section, `scheduling.py`'s new loop, `door/nouns/schedules.py`, both new test files, the extended `test_scheduling.py`/`test_client_surface.py`, and `docs/console/nouns.md`. Two suspected bugs below were confirmed by running small reproductions against the actual code rather than reasoning about them from the diff alone.

**Scope reconciliation against `plan.md` § Files that change:** every file the plan named is touched. One file the plan did not name is touched: `CLAUDE.md` (see Important, below). `tests/unit/test_client_surface.py` was already flagged in the plan itself as "added during implementation," so that one is not a surprise.

**Compliance pass, checked item by item:**
- Requirements 1-2 (`cron.py`): `tests/unit/test_cron.py` covers every operator on every field, the day-field OR semantics, one real DST transition each direction, and the four-year search ceiling — matches plan.md § Proof for this file exactly.
- Requirement 3 (migration): `tests/unit/test_conversation_store_schedules.py::test_adopt_scheduled_triggers_copies_every_legacy_row_once` and `..._is_idempotent_on_a_second_open`, plus `test_client_surface.py::test_open_runtime_migrates_legacy_scheduled_triggers_into_schedules`, discharge this — except for one gap, see Important below.
- Requirements 4, 10 (CRUD + capability gating): present in `door/nouns/schedules.py` and registered in `subcommands/door.py`/`capabilities.py`; `test_schedules_write_is_declared` discharges the registration half.
- Requirement 5 (404 not 403): `get_schedule`/`update_schedule`/`remove_schedule`/`set_schedule_state` all filter `WHERE id = ? AND account_key = ?`, and `door/nouns/schedules.py` returns `NOT_FOUND` when the read comes back empty on every verb — correct by inspection, but see the Important finding below about what's actually tested.
- Requirement 6 (resume recomputes, never reuses stale `next_run_at`): `act()`'s `resume` branch calls `scheduling.next_schedule_run(row, now=now)` and never reads `row.next_run_at` — matches, and `test_resume_sets_next_run_at_and_reactivates` proves it.
- Requirement 7 (no catch-up): `_advance` and `cron.next_after` both compute from `now`, never from a backlog; `test_tick_fires_a_due_cron_schedule_exactly_once_under_an_injected_now` proves a schedule doesn't double-fire.
- Requirement 8 (every write ledgered): every one of `create_schedule`/`update_schedule`/`set_schedule_state`/`remove_schedule`/`advance_schedule` calls `ledger.record_change` inside its own `write_txn` — verified by reading all five, and by `test_create_writes_a_ledger_created_row`/`test_remove_writes_a_ledger_deleted_row_then_deletes`.
- Requirement 9 (per-conversation lock, not process-wide): the schedules loop in `tick()` calls `gateway_dispatch.handle_inbound` the same way the pre-existing trigger loop does, holding no lock of its own — matches.
- Requirement 11 (`nouns.md` + conformance suite): both done.
- Rejected alternatives: no `croniter` import anywhere in the diff, no JSON-file storage, no per-account `name` scoping snuck in (still box-wide `UNIQUE`, as the DDL was given) — none of the three declined alternatives crept back in.
- Design principle 1 (learn from the reference first): `spec.md` names exactly what was adopted and declined from `SCHEDULING`'s `cron/jobs.py`, with line numbers; the diff matches what it describes.
- Design principle 4 (least step-cost): `tick()` gained one loop rather than a second background thread — matches, and `subcommands/gateway.py` needed no change, as the spec predicted.

### Important — all six fixed, in this same branch

- **[Bugs] A migrated legacy one-shot trigger (`interval_seconds IS NULL`) becomes a permanently unfireable, permanently "due" ghost row in `schedules`.** `adopt_scheduled_triggers` (`conversation_store.py`) copies every `scheduled_triggers` row verbatim, including one-shot triggers, which by design have `interval_seconds = NULL` (see `scripts/prove_gateway_scheduling_e2e.py:73-76`, and the existing `due_triggers` loop's own `if trigger.interval_seconds is None: delete_scheduled_trigger(...)` one-shot-then-delete behavior in `scheduling.py:116-120`). The migrated `schedules` row for such a trigger has both `cron = NULL` and `interval_seconds = NULL`. I reproduced this directly: seeding one such legacy row, running `adopt_scheduled_triggers`, then calling `scheduling.next_schedule_run` on the migrated row raises `ValueError: schedule ... has neither cron nor interval_seconds set`. In `scheduling.tick`, that `ValueError` is caught and the schedule is `continue`d without ever being advanced or removed (`scheduling.py`'s new loop, the `except ValueError` branch) — so it stays in `due_schedules`'s result set forever, and every future tick (default ~30s) logs `"scheduled %r has an unfireable cadence, skipping"` for it indefinitely. The legacy trigger itself still fires correctly through the untouched `due_triggers` loop and gets deleted from `scheduled_triggers`, but the `schedules` copy is an orphaned, permanently-broken-looking entry a console account would see via `list`/`get` (stuck `next_run_at` in the past, `last_run_at`/`last_state` forever null) — the opposite of what intent.md promises ("can see when it last ran, what happened, and when it will run next"). No test in the diff exercises a migrated row through `tick()`/`next_schedule_run`; `test_adopt_scheduled_triggers_copies_every_legacy_row_once` only asserts the copy's columns, not what happens when it's ticked.

  **Fixed:** `adopt_scheduled_triggers` now excludes `interval_seconds IS NULL` rows from the migration query entirely — a one-shot trigger has no representable shape in `schedules` (no "fire once" concept exists there), so it stays exclusively in `scheduled_triggers`, keeps firing through the untouched `due_triggers` loop exactly as before, and never gets a broken `schedules` counterpart. `test_adopt_scheduled_triggers_copies_every_recurring_legacy_row_once` (renamed from the old test, now using a genuinely recurring `interval_seconds`) and a new `test_adopt_scheduled_triggers_skips_one_shot_legacy_rows` cover both directions.

- **[Bugs] `update` doesn't catch the same `sqlite3.IntegrityError` `create` catches, so renaming a schedule to a name already in use returns `500 INTERNAL` instead of `409 CONFLICT`.** `spec.md`'s Interface section lists `name` as an accepted field on both `create` and `update`. `door/nouns/schedules.py::create` wraps its `conversation_store.create_schedule` call in `try/except sqlite3.IntegrityError` and returns `409 CONFLICT` (line ~209); `update` calls `conversation_store.update_schedule` with no such guard (line ~271), and `update_schedule`'s raw `UPDATE ... SET name = ?` hits the same `UNIQUE NOT NULL` constraint on `schedules.name`. I reproduced this directly: creating two schedules named `a` and `b`, then calling `update_schedule(..., id=b.id, name="a")` raises an uncaught `sqlite3.IntegrityError`. `door/router.py::handle`'s blanket `except Exception` turns that into a generic `500` Problem rather than a crash, but it is still the wrong status for a validation-shaped conflict the door already knows how to report correctly on the sibling verb.

  **Fixed:** `update()` now wraps its `conversation_store.update_schedule` call in the identical `try/except sqlite3.IntegrityError` → `409 CONFLICT` shape `create()` already had. `test_update_with_a_name_collision_is_409_not_500` reproduces the exact scenario above and asserts `409`.

- **[Compliance] `plan.md` § Proof's cross-account-404 item is only discharged for `get`.** The plan states: *"another account's schedule id on `GET`/`update`/`remove`/`pause`/`resume` returns `404`"* (also stated in `spec.md` § Acceptance criteria). `tests/contract/nouns/test_schedules.py::test_another_accounts_schedule_is_404_not_403` exercises only `GET`. By code inspection `update`/`remove`/`act` all use the same `get_schedule(..., account_key=...)` scoping and would behave the same, but the plan's own proof item names all five verbs and only one is actually tested.

  **Fixed:** `test_another_accounts_schedule_is_404_on_every_mutating_verb`, parametrized over `PATCH`/`DELETE`/`pause`/`resume`, added — all four now proven 404, alongside the existing `GET` case.

- **[Compliance] `plan.md` § Proof's capability-missing item is only discharged for `create`.** The plan states: *"`create`/`update`/`pause`/`resume` without `schedules.write` declared return `501 HARNESS_CAPABILITY_MISSING`"*. `tests/contract/nouns/test_schedules.py::test_create_without_schedules_write_is_capability_missing` exercises only `create`. `update` and the `pause`/`resume` actions both gate on the same capability (`_require_write` / `ActionSpec.capability`), but three of the four named verbs have no test proving it.

  **Fixed:** `test_update_and_pause_without_schedules_write_are_capability_missing` (parametrized over `PATCH`/`pause`) and `test_resume_without_schedules_write_is_capability_missing` added — `resume` needed its own setup (pausing first through a full-capability context) since `router.py` checks state before capability, and a `resume` against a still-`active` row would hit the state gate (`409`) before ever reaching the capability check the test means to prove.

- **[Compliance] `CLAUDE.md` is modified and was not named in `plan.md` § Files that change.** The diff adds a new "Please do" bullet generalizing the plugin_id/conversation_id "names, not pointers" convention project-wide. Per this stage's own methodology, a file touched that the plan did not name is an Important finding regardless of how small or reasonable the change is — the artifact chain should have said this file would change, and it did not.

  **Fixed:** `plan.md` § Files that change now lists `CLAUDE.md`, with a note on why it was missed initially.

- **[Compliance] `spec.md`/`plan.md`'s stated design for `last_state` assumed H20 already exists and already provides a harness-exit-reason-to-console-five-value mapping to reuse; H20 has not been built, and the shipped code silently substitutes a two-value `"completed"`/`"failed"` mapping instead.** `spec.md` § Design says *"`_map_exit_reason` reuses whatever mapping H20 already introduced for the harness's eight-value `exit_reason` down to the console's five (wire.md §6) — this work item does not invent a second mapping."* `plan.md` § Risks repeats the same assumption. There is no `docs/tasks/H20-*` directory in this repo (confirmed by listing `docs/tasks/`), and no such mapping function exists anywhere in `src/sadana/` (confirmed by grep). `scheduling.py`'s actual code (`last_state = "completed" if ok else "failed"`) is a reasonable, well-commented stand-in given that reality — the comment even names why — but it is a real, uncorrected deviation from what both artifacts describe, and it reintroduces exactly the information loss `docs/console/wire.md` §6 calls out as the reason the five-vs-eight mapping matters: a schedule that failed because of `budget_exhausted`, `wall_clock_exhausted`, `interrupted`, or a real provider failure all render identically as `"failed"` on the wire, with no way for a console account to tell them apart. Neither `spec.md` nor `plan.md` was updated to reflect that H20 doesn't exist yet, so the artifact chain still describes a mechanism the code doesn't use.

  **Fixed (documentation only — the code itself was already the reasonable stand-in described above, and stays):** `spec.md` § Design and `plan.md` step 4 both now carry an explicit correction stating H20 doesn't exist, that `handle_inbound` only exposes `(ok, text)`, and that the `"completed"`/`"failed"` mapping is a documented placeholder pending H20's real one — not a silent gap.

### Nits

- [Bugs] `door/nouns/schedules.py::search_doc`'s subtitle falls back through `interval_seconds` with a bare truthiness check (`f"every {interval_seconds}s" if interval_seconds else None`); an `interval_seconds == 0.0` row (never legitimately created today, but not rejected by any validation either) would silently render no subtitle instead of "every 0.0s". **Fixed** — changed to `is not None`.
- [Bugs] `_resolve_plugin` calls `plugin_manifest.discover_plugins()` — which re-validates and re-imports every installed plugin's body modules — as the entire mechanism for checking one string against a name list, on every `create`/`update` request that names a `plugin_id`. **Not fixed** — a real inefficiency but not a correctness issue, and a lighter-weight "does this name exist" check would need its own small work item to design properly rather than a quick patch here.

## Evidence (re-run after fixes)

```
$ make verify
docs/tasks/H27-door-nouns-schedules: all present artifacts valid
CHAIN OK
... (lint hooks all Passed) ...
LINT OK
Success: no issues found in 77 source files
TYPES OK
▶ hermetic run: TZ=UTC LANG=C.UTF-8 PYTHONHASHSEED=0, empty env
... [100%]
1412 passed in 132.63s (0:02:12)
TESTS OK
VERIFY OK
```

## Decision

Approved by Adam, 2026-09-14, with all six Important findings (two bugs,
three missing-coverage gaps, and the CLAUDE.md/H20 artifact-drift items)
fixed in this branch before merge; the two Nits were triaged (one fixed,
one left as a noted future concern) rather than blocking.
