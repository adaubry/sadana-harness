# Plan: OBSERVABILITY-01 turn-and-plugin-run-records (from intent.md 2026-09-10)

## Files that change

- `src/sadana/observability.py` (new) — `Recorder` dataclass, `make_recorder(conn)`,
  schema (`turn_runs`, `plugin_runs`), `record_turn`/`record_plugin_run`
  closures (time via caller-supplied duration, write via `asyncio.to_thread`,
  catch+log `sqlite3.Error`).
- `tests/unit/test_observability.py` (new) — schema idempotency (fresh +
  pre-existing store), a successful `record_turn`/`record_plugin_run` write
  round-tripped by direct `SELECT`, and a forced-failure case (closed/invalid
  connection) proving the write is swallowed and logged, never raised.
- `src/sadana/plugin_dispatch.py` — add `record_turn`/`record_plugin_run`
  optional params (no-op defaults) to `build_dispatch()` and
  `take_turn_and_reconcile()`; time and call them from `dispatch()`, `ask()`,
  and `take_turn_and_reconcile()`'s own `_take_turn()` call; a closure-local
  `seq` counter in `build_dispatch()` for `plugin_runs.seq_in_turn`.
- `tests/unit/test_plugin_dispatch.py` — extend existing fixtures/tests:
  default (no-op) behavior unchanged when the new params aren't passed; a
  fake recorder captures the right `TurnKey`/`TurnResult`/duration on a
  plain turn, on a turn with a plugin dispatch, and on an `ask`-spawned
  child turn; a recorder that raises does not change the turn's own result
  or propagate.
- `src/sadana/subcommands/runs.py` (new) — `build_runs_parser`, `cmd_runs`,
  `format_turn_run_line`/`format_plugin_run_line`, mirroring
  `subcommands/conversations.py`'s shape.
- `tests/unit/test_subcommands_runs.py` (new) — parser wiring, formatting of
  a turn row and a plugin row, empty-result behavior for an unknown key.
- `src/sadana/cli.py` — import and register `build_runs_parser`.
- `src/sadana/subcommands/chat.py` — pass `observability.make_recorder(conn)`'s
  `record_turn`/`record_plugin_run` into `build_dispatch()`/
  `take_turn_and_reconcile()`.
- `tests/unit/test_subcommands_chat.py` — extend: a chat turn produces a
  `turn_runs` row in the store `chat.py` already opens.
- `src/sadana/gateway_dispatch.py` — same wiring as `chat.py`.
- `tests/unit/test_gateway_dispatch.py` — same assertion as chat's.

## Order of work

1. `observability.py` + `test_observability.py` — lands and is provable
   standalone, nothing else depends on it existing yet.
2. `plugin_dispatch.py` + its test extensions, using a fake in-test recorder
   (not `observability.py` itself) — proves the wiring contract (right
   args, right call sites, failure isolation) independent of real SQLite.
3. `subcommands/runs.py` + its tests + `cli.py` registration — the read
   side, provable against rows inserted directly via `observability.py`
   from step 1.
4. `chat.py` wiring + test extension — the first real end-to-end producer.
5. `gateway_dispatch.py` wiring + test extension — same change, second
   caller; lands last because it repeats step 4's pattern exactly, so any
   surprise already surfaced once before this step.

Risky step (recording inside live `ask`/`dispatch` closures) lands at step 2,
proven with a fake recorder before step 4 ever touches a real database file.

## Risks

- **What this could break**: `build_dispatch()` and `take_turn_and_reconcile()`
  are called today by exactly `chat.py` and `gateway_dispatch.py`
  (confirmed via grep during Design). Both call sites use only keyword
  arguments already, so new optional parameters with no-op defaults are
  additive — no existing call breaks. `ChildSeqTracker`'s existing
  reconciliation contract is untouched; the new `seq` counter is a sibling,
  not a replacement.
- **Most risky step**: step 2 — three call sites (`dispatch`, `ask`,
  `take_turn_and_reconcile`) inside closures that already carry the
  `ChildSeqTracker` footgun (`docs/reference/dispatch_closure_state_bug.md`).
  Mitigation: a fake recorder in tests asserts call count and args per
  closure invocation before any real I/O is wired in (step 4), and the
  forced-failure test proves the try/except boundary holds before it's ever
  load-bearing against a real database.
- **Drift check against `spec.md` § Rejected alternatives**: no event-bus/
  subscriber list is introduced (`observability.py` exposes two plain
  callables, not a `subscribe()`/registry); no wire-format versioning; no
  dollar-cost field; no second SQLite file — `make_recorder(conn)` takes the
  same connection `chat.py`/`gateway_dispatch.py` already hold, never opens
  its own. Verified against the plan above file by file — none reinstates a
  rejected option.

## Proof

- `test_observability.py`: schema idempotent on fresh + pre-existing
  `tmp_path` stores; a `record_turn`/`record_plugin_run` call round-trips
  via direct `SELECT` on the connection; a forced write failure (e.g. a
  closed connection passed to the recorder) is swallowed, not raised.
- `test_plugin_dispatch.py`: fake-recorder call assertions for plain turn,
  plugin-dispatch turn, and `ask`-spawned child turn; a raising fake
  recorder does not change `TurnResult`/`DagResult` or propagate out of
  `take_turn_and_reconcile`/`dispatch`/`ask`.
- `test_subcommands_runs.py`: `sadana runs <key>` output lines match rows
  inserted directly via `observability.record_turn`/`record_plugin_run`;
  unknown key prints nothing, doesn't raise.
- `test_subcommands_chat.py` / `test_gateway_dispatch.py`: one exercised
  turn leaves exactly one `turn_runs` row (plus a `plugin_runs` row when a
  plugin was dispatched) in the store the test already opens for that
  suite.
- `make verify` ends `VERIFY OK`, pasted in full as this stage's evidence.

## Rejected alternatives revisited during build (none)

## Reference corpus checked

`agent/monitoring/{events,emitter}.py`: hermes's emitter is an egress-only,
fire-and-forget queue with no local persistence ("Nothing is persisted
here... if no subscriber is attached, events simply age out") — the
opposite of Requirement 3 (a local queryable record). Confirms `spec.md`'s
decision to decline the emitter/pub-sub shape; adopts only its "must not
affect the hot path, must not raise" posture, already folded into
`spec.md`'s best-effort recording contract.
