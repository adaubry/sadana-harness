# Plan: letting an approved step actually run (from intent.md 2026-09-07)

Work item: `G1-call-node-run`. Spec: `docs/tasks/G1-call-node-run/spec.md`.

## Context

`intent.md` names the problem: an approved `call` node still doesn't run —
F1 built the approval gate, E1 built the one outward mechanism (`run_http`),
and nothing connects them. `spec.md` designs the fix: `run_graph`'s `call`
branch runs the node's body (the same `_resolve_body` machinery `compute`
already uses) once approved, but off the event loop via `asyncio.to_thread`
— because `call` is definitionally the one kind expected to block, the same
reasoning that made F1 wrap `_default_approve`'s `input()` call the same
way. `plugin_manifest.py` gains no import of `execution.py`; a `call`
node's body stays an opaque callable to the walker, exactly like
`compute`'s.

Already found and reused, not re-derived here: `run_graph`'s current `call`
branch (`src/sadana/plugin_manifest.py:350-352`, landed in F1):
```python
elif node.kind == "call":
    approved = await approve(manifest.name, node.name, value)
    return failed(node, f"{node.kind} steps are not runnable yet" if approved else "declined")
```
`compute`'s own body-resolution pattern (`plugin_manifest.py:337-338`):
`value = _resolve_body(plugin_dir, node, modules)(value)`. `import asyncio`
is already present in this file (added by F1) — no new import needed.

## Files that change

- `src/sadana/plugin_manifest.py` — `run_graph`'s `call` branch: after
  approval, run the body via `_resolve_body` + `asyncio.to_thread` instead
  of unconditionally failing.
- `tests/unit/test_plugin_manifest.py`:
  - `test_run_graph_refuses_call_nodes` (currently line 513) — its own
    premise (approved `call` still fails) is exactly what this item
    removes. Renamed and rewritten to prove the new behavior instead:
    `test_run_graph_call_node_runs_its_body_and_threads_the_result`.
  - `test_run_graph_call_node_records_what_was_asked` (currently line 535)
    — its local `_capturing_approve` stub returns `True` today; under the
    new code that means it now tries to resolve `init:whatever` against an
    empty `init.py` and raises. One-line fix: return `False` instead — the
    test is about what args reached `approve`, not what happens after, so
    the decline path (which never resolves the body) keeps it green
    without needing a working fixture body.
  - New: `test_run_graph_call_node_raising_body_fails_closed`, mirroring
    the existing `test_run_graph_a_raising_body_fails_closed_without_leaking_the_exception`
    (`plugin_manifest.py` test file, currently line 502) but for a `call`
    node.
  - `test_run_graph_call_node_declined` (currently line 524) — verified to
    need **no change**: it already uses an empty `init.py` with
    `body="init:whatever"`, and the declined path returns before
    `_resolve_body` is ever reached, so it already structurally proves the
    body is never resolved on decline. Running it unchanged is part of
    this item's own proof.
- No change: `src/sadana/plugins.py`, `src/sadana/plugin_dispatch.py`,
  `tests/unit/test_plugin_dispatch.py` — run unchanged, as proof the
  walker stayed body-agnostic and no other caller is affected.

## Order of work

1. **`plugin_manifest.py`: change the `call` branch.**
   ```python
   elif node.kind == "call":
       approved = await approve(manifest.name, node.name, value)
       if not approved:
           return failed(node, "declined")
       value = await asyncio.to_thread(_resolve_body(plugin_dir, node, modules), value)
   ```
   Falls through to the same `trace.append(...)`/`next_name` logic every
   other successful node kind already uses — no change needed there.

2. **Run `tests/unit/test_plugin_manifest.py` as-is** (no test edits yet).
   Expect exactly two failures: `test_run_graph_refuses_call_nodes` (now
   hits `_resolve_body` against a missing `init:whatever` and raises,
   instead of the old "not runnable yet") and
   `test_run_graph_call_node_records_what_was_asked` (same reason, since
   its stub returns `True`). This is the empirical confirmation that the
   code change actually took effect, before touching any test.

