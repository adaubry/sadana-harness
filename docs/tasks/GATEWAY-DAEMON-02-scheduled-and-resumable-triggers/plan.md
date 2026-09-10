# Plan: Scheduled and resumable triggers (from intent.md 2026-09-10)

## Context

GATEWAY-DAEMON-02 gives sadana two things nothing today provides: a
time-based trigger that starts a conversation with nobody asking, and a
`wait` node that lets a plugin's DAG pause mid-run and resume later when a
slow external answer arrives — the piece `plugin_blueprint.md §7.2`
deliberately deferred until a real scenario needed it. `intent.md` and
`spec.md` are both written and validated; this plan is the implementation
sequence spec.md's design already fully specifies (every function signature,
every table, every rejected alternative is already decided — this plan
orders the work and names the tests, it does not re-decide the design).

## Files that change

- `src/sadana/plugins.py` — `ResumeState` (new frozen dataclass);
  `DagResult.paused_node: str | None = None` (new field, default preserves
  every existing construction site).
- `tests/fixtures/plugins/plugin-d/` (new) — `plugin.toml`,
  `schema/plugin_d_entry.json`, `init.py`. A `call` → `wait` → `compute`
  chain (mirrors plugin-a's `call`-node shape, plugin-c's minimal schema),
  the concrete "waiting on a slow external answer" scenario from `intent.md`.
