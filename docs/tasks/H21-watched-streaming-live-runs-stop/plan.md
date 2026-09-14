# Plan: Watched — streaming, live runs and stop (from intent.md 2026-09-14)

Author: Adam Aubry (project owner). Status: approved.

## Files that change

`src/sadana/subcommands/door.py` (pre-existing duplicate-key lint fix,
unrelated to this item's design, bundled at the user's direction);
`src/sadana/observability.py`; `src/sadana/ledger.py` (registers the new
`spans` noun, `prefix="spn"`, one line, discovered mid-implementation:
`record_change(noun="spans", ...)` raises `ValueError` against the closed
`_NOUNS` registry otherwise — `## Design` in spec.md named the table but not
this registration); `src/sadana/model_access.py`;
`src/sadana/model_providers/stub/provider.py` (new);
`src/sadana/model_providers/openrouter/provider.py`;
`src/sadana/conversation.py`; `src/sadana/plugin_manifest.py`;
`src/sadana/plugin_dispatch.py`; `src/sadana/client_surface.py`;
`src/sadana/conversation_store.py`; `src/sadana/door/run_control.py` (new);
`src/sadana/door/events.py` (new); `src/sadana/door/nouns/messages.py`;
`src/sadana/door/nouns/runs.py`; `src/sadana/door/nouns/spans.py`;
`src/sadana/door/capabilities.py`; `src/sadana/door/turn_client.py`
(one new parameter, `observer`, forwarded unchanged to `client_surface.
take_turn` — spec.md's own `## Design` names this exactly, missed from
this list at first pass; added mid-implementation, step 12); `docs/console/nouns/run.md`;
`docs/console/nouns/span.md`; `docs/console/nouns/message.md`;
`docs/console/wire.md`; `scripts/prove_streaming_e2e.py` (new, Deploy-stage
evidence, not part of `make test`).

Tests: `tests/fixtures/openrouter_stream.txt` (new);
`tests/unit/test_observability.py`; `tests/unit/test_model_access.py`;
`tests/unit/test_model_providers_stub.py` (new);
`tests/unit/test_model_providers_openrouter.py`;
`tests/unit/test_conversation.py`; `tests/unit/test_plugin_manifest.py`;
`tests/unit/test_plugin_dispatch.py`; `tests/unit/test_conversation_store.py`;
`tests/unit/test_door_run_control.py` (new); `tests/unit/test_door_events.py`
(new); `tests/unit/test_door_capabilities.py`;
`tests/contract/nouns/test_messages.py`; `tests/contract/nouns/test_runs.py`;
`tests/contract/nouns/test_spans.py`; `tests/contract/nouns/test_golden_journey.py`
(its own `model_access.send` test doubles don't accept the new `on_delta`
argument, now that a real door turn genuinely streams — missed from this
list at first pass; added mid-implementation, step 12).

## Order of work

1. **`subcommands/door.py`**: delete the duplicate `"harness": harness`
   entry (line 115). Clears the pre-existing lint break before any other
   step's `make verify` run has to carry it.
2. **`observability.py`**: append the `spans` table to `_SCHEMA`;
   `TURN_RUN_STATES` gains `"running"`, `"waiting"`, `"stopped"`;
   `PLUGIN_RUN_STATES` gains `"running"`; add
   `record_turn_started(turn_key, user_message_seq, started_at) -> str`,
   `record_plugin_run_started(turn_key, seq_in_turn, started_at) -> str`,
   `record_node(...)`, `noop_record_started`. `record_turn`/
   `record_plugin_run` (existing) detect "no row yet for this key" and keep
   inserting a finished row in one shot exactly as today when no
   `*_started` call preceded them — the existing callers (`build_dispatch`'s
   own two record calls, before this item touches them) keep working with
   zero change. Lands and is tested standalone — nothing else depends on it
   yet, so a mistake here is caught before anything is built on top.
3. **`model_access.py`**: `Request.stream: bool = False`;
   `ProviderManifest.stream_fn`/`OnDelta`; `send(request, on_delta=None)`;
   `resolve(request, on_delta=None)`. Tested with a hand-built
   `ProviderManifest` whose `stream_fn` is a local test double — no real
   provider needed yet.
4. **`model_providers/stub/provider.py`** (new): `request_fn` returns a
   fixed reply; `stream_fn` emits it as three deltas via `on_delta`, then
   returns the identical body. Gives every later step a network-free,
   cost-free provider to drive streaming against.
