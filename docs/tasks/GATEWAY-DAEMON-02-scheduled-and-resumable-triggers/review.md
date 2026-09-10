# Review: Scheduled and resumable triggers (from plan.md 2026-09-10)

Reviewed: HEAD (working tree, uncommitted — nothing has been committed for
this work item yet) — 23 files, +2421/-30
Reviewer context: fresh session — the review itself was delegated to a
freshly-spawned subagent with no context beyond the diff and the three
artifacts (intent.md, spec.md, plan.md), per this stage's own "buy the
separation instead of assuming it" instruction. It independently re-ran
both new e2e proof scripts and the six changed/added unit test files for
real before writing any finding.
Second opinion: this cold review is a different pass than build's own
self-check, not a repeat of it. Build stage already ran `/ponytail-review` +
4 parallel `/simplify` agents (reuse/simplification/efficiency/altitude —
complexity and quality only, explicitly out of scope for bugs/security).
This is the first pass that looked for bugs, security exposure, and
spec/plan compliance.

## Evidence

```
$ make verify
docs/tasks/GATEWAY-DAEMON-02-scheduled-and-resumable-triggers: all present artifacts valid
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
Success: no issues found in 37 source files
TYPES OK
▶ hermetic run: TZ=UTC LANG=C.UTF-8 PYTHONHASHSEED=0, empty env
........................................................................ [ 12%]
........................................................................ [ 24%]
........................................................................ [ 36%]
........................................................................ [ 48%]
........................................................................ [ 61%]
........................................................................ [ 73%]
........................................................................ [ 85%]
........................................................................ [ 97%]
............                                                             [100%]
588 passed in 16.60s
TESTS OK
VERIFY OK
```

`scripts/prove_gateway_scheduling_e2e.py` and `scripts/prove_gateway_wait_e2e.py`
both ended `ALL ASSERTIONS PASSED` on their own real, independent runs
(re-run by this session and, separately, re-run again by the cold-review
subagent itself before it wrote any finding) — a real daemon, a real
SQLite store, a real wait/resume round trip including a genuine
`plugin_pauses` row.

## Scope check

Diff touches exactly the files `plan.md § Files that change` names — every
one of `plugins.py`, `tests/fixtures/plugins/plugin-d/*`,
`plugin_manifest.py` (+ its test), `conversation_store.py` (+ its test),
`plugin_dispatch.py` (+ its test), `scheduling.py` (new, + its test),
`gateway_dispatch.py` (+ its test), `tests/conftest.py`,
`subcommands/gateway.py` (+ its test), both `scripts/prove_*_e2e.py`, and
the already-approved `CLAUDE.md` line. No file touched that the plan didn't
name; no file the plan named that the diff doesn't touch.

## Findings

**Important**

- **[Bugs]** `scheduling.tick()` reads and writes `conn` directly from the
  background `run_tick_loop` daemon thread — `due_triggers()`
  (`src/sadana/scheduling.py:65`), `delete_scheduled_trigger()` (`:82`),
  `advance_scheduled_trigger()` (`:84`) — none of it guarded by
  `gateway_dispatch._conn_lock` (`gateway_dispatch.py:46`), the only thing
  serializing `conn` across the webhook's per-request threads.
  `conversation_store.open_store()`'s own docstring states the connection
  is safe only "by one thread at a time, different call to call"
  (`conversation_store.py:140`) — this design violates that documented
  invariant for real, every tick, for the daemon's whole life. This is
  exactly the scenario CLAUDE.md's own "Revisit `conversation_store.py`'s
  single connection…" rule names by example: "a background/scheduled task
  writing alongside a live turn." Neither spec.md nor plan.md discusses
  `_conn_lock` in connection with `tick()` at all — only with
  `resume_paused_run`'s own re-pause branch, which genuinely is inside the
  lock (called from `handle_inbound`). Fix before merge: wrap `tick()`'s
  store calls in the same lock, or route the tick loop's inbound firing
  through `handle_inbound`-equivalent locking.
  **Fixed**: `gateway_dispatch`'s lock is now the public `conn_lock` (a
  second real cross-module caller earned the rename), and `tick()` wraps
  its own `due_triggers()` read and its post-fire advance/delete in it —
  as two separate acquisitions, never nested inside the acquisition
  `handle_inbound()` takes internally, which would deadlock the
  non-reentrant `threading.Lock`. New regression test:
  `test_tick_wraps_its_own_conn_access_in_the_shared_conn_lock`
  (`tests/unit/test_scheduling.py`), asserting exactly two acquisitions
  per tick and that `handle_inbound` is never called while one is held.

