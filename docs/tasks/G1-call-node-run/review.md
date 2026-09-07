# Review: letting an approved step actually run (from plan.md 2026-09-07)

Reviewed: 8d31042..working-tree — 2 files, +37/-12 (after the Important finding's fix, applied in this branch)
Reviewer context: fresh session, no builder context
Second opinion: none — ran during build (self-check /ponytail-review + /simplify), not repeated here by design.

## Evidence

```
docs/tasks/G1-call-node-run: all present artifacts valid
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
Success: no issues found in 12 source files
TYPES OK
▶ hermetic run: TZ=UTC LANG=C.UTF-8 PYTHONHASHSEED=0, empty env
........................................................................ [ 21%]
........................................................................ [ 42%]
........................................................................ [ 64%]
........................................................................ [ 85%]
...............................................                          [100%]
335 passed in 2.67s
TESTS OK
VERIFY OK
```

## Findings

Cold review, no builder context. Three passes run (bugs, security,
compliance) against `intent.md`/`spec.md`/`plan.md`, plus a files-touched
scope check and a direct code-level re-check of the specific event-loop
hazard this deploy stage was told to scrutinize. One Important finding
surfaced — a plan.md step that was not carried out — everything else
checked came back clean, detailed below.

### Important

1. **plan.md step 3 was not carried out — a deviation from the approved plan (`docs/tasks/G1-call-node-run/plan.md`, "Order of work" step 3; `tests/unit/test_plugin_manifest.py:553-565`, `test_run_graph_call_node_records_what_was_asked`).** The plan says explicitly: "Fix `test_run_graph_call_node_records_what_was_asked`: change its local `_capturing_approve` to return `False`." The diff at review time did not touch this test — `_capturing_approve` still `return True`, and its fixture was still an empty `init.py` with `body="init:whatever"`. Under the new `call` branch this meant every run of this test resolved the approved node's body, `getattr`'d a non-existent `whatever` off an empty module, raised `AttributeError` inside `run_body`, and had that exception silently caught by `run_graph`'s own `except Exception` before falling through — none of which the test observed, since it only asserted on `seen` and never inspected the returned `DagResult`. The assertion still passed, but for a reason the plan never intended.

   **Fixed during this review**, applying the plan's own originally-written step: `_capturing_approve` now `return False` (`tests/unit/test_plugin_manifest.py:559`). The test now takes the decline path — which never reaches body resolution — matching the clean premise plan.md always described. Re-ran `make verify` after the change: still `VERIFY OK` (335 passed).

Everything else checked came back clean:
- **Bugs** — the one hazard this deploy stage was told to scrutinize (resolution done off-thread, not just invocation) is genuinely fixed: `src/sadana/plugin_manifest.py:350-358` defines `run_body` as an uncalled closure and only calls `_resolve_body(plugin_dir, n, modules)(v)` from inside it; `asyncio.to_thread(run_body)` therefore performs both the disk-I/O/`init.py`-exec resolution step and the invocation entirely off the event loop, unlike the previously-flagged mistake of eagerly resolving before passing to `to_thread`. No other logic error found in the `call` branch or its tests.
- **Security** — no widened reach: no new import of `sadana.execution` in `src/sadana/plugin_manifest.py` or `src/sadana/plugins.py` (confirmed via `grep -n "^import\|^from"` on both files), no secrets introduced, `call` bodies remain in-process opaque callables exactly like `compute`'s.
- **Scope** — `git diff --stat HEAD` touches exactly `src/sadana/plugin_manifest.py` and `tests/unit/test_plugin_manifest.py`, matching plan.md's `## Files that change` exactly; `src/sadana/plugins.py`, `plugin_dispatch.py`, `test_plugin_dispatch.py`, and `test_plugins.py` are untouched and still pass (335 passed; no `kind="call"` fixture exists in the dispatch/plugins test files).
- **spec.md acceptance criteria** — all satisfied: off-thread resolution (`plugin_manifest.py:350-358`); value threads to the next node (`test_run_graph_call_node_runs_its_body_and_threads_the_result`, `test_plugin_manifest.py:513-524`); decline never resolves the body (`test_run_graph_call_node_declined`, unchanged, `test_plugin_manifest.py:542-549`, structurally provable since resolving `init:whatever` against an empty module would raise); a raising body fails generically (`test_run_graph_call_node_raising_body_fails_closed`, `test_plugin_manifest.py:527-540`); no `sadana.execution` import (grep, above); `test_plugin_dispatch.py` unchanged and green; `make verify` ends `VERIFY OK` (Evidence, above).
- **spec.md Rejected alternatives** — no drift: body resolved via the shared `_resolve_body` machinery rather than a direct `sadana.execution` dispatch; run via `asyncio.to_thread` rather than in-line; body stays a plain sync callable, no `async def` requirement; no new live external round-trip script added, new tests use fake `init.py` functions like the existing `compute`/`route` tests.
- **Five design principles** — no violation: reuses F1's own `asyncio.to_thread` pattern rather than a new one; no new expensive-to-reverse decision; change stays in core exactly where spec.md places it; no step added to any other node kind's path (`compute`/`route`/`ask`/`stop` byte-identical); no new mutable state (`run_body`'s captured `node`/`value` are per-iteration locals, and `modules` is the same pre-existing cache dict `compute`/`route` already share).

### Nits

1. Plan.md's own step-7 proof command, `grep -n "execution" src/sadana/plugin_manifest.py src/sadana/plugins.py` (expected to "return nothing"), actually returns three matches — all pre-existing comment text unrelated to this diff (`plugin_manifest.py:199`, `plugins.py:164`, `plugins.py:387`, all just containing the English word "execution"). The underlying claim (no import of `sadana.execution`) is true and independently confirmed via `grep -n "^import\|^from"` on both files; this is only an imprecise verification recipe in plan.md, not a defect in the change.

## Decision

Approved by Adam Aubry, 2026-09-07, with the Important finding fixed in
this branch before merge: `test_run_graph_call_node_records_what_was_asked`'s
`_capturing_approve` stub corrected to `return False`, matching plan.md's
own originally-written step 3.