3. **Fix `test_run_graph_call_node_records_what_was_asked`**: change its
   local `_capturing_approve` to `return False`. Re-run; this one test
   should go green.

4. **Rewrite `test_run_graph_refuses_call_nodes`** into
   `test_run_graph_call_node_runs_its_body_and_threads_the_result`: give
   it its own `init.py` (`def do_call(value): return {"called": value}`),
   a `call` node with `next="done"` into a `stop` node, `approve=_stub_approve_ok`.
   Assert `result.failed_node is None` and `result.text` reflects the
   body's return value — proving the value actually threads to the next
   node, not just that the call succeeded.

5. **Add `test_run_graph_call_node_raising_body_fails_closed`**: an
   `init.py` function that raises (mirroring the existing compute version
   at line 502), a `call` node, `approve=_stub_approve_ok`. Assert
   `failed_node` is set and no exception detail leaks into `result.text`.

6. **Full regression pass**: run
   `tests/unit/test_plugin_manifest.py`, `tests/unit/test_plugin_dispatch.py`,
   `tests/unit/test_plugins.py`. All green, the latter two byte-identical
   to their pre-diff behavior.

7. **Grep-confirm no new import**: `grep -n "execution" src/sadana/plugin_manifest.py src/sadana/plugins.py`
   returns nothing — the walker stayed body-agnostic, per spec's own
   acceptance criterion.

8. **Self-check and verify**: `/ponytail-review` and `/simplify` against
   the diff; apply anything in the "worth taking now" bucket; then
   `make verify`, pasted as this stage's evidence.

## Risks

**What could this change break?** Exactly two existing tests, both
identified above by reading their fixtures against the new code path
before writing anything (`test_run_graph_refuses_call_nodes` and
`test_run_graph_call_node_records_what_was_asked`) — no other test in the
suite constructs a `call`-kind `Node`. `test_run_graph_call_node_declined`
is the other call-node test and is verified, by tracing the new code's
control flow, to need no change — step 6 is where that's proven
empirically rather than assumed.

**Which step is riskiest?** Step 1 — it's the third time this exact
dispatch loop has changed (D-series built it, F1 added the approval
branch, this adds body execution). Ordered so step 2 immediately shows,
empirically, everything the change disturbs before any test is edited —
red for a known, expected reason, not a surprise discovered later.

**Drift back toward a rejected alternative?** Checked against spec.md's
`## Rejected alternatives`: the body is resolved via the same
`_resolve_body` call `compute` already uses, not a new
`execution`-specific dispatch (step 1, confirmed above — step 7 is the
grep that proves it). The body runs via `asyncio.to_thread`, not in-line
like `compute` (step 1's own code). The body stays a plain sync callable —
no `async def` requirement introduced anywhere in this plan. No new live
external round-trip script is added; the new tests use fake `init.py`
functions exactly like the existing `compute`/`route` tests already do.

## Proof

- `test_run_graph_call_node_runs_its_body_and_threads_the_result` — an
  approved `call` node's body runs and its return value becomes the next
  node's input, ending the walk with `failed_node is None`.
- `test_run_graph_call_node_raising_body_fails_closed` — a raising `call`
  body ends the walk generically, no leaked exception text.
- `test_run_graph_call_node_declined` (unchanged) — decline still never
  resolves the body.
- `test_run_graph_call_node_records_what_was_asked` (one-line fix) — still
  proves the exact args passed to `approve`.
- `grep -n "execution" src/sadana/plugin_manifest.py src/sadana/plugins.py`
  — no output, proving the walker imports nothing from `sadana.execution`.
- `tests/unit/test_plugin_dispatch.py`, `tests/unit/test_plugins.py` —
  unchanged, green.
- `make verify` output, ending `VERIFY OK`, pasted as this stage's
  evidence.