5. **`model_providers/openrouter/provider.py`**: real `stream_fn` —
   `stream: true` in the body, `resp.readline()` loop, `data: {...}` /
   `data: [DONE]` parsing, delta/tool-call-fragment accumulation (name
   assigned, arguments concatenated, keyed by index), final body in the
   non-streaming shape, `(None, {...})` on a transport error mid-read.
   `tests/fixtures/openrouter_stream.txt` recorded once (by hand, from the
   shape OpenRouter's own docs and `docs/tasks/C1-model-access/spec.md`
   already establish) and never touching the network at test time.
6. **`conversation.py`**: `TurnObserver` protocol; `_noop_observer`;
   `complete(..., on_delta=None)`; `run_turn(..., observer=None)` —
   `turn_started` once, `text_delta` forwarded with the per-turn `seq`,
   `should_stop()` checked before each `MODEL_CALL` and after each
   `TOOL_ROUND`, `turn_finished` once at the end; `take_turn(...,
   observer=None)` passes it through. Tested against the stub provider
   from step 4 — no database, no door, so a failure here is isolated to the
   loop's own control flow, the single riskiest piece of logic in this
   item. `run_turn`/`take_turn` called with `observer=None` (every existing
   test in `test_conversation.py`) must keep passing unchanged.
7. **`plugin_manifest.py`**: `NodeSink` protocol; `run_graph`/`_run_graph`
   gain `node_sink: NodeSink | None = None`; `node_started`/`node_finished`
   called around every node kind's own body, including the existing
   `except Exception as e:` branch, which also reports
   `f"{type(e).__name__}: {e}"[:200]` to the sink. The walker itself reads
   no clock. Tested with a hand-built manifest and a recording test double
   sink — no dependency on step 6.
8. **`plugin_dispatch.py`**: `build_dispatch` gains
   `record_turn_started`/`record_plugin_run_started` (defaulted to
   `observability.noop_record_started`) and builds a `NodeSink` inside its
   `dispatch()` closure that reads `time.time()` on both sides and calls
   `observability.record_node`; `take_turn_and_reconcile(...,
   observer=None)` passes through to `_take_turn`/`conversation.take_turn`.
9. **`client_surface.py`**: `take_turn(..., observer=None)` — a one-line
   passthrough to `plugin_dispatch.take_turn_and_reconcile`. Every existing
   caller (`subcommands/chat.py`, `gateway_dispatch.handle_inbound`,
   `eval_harness.py`'s indirect path) keeps compiling and passing with no
   change, proving Requirement 6 mechanically rather than by inspection.
10. **`conversation_store.py`**: `_insert_messages`'s `present` computation
    excludes a `msg_seq` whose row is still `state = 'streaming'`; the
    `INSERT` becomes `INSERT ... ON CONFLICT(conversation_key, msg_seq) DO
    UPDATE SET content=excluded.content, tool_calls_json=excluded.tool_calls_json,
    tool_call_id=excluded.tool_call_id, state=excluded.state WHERE
    messages.state = 'streaming'` (never touching `id`/`created_at`);
    `_record_messages` is told which returned ids were freshly inserted
    versus promoted from `streaming`, emitting a `created` or `changed`
    ledger row accordingly. The second-riskiest step — it touches a
    documented "never edited once written" invariant directly — landed
    only after everything underneath (steps 2-9) already works in
    isolation, with a test proving the inverse case just as explicitly as
    the positive one: an already-`sent` row resubmitted with different
    content is untouched.
11. **`door/run_control.py`** (new) and **`door/events.py`** (new): the
    stop registry and the ephemeral-frame queue. Pure new modules with no
    existing caller yet, tested standalone.
12. **`door/nouns/messages.py`**: build the door's `TurnObserver` (closing
    over `conn`, `conversation_key`, the freshly-minted `run_id`, the
    assistant row's own minted `id`, and the `run_control`/`events` modules
    from step 11); rewrite `create()` to insert the user row directly
    (content and position already known synchronously, no need to wait for
    the turn) before calling `turn_client.send(..., observer=...)`, and to
    read the finished result back by `run_id` instead of the current
    before/after `msg_seq`-range diff. The single riskiest step in this
    plan: it is where every prior step's wiring actually meets, and it is
    exactly the area H20's own `review.md` left two Important findings
    open (an empty-200-vs-404 gap on `messages.list`/`traces.list` for an
    unknown parent; an untested failed-turn-marks-assistant-row path).
    Both are re-verified with real, named tests in this step, not assumed
    still true after the rewrite (see `## Proof`).