- `src/sadana/plugin_manifest.py` — `run_graph()` gains
  `resume: plugins.ResumeState | None = None`; the `wait` branch splits from
  `each` (which keeps today's `failed(...)` behavior) and instead returns a
  `paused_node`-set `DagResult`; resume mode seeds `trace`/`artifacts`/`value`/
  `current` from `resume` instead of `entry.start`/`arguments`.
  `tests/unit/test_plugin_manifest.py` — rewrite
  `test_run_graph_refuses_wait_nodes` (behavior intentionally changed, not a
  bug) into a pause-behavior test; add: resume continues past the wait node,
  a second `wait` pauses again cleanly, and one test walking `plugin-d`'s
  real fixture end to end (pause, then resume) for cross-check against the
  hand-built-`Manifest` tests.
- `src/sadana/conversation_store.py` — two new `CREATE TABLE IF NOT EXISTS`
  entries in `_SCHEMA` (`scheduled_triggers`, `plugin_pauses` — full DDL in
  `spec.md § Design #5`); `ScheduledTrigger`/`Pause` dataclasses; seven new
  functions: `upsert_scheduled_trigger`, `due_triggers`,
  `advance_scheduled_trigger`, `delete_scheduled_trigger`, `save_pause`,
  `load_pause`, `delete_pause`. No `_migrate_columns()`-style guard needed —
  new tables, not new columns on an existing one.
  `tests/unit/test_conversation_store.py` — CRUD roundtrips for both tables,
  `due_triggers` due/not-due filtering, `save_pause` upsert-on-conflict.
- `src/sadana/plugin_dispatch.py` — `dispatch()`'s `call_arguments` gains
  `_sadana_session_key` (merged last, same convention `_sadana_memory_ctx`
  already established — now a CLAUDE.md rule). `build_dispatch()` gains a
  fifth optional injected callback, `persist_pause`, awaited by `dispatch()`
  right next to `record_plugin_run` whenever a result comes back with
  `paused_node` set — **added during build**, once a self-check pass (see
  `review.md`) found that without it, nothing ever persisted the *first*
  pause a real turn produces. New `resume_paused_run(conn, conversation_key,
  payload_text, *, approve=...) -> plugins.DagResult` and module-level
  `_unsupported_ask` (`AskFn` that always returns `None` — spec.md's own
  Non-goal: a resumed walk cannot yet reach a real `ask` node); its own
  plugin/entry lookup uses `plugin_manifest.validate()` on `pause.plugin`'s
  own directory, not `discover_plugins()` (a self-check efficiency finding —
  the latter would re-validate every other installed plugin on every resume).
  `tests/unit/test_plugin_dispatch.py` — session-key seeding is present and
  wins over a same-named model-supplied argument; `resume_paused_run`
  against the shared `wait_then_summarize_installed` fixture (now in
  `tests/conftest.py`, alongside `test_gateway_dispatch.py`'s identical
  need): successful resume, plugin-no-longer-resolves failure (clears the
  pause row, returns `failed_node`), entry-no-longer-resolves failure, a
  second pause mid-resume.
- `src/sadana/scheduling.py` (new) — `_is_due(next_run_at, now) -> bool`,
  `_advance(now, interval_seconds) -> float` (pure); `async def tick(conn, *,
  plugin_set, persona, provider, model, record_turn=..., record_plugin_run=...) -> int`
  (queries due triggers, fires each via a synthetic `MessageEvent(platform="schedule", ...)`
  through `gateway_dispatch.handle_inbound()`, never raises — a per-trigger
  failure is caught, logged, skipped, and deliberately left due so it
  retries on the next tick rather than being silently advanced past);
  `run_tick_loop(conn, *, interval_seconds, plugin_set, persona, provider,
  model, record_turn=..., record_plugin_run=...) -> None` (blocking loop,
  meant to run in a `daemon=True` thread) — a self-check pass first
  collapsed this to `**tick_kwargs` to avoid repeating `tick()`'s own
  parameter names, then a second, more careful pass reversed it: forwarding
  `**kwargs` loses mypy's ability to check the call, the wrong trade for
  five duplicated names in this project. Shipped with the explicit list.
  `tests/unit/test_scheduling.py` (new) — `_is_due`/`_advance` unit cases
  (including the "long gap, no backlog" case spec.md's requirement 5 names);
  `tick()` against a tmp SQLite store with a monkeypatched
  `gateway_dispatch.handle_inbound`, asserting due-only firing and
  advance-vs-delete behavior; one failing trigger doesn't stop the others.
- `src/sadana/gateway_dispatch.py` — `handle_inbound()` gains one branch at
  the top: `conversation_store.load_pause(conn, key)`; `None` → today's exact
  existing flow, unchanged, except that its own `build_dispatch()` call now
  also passes `persist_pause` — a closure over this call's own `conn`/`key`
  calling the new `conversation_store.save_pause_from_result()`, the one
  place a run's *first* pause is written. When `pause` is not `None` →
  `plugin_dispatch.resume_paused_run()` instead of `take_turn_and_reconcile()`,
  its result text appended via `conversation.append()` and persisted, no
  model call.
  `tests/unit/test_gateway_dispatch.py` — an explicit regression test (no
  pause row: byte-identical behavior to before this work item), a
  resume-routing test (pause row present: model never called, history gains
  the result message, pause row cleared on a terminal result), and a test
  proving a real turn's dispatch call pausing for the first time persists a
  real row (the case that caught the `persist_pause` gap in the first place).
- `src/sadana/conversation_store.py` — one more function beyond the two
  tables/seven originally planned: `save_pause_from_result(conn, *,
  conversation_key, result: plugins.DagResult) -> None`, unpacking a
  `paused_node`-bearing `DagResult` into `save_pause()`'s own arguments —
  added during self-check so the two callers that can produce a pause
  (`plugin_dispatch.py`'s new `persist_pause` closure and its own
  `resume_paused_run`) share one definition of that mapping instead of each
  writing it out by hand.
- `tests/conftest.py` — gains `wait_then_summarize_installed(tmp_path)`,
  a shared `call` → `wait` → `compute` fixture (with a real `plugin.toml` on
  disk, not just an in-memory `Manifest` — `resume_paused_run`'s own
  `plugin_manifest.validate()` call needs one) — added during self-check
  once a reuse finding caught the same fixture duplicated byte-for-byte
  between `test_gateway_dispatch.py` and `test_plugin_dispatch.py`.
- `src/sadana/subcommands/gateway.py` — `cmd_gateway_run` reads
  `SADANA_SCHEDULING_TICK_SECONDS` (`config.env_int`, default `30`) and
  starts `threading.Thread(target=scheduling.run_tick_loop, kwargs={...}, daemon=True).start()`
  before `return gateway_daemon.run(...)`. `gateway_daemon.py` itself is not
  touched.
  `tests/unit/test_subcommands_gateway.py` — new test: secret set,
  `gateway_daemon.run` and `scheduling.run_tick_loop` both monkeypatched,
  asserts the thread is started with the right `conn`/`plugin_set`/interval
  before the daemon call — the only existing test for this function
  (`..._refuses_to_start_when_secret_is_unset`) returns before reaching this
  code, so today this line would ship with zero coverage without this test.
- `scripts/prove_gateway_scheduling_e2e.py` (new) — real daemon, a real
  short-interval tick loop, a real SQLite store: registers a one-shot
  trigger due immediately, asserts it fires with no webhook call involved
  and is gone from `scheduled_triggers` afterward.
- `scripts/prove_gateway_wait_e2e.py` (new) — real daemon + `plugin-d`: one
  real webhook POST invokes the entry tool (via the model — `model_access.send`
  monkeypatched to call the tool, matching `prove_plugin_dispatch_e2e.py`'s
  own pattern) and pauses; a second real POST on the same `chat_id` resumes
  it; asserts the paused reply, the real `plugin_pauses` row, the resumed
  terminal reply, and the row's removal.
- `CLAUDE.md` — already amended this session (the `_sadana_`-prefixed
  reserved-key rule), included in this work item's one commit; not touched
  again by this plan.

## Order of work

1. `plugins.py`: `ResumeState`, `DagResult.paused_node`. Narrow:
   `bash scripts/run_tests.sh tests/unit/test_plugins.py`, `make typecheck`.
2. `tests/fixtures/plugins/plugin-d/`. Verify by hand:
   `SADANA_PLUGINS_DIR=tests/fixtures/plugins python3 -c "from sadana import plugin_manifest; print(plugin_manifest.discover_plugins())"`
   finds it alongside a/b/c.
3. `plugin_manifest.py` resume support + wait-branch split, and its tests
   (including the rewritten `test_run_graph_refuses_wait_nodes`). Narrow:
   `bash scripts/run_tests.sh tests/unit/test_plugin_manifest.py`.
4. `conversation_store.py`'s two tables + CRUD + tests. Narrow:
   `bash scripts/run_tests.sh tests/unit/test_conversation_store.py`.
5. `plugin_dispatch.py`'s session-key seeding + `resume_paused_run` + tests.
   Narrow: `bash scripts/run_tests.sh tests/unit/test_plugin_dispatch.py`.
6. `scheduling.py` + tests. Narrow:
   `bash scripts/run_tests.sh tests/unit/test_scheduling.py`.
7. `gateway_dispatch.py`'s pause-check branch + tests — the riskiest step
   (see `## Risks`), landed only once steps 4 and 5 have already proven the
   pause store and the resume function correct in isolation. Narrow:
   `bash scripts/run_tests.sh tests/unit/test_gateway_dispatch.py`.
8. `subcommands/gateway.py`'s thread wiring + its new test. Narrow:
   `bash scripts/run_tests.sh tests/unit/test_subcommands_gateway.py`.
9. `scripts/prove_gateway_scheduling_e2e.py`, run for real.
10. `scripts/prove_gateway_wait_e2e.py`, run for real.
11. Self-check (`/ponytail-review` + `/simplify` on the full diff), then
    `make verify`.

## Risks

**What could this change break?** Named, not generic:

- `run_graph()`'s existing callers (`plugin_dispatch.dispatch()`,
  `eval_harness`, `scripts/prove_plugin_dispatch_e2e.py`) all call it without
  `resume`, which defaults to `None` — behavior for every one of them must
  stay identical; proven by their own existing tests staying green with no
  changes to those call sites.
- `test_run_graph_refuses_wait_nodes` — the one existing test this plan
  *must* break on purpose. Confirmed by reading it: it currently asserts
  `wait` fails closed, which spec.md's design directly changes. Rewritten,
  not deleted, in step 3 — an untouched copy would silently contradict the
  new behavior forever.
- `DagResult`'s construction sites — checked (`plugins.py`'s own
  `NodeTrace`/`DagResult` docstrings, `run_graph`'s `result()`/`failed()`
  helpers, `dispatch()`'s "no installed plugin" branch) — every one already
  uses keyword arguments, so a new field with a default breaks none of them.
  Re-grepped for `DagResult(` at the start of step 1 to catch any positional
  use this plan missed.
- `conversation_store.open_store()`'s existing schema test
  (`test_open_store_creates_file_and_both_tables`) already asserts a subset
  (`{"conversations", "messages"} <= tables`), not an exact set — confirmed
  by reading it; two more tables do not break it.
- `cmd_gateway_run`'s only existing test
  (`test_cmd_gateway_run_refuses_to_start_when_secret_is_unset`) returns
  before reaching the new thread-start line at all — confirmed by reading
  it. Without step 8's new test, the new line would ship with zero coverage,
  and a test that reaches it without monkeypatching `scheduling.run_tick_loop`
  would hang the suite on a real infinite loop. Both risks are why step 8's
  test explicitly monkeypatches the loop target, not just `gateway_daemon.run`.

**Which step is riskiest, and why that one?** Step 7,
`gateway_dispatch.py`'s pause-check branch. Getting the condition wrong in
either direction is a real, hard-to-notice failure: too eager (treating an
ordinary chat message as a resume) silently swallows real conversation; too
narrow (never clearing a pause row on a lookup failure) leaks a permanently
stuck conversation with no path back to ordinary chat. It is ordered last
among the mechanism steps specifically so that by the time it calls
`conversation_store.load_pause`/`plugin_dispatch.resume_paused_run`, both are
already independently unit-tested and trusted — step 7 then only has to get
the *branching* right, not the underlying mechanism too. The two e2e proofs
(steps 9-10) land after it for the same reason applied one level up: they
exercise the fully wired system only once every piece under it is already
proven alone.

**Drift check against spec.md's `## Rejected alternatives`:** re-read before
writing this plan. Confirmed not drifting back toward: a `DagResult | DagPaused`
union (kept as one optional field); a fresh conversation per scheduled firing
(kept as one growing conversation per trigger name); extending
`build_dispatch()`'s return tuple to share its `ask` closure (kept as
`resume_paused_run`'s own small `_unsupported_ask` instead); cron-expression
parsing (kept to anchor timestamp + interval only); a second pure-logic file
for `scheduling.py`'s two one-line helpers (kept in one file, matching
`conversation_store.py`'s own precedent).

## Proof

- Every narrow command in `## Order of work` passing is evidence the piece
  it names works in isolation, not evidence the whole feature works — named
  per step rather than claimed once at the end.
- `bash scripts/run_tests.sh tests/unit/test_plugin_manifest.py` covering:
  wait pauses (not fails); resume continues past it with the resuming
  payload as the next node's input; trace/artifacts carry forward
  faithfully across the pause; a second `wait` pauses again; `each` still
  fails exactly as before.
- `bash scripts/run_tests.sh tests/unit/test_gateway_dispatch.py` covering:
  no-pause behavior is unchanged (the named regression test); a pause routes
  through `resume_paused_run` with no model call; a terminal resume clears
  the pause row and appends one history message.
- `scripts/prove_gateway_scheduling_e2e.py`'s real output: a one-shot
  trigger registered due-now fires with no webhook call in the log, and a
  trigger registered due-later does not fire on the same tick — pasted into
  `review.md`'s `## Evidence`.
- `scripts/prove_gateway_wait_e2e.py`'s real output: POST 1 against
  `plugin-d` returns the "waiting" text and a real row exists in
  `plugin_pauses` (queried directly against the script's own SQLite file);
  POST 2 on the same `chat_id` returns the resumed terminal text and the row
  is gone — pasted into `review.md`'s `## Evidence`.
- `make verify` ending `VERIFY OK`, pasted in full.