- **[Bugs]** `resume_paused_run()` — and its own Interface contract ("none
  of the new functions raise for an expected outcome... never an
  exception") — does not hold when a plugin is redeployed between pause and
  resume such that the plugin and entry still resolve but the specific
  paused node name no longer exists in the new graph.
  `plugin_manifest.run_graph()`'s resume seeding does
  `wait_node = by_name[resume.node]` (`plugin_manifest.py:370`) with no
  guard, and `resume_paused_run()` (`plugin_dispatch.py:327-335`) has no
  `try`/`except` around the `run_graph()` call. Reproduced directly by the
  reviewer: a pause row naming node `"future"`, resumed against a plugin
  whose node was renamed to `"future2"` (plugin+entry still valid), raises
  an uncaught `KeyError: 'future'` — and because the crash happens before
  either of `resume_paused_run`'s own delete/save branches run, the
  `plugin_pauses` row is never cleared. Every later inbound event on that
  session key repeats the same crash, permanently stranding the
  conversation with no path back to ordinary chat — the exact failure
  shape plan.md's own step-7 Risk worried about, reached through a case
  (stale node name under an otherwise-valid plugin/entry) neither spec.md's
  Concerns nor plan.md's Risks considered.
  **Fixed**: `resume_paused_run()` now checks `pause.node` against
  `plugins._node_index(outcome.manifest)` before ever calling `run_graph`,
  treating a missing node as the same class of clean, `failed_node`-set
  failure as an unresolved plugin/entry — the pause row is cleared, not
  crashed past. New regression test reproducing the reviewer's own
  scenario: `test_resume_paused_run_clears_the_pause_row_and_fails_closed_when_the_node_was_renamed`
  (`tests/unit/test_plugin_dispatch.py`).

- **[Compliance]** A resumed run is invisible to observability.
  `resume_paused_run()` (`plugin_dispatch.py:274-340`) takes no
  `record_turn`/`record_plugin_run` parameters and never calls either;
  `gateway_dispatch.handle_inbound()`'s pause branch (`gateway_dispatch.py:108-118`)
  doesn't call them either. Only the *first* pause (recorded via
  `build_dispatch()`'s own `dispatch()` closure, which does call
  `record_plugin_run` right before `persist_pause`) is ever recorded —
  every node a resumed walk visits afterward, including further
  `call`/`compute` nodes en route to the terminal result, produces a real
  `DagResult` OBSERVABILITY-01's own machinery never sees. Neither spec.md
  nor plan.md names this as a Non-goal or a Concern; both only discuss
  `record_turn`/`record_plugin_run` in connection with the first pause and
  `tick()`'s pass-through to `handle_inbound`. This silently breaks
  OBSERVABILITY-01's own already-closed contract ("a run leaves numbers
  behind, not just a transcript") for an entire new code path.
  **Deferred, not fixed**: explicitly scoped out by Adam's own "fix the
  bugs" instruction — the two `[Bugs]` findings above were fixed, this
  `[Compliance]` one was not, on purpose, not silently. Worth a follow-up
  work item wiring `record_turn`/`record_plugin_run` (or a documented
  Non-goal amendment to spec.md) before this path carries real production
  traffic.

**Nits**

- **[Bugs]** `resume_paused_run` and `save_pause_from_result` both enforce
  a caller-contract with a bare `assert` ("pause row exists",
  "result.paused_node is not None"), which vanishes under
  `python -O`/`PYTHONOPTIMIZE` — an existing codebase convention, not new
  to this diff, but worth a second look given how state-sensitive this
  particular boundary is.
- **[Compliance]** A recurring trigger that fails on every single tick (a
  permanently broken plugin) retries forever with no backoff — not
  required by any stated requirement or Non-goal, but a rough edge worth
  naming against the otherwise-deliberate "no catch-up" design.

**Confirmed clean (compliance pass, checked directly)**

- No drift toward any of spec.md's five Rejected alternatives:
  `DagResult.paused_node` stays one optional field, not a union
  (`plugins.py:157-163,206-224`); one growing conversation per trigger name
  via `schedule:<name>` (`gateway.py:37-46`); `build_dispatch()`'s return
  tuple is still `(dispatch, tracker)`, not extended; no cron parser
  anywhere; `scheduling.py`'s two pure helpers stay in the one file.
- `DagResult.paused_node`/`plugins.ResumeState` are constructed fresh every
  call, never stored as live objects; `Pause`/`ScheduledTrigger` store only
  names (`plugin`/`entry`/`node` strings), never directory paths or
  manifest objects — names-not-pointers holds.
- `approve()` is still only ever invoked for `call` nodes
  (`plugin_manifest.py:413-414`), unaffected by the new `wait` branch.
- `_sadana_session_key` is merged in last, so a model-supplied value under
  that key cannot win — verified directly against
  `test_build_dispatch_session_key_wins_over_a_model_supplied_value`.
- `plugin_pauses.conversation_key PRIMARY KEY` genuinely enforces "at most
  one outstanding pause per conversation" as a real database constraint,
  not just documentation.

## Decision

Approved by Adam, 2026-09-10 — "fix the bugs and consider it approved." Both
`[Bugs]` Important findings (the unlocked `conn` access in `scheduling.tick()`;
the uncaught `KeyError` on a resumed run whose node was renamed) were fixed
in this branch, each with a new regression test, and re-verified: `make
verify` green (590 tests), both `scripts/prove_gateway_*_e2e.py` scripts
re-run for real and still `ALL ASSERTIONS PASSED`. The one `[Compliance]`
Important finding (resumed runs invisible to OBSERVABILITY-01) was
explicitly left open per the same instruction — recorded above as deferred,
not silently dropped.