13. **`door/nouns/runs.py`**: `act()` gains a real body for `"stop"` —
    `run_control.request_stop(id)`, `True` returns the run's current
    rendering, `False` returns `409 CONFLICT`. Small and isolated; depends
    only on step 11.
14. **`door/nouns/spans.py`**: replace the "no table yet" stub — real
    `list`/`get` reading the `spans` table step 2 created.
15. **`door/capabilities.py`**: append `"streaming"`, `"runs.live"`,
    `"runs.stop"` to `DECLARED`. Landed last among the "make it reachable"
    steps so that `runs.py`'s stop action and `spans.py`'s real reads are
    already correct, and already covered by their own tests, before
    `router.py`'s capability gate makes either one reachable through the
    door for the first time.
16. **Doc updates**: `docs/console/nouns/run.md`, `span.md`, `message.md`,
    `docs/console/wire.md` — drop the "arrives with H21"/"no table yet"/
    "does not always produce a row" language each already flags as this
    item's own trigger.
17. **`scripts/prove_streaming_e2e.py`** (new): one real OpenRouter turn
    with `stream=True`, printing each delta's `seq` and the final message;
    a second turn stopped after the first delta, printing the run's final
    state. Written last, since it needs every other step working; not part
    of `make test`.

## Risks

**What could this change break, beyond the code being added?** Every
existing caller of a turn that never opts into `observer=` —
`eval_harness.run_task`, every `scripts/prove_*.py` script,
`subcommands/chat.py`, `gateway_dispatch.handle_inbound`, and every
existing test in `test_conversation.py`/`test_client_surface.py`/
`test_plugin_dispatch.py` that calls `run_turn`/`take_turn`/
`take_turn_and_reconcile` positionally or with today's keyword set. All of
them keep compiling and passing with zero edits, because `observer`,
`record_turn_started`, `record_plugin_run_started`, and `node_sink` are
every one a new, defaulted, trailing parameter — none of the fourteen
existing call sites this touches need to change. The one genuine behavior
change with no opt-in gate at all is `conversation_store._insert_messages`:
its `present` filter and its `INSERT` statement change for **every**
caller, not just a streaming one, so step 10's test suite has to prove the
non-streaming path is unaffected (a `state = 'sent'` row is still
literally never touched) rather than merely proving the new streaming
path works. `tests/contract/nouns/test_messages.py`,
`test_runs.py` and `test_spans.py`'s own existing assertions about the
stub behavior (`spans.list` always empty, `messages.create`'s current
before/after diffing) are expected to fail after steps 12/14 and are
rewritten as part of those same steps, not left red.

**Which step is the most risky, and why that one?** Step 12
(`door/nouns/messages.py`). It is the only step where the streaming chain
(6-9), the recording chain (2, 8), the stop registry (11), and H16's own
message-persistence invariant (10) all have to cooperate inside one
function, and it is the one function H20's own cold review already flagged
twice as under-tested in exactly this area. Mitigated by ordering: every
piece it depends on (2-11) is already independently proven before step 12
starts, so a failure in step 12's own tests localizes to the integration,
not to any one piece; and by naming the two carried-over findings as
required test cases rather than optional cleanup (`## Proof`). Second most
risky: step 10, for the reason given above — mitigated the same way, by
landing it only after everything that will call it (2-9) already works.

**Which options did spec.md already reject, and is this plan drifting back
toward one?** Re-checked against spec.md's own `## Rejected alternatives`:
no second streaming outcome type is introduced (step 3's `stream_fn`
returns the same `(status, body)` shape); nothing in step 6 kills or
cancels a turn from outside — `should_stop()` is polled at the two named
boundaries spec.md fixed; steps 6-9 use plain optional parameters, not a
registry, for `TurnObserver`/`NodeSink`; step 11's queue holds no replay
buffer, just the live frames; step 10's upsert is guarded by
`WHERE state = 'streaming'`, not a general-purpose upsert over every row.
No drift found.

## Proof

- `make verify` ends `VERIFY OK` (lint clean, including step 1's fix;
  `make typecheck` clean; the full suite green).
- `tests/unit/test_observability.py`: `record_turn_started` inserts a
  `state="running"` row with `started_at` set and no `ended_at`;
  `record_turn` called afterward transitions it to `done`/`failed`/
  `stopped` with `ended_at >= started_at`; `record_turn` called with no
  prior `record_turn_started` still inserts a finished row in one shot,
  unchanged from today (regression); `record_node` writes one `spans` row
  per call, and a raising node's row's `error` field holds the exception's
  own message.
- `tests/unit/test_model_access.py`: a `stream_fn` test double drives
  `send`/`resolve` to call `on_delta` per fragment and to return the exact
  `Outcome` `classify()` would produce from an equivalent non-streaming
  body; `request.stream=True` against a manifest with no `stream_fn` falls
  back to `request_fn` with no error.
- `tests/unit/test_model_providers_stub.py`: `stream_fn`'s three
  `on_delta` calls concatenate to `request_fn`'s own content.
- `tests/unit/test_model_providers_openrouter.py`: the SSE parser reading
  `tests/fixtures/openrouter_stream.txt` (recorded, includes a split
  tool-call-argument sequence and `[DONE]`) produces a body equal to the
  non-streaming shape; a simulated `OSError` mid-read returns
  `(None, {...})`.
- `tests/unit/test_conversation.py`: `turn_started` called exactly once;
  `text_delta`'s `seq` strictly increasing from 1 across multiple model
  calls in one turn; `should_stop() -> True` before the second model call
  ends the turn with `ExitReason.INTERRUPTED, detail="stopped"` and a
  call-count assertion proving no second model call happened; every
  existing `observer=None` test unchanged and still green.
- `tests/unit/test_plugin_manifest.py`: a hand-built manifest with two
  `compute` nodes and one that raises: the sink's `node_started`/
  `node_finished` are called once per node in order, and the raising
  node's `node_finished` carries `ok=False` and the exception's own
  message threaded through for `plugin_dispatch`'s wrapper to record as
  `error`.
- `tests/unit/test_plugin_dispatch.py`: `build_dispatch`'s own sink wrapper
  produces one `record_node`-shaped call per node with
  `started_at <= ended_at`; a caller passing none of the three new
  recorder parameters behaves exactly as today (regression).
- `tests/unit/test_conversation_store.py`: a `state='streaming'` row is
  excluded from the append-only `present` filter and is updated in place
  (content/state change, `id`/`created_at` unchanged) when resubmitted; an
  already-`sent` row resubmitted with different content is **not**
  changed; `_record_messages` emits `created` for a fresh id and `changed`
  for a promoted one.
- `tests/unit/test_door_run_control.py`: register → request_stop (True,
  event set) → unregister; `request_stop` on an unregistered id returns
  `False`.
- `tests/unit/test_door_events.py`: frames pushed by `text_delta` arrive on
  `ephemeral_queue` in non-decreasing `seq` order.
- `tests/contract/nouns/test_messages.py`: a streamed `create()` produces
  exactly one assistant row for the turn, its final `content` equal to the
  turn's `final_text`; **H20's two carried-over findings re-verified
  directly**: (a) `messages.list` on an unknown/foreign parent answers
  `404`, not an empty `200` (extended from the harness noun's own correct
  behavior); (b) a turn that fails before any model completion (no
  assistant row ever appended) still surfaces `[INTERNAL]` with no row to
  mark, and a turn that fails after a completion has its own assistant row
  actually observed at `state="failed"` by a real test, not left asserted
  only in prose.
- `tests/contract/nouns/test_runs.py`: `POST /v1/runs/{id}:stop` on a run
  registered mid-turn stops it (the run's own state settles to
  `"stopped"`); the same action on an id never registered, or already
  finished, answers `409 CONFLICT`.
- `tests/contract/nouns/test_spans.py`: real rows produced by a real
  plugin dispatch inside a contract-level turn are listable and gettable.
- `tests/unit/test_door_capabilities.py`: `declared()` includes the three
  new names; `ALL` still has exactly sixteen entries.
- `scripts/prove_streaming_e2e.py`'s own output (Deploy-stage evidence,
  pasted into `review.md`, not part of `make test`): one real OpenRouter
  turn with `stream=True` printing each delta's `seq` and the final
  message; a second turn stopped after its first delta, printing the run's
  final state; one real plugin run's `spans` rows, printed in full.
