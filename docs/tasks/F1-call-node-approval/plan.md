# Plan: asking before a step reaches outside (from intent.md 2026-09-07)

Work item: `F1-call-node-approval`. Spec: `docs/tasks/F1-call-node-approval/spec.md`.

## Context

`intent.md` names the problem: a plugin's `call` node (the one kind with an
outward effect) runs with no chance for the person to stop it first.
`spec.md` designs the fix: an injected `approve` callable, mirroring the
existing `ask` callable exactly, gating only the `call` branch of
`run_graph`'s existing per-node dispatch loop. This plan is the
implementation of that design — no new decisions, just sequencing.

Already found and reused, not re-derived here: `run_graph`
(`src/sadana/plugin_manifest.py:252-335`) already branches on `node.kind`
once per step; `call` currently shares a guard with `each`/`wait`
(`plugin_manifest.py:304-305`) that unconditionally returns `failed(node,
"... steps are not runnable yet")`. `plugins.py:408` already has
`AskFn = Callable[[SkillRef, str], Awaitable[str | None]]`, the exact shape
`ApproveFn` mirrors. `plugin_dispatch.py:104-166`'s `build_dispatch` calls
`run_graph(...)` today with no `approve=` — it needs no change, per spec's
Design section, because the new default is this project's real production
behavior for its one interactive caller.

## Files that change

- `CLAUDE.md` — add the "Please do" rule this spec establishes (`## 2` of
  `spec.md`'s brief): a `call` node's body runs only after `approve()`
  returns `True`.
- `src/sadana/plugins.py` — add `ApproveFn` type alias.
- `src/sadana/plugin_manifest.py` — add `import asyncio`; add
  `_default_approve`; add `approve` parameter to `run_graph`; split `call`
  out of the `each`/`wait` guard into its own branch.
- `tests/unit/test_plugin_manifest.py` — update
  `test_run_graph_refuses_call_nodes` (currently line 488); add four new
  tests (two for `_default_approve`, two for the wired gate).
- No change: `src/sadana/plugin_dispatch.py`,
  `tests/unit/test_plugin_dispatch.py` — run unchanged, as proof the design
  holds.

## Order of work

1. **`plugins.py`: add `ApproveFn`.**
   ```python
   ApproveFn = Callable[[str, str, object], Awaitable[bool]]
   ```
   Placed directly below `AskFn = Callable[[SkillRef, str], Awaitable[str | None]]`
   (`plugins.py:408`). No behavior change; verified by import alone.

2. **`plugin_manifest.py`: add `_default_approve`, proven in isolation
   before anything wires it up.**
   ```python
   async def _default_approve(plugin: str, node: str, value: object) -> bool:
       prompt = f"{plugin}'s {node!r} step wants to run with input {value!r}. Allow it? [y/N] "
       answer = await asyncio.to_thread(input, prompt)
       return answer.strip().lower() in ("y", "yes")
   ```
   Needs `import asyncio` added to the module's existing import block.
   Two new tests land with it, both monkeypatching `builtins.input` (never
   real stdin):
   - `test_default_approve_accepts_yes` — `input` returns `"y"` (and
     separately `"yes"`, `"Y"`) → `True`.
   - `test_default_approve_rejects_anything_else` — `input` returns `""`,
     `"n"`, `"maybe"` → `False` in every case (fails closed).
   Run just these two (`pytest -k default_approve`, through `make test`'s
   underlying runner — see Proof) before touching `run_graph` at all.

3. **`plugin_manifest.py`: wire the gate into `run_graph`.**
   - Signature gains `approve: plugins.ApproveFn = _default_approve` as a
     new keyword-only parameter, after `ask`.
   - The existing guard
     ```python
     if node.kind in ("call", "each", "wait"):
         return failed(node, f"{node.kind} steps are not runnable yet")
     ```
     becomes
     ```python
     if node.kind in ("each", "wait"):
         return failed(node, f"{node.kind} steps are not runnable yet")
     ```
   - Inside the existing `try` block, alongside the `compute`/`route`/
     `ask`/`stop` branches, add:
     ```python
     elif node.kind == "call":
         if not await approve(manifest.name, node.name, value):
             return failed(node, "declined")
         return failed(node, "not runnable yet")
     ```
   - Update `run_graph`'s docstring to mention `approve` the same way it
     already documents `ask`.

4. **`test_plugin_manifest.py`: update and extend the call-node tests.**
   - `test_run_graph_refuses_call_nodes` (line 488): pass an explicit
     `approve=_stub_approve_ok` (new stub, mirrors `_stub_ask_ok`'s shape)
     instead of relying on the new default — the unit suite must never
     touch real stdin. Assertion stays: `failed_node == "future"`,
     `"not runnable yet" in detail`.
   - New `_stub_approve_ok`, `_stub_approve_denied`, `_capturing_approve`
     helpers next to the existing `_stub_ask_*`/`_capturing_ask` ones
     (around line 401-410).
   - New `test_run_graph_call_node_declined`: `approve=_stub_approve_denied`
     → `result.failed_node == "future"`, `result.trace[-1].detail ==
     "declined"`.
   - New `test_run_graph_call_node_records_what_was_asked`: uses
     `_capturing_approve` to assert it was called with
     `(manifest.name, node.name, value)` — proves the gate passes the real
     plugin name, node name, and in-flight value, not placeholders.

5. **Regression pass.** Run the full `tests/unit/test_plugin_manifest.py`
   and `tests/unit/test_plugin_dispatch.py` files. Every pre-existing test
   that passes `ask=` but not `approve=` must still pass unchanged — this
   is the empirical proof that defaulting `approve` (spec's Rejected
   alternatives, "no default for approve... declined") was the right call,
   not just an assumption.

6. **Self-check and verify.** `/ponytail-review` and `/simplify` against
   the diff; apply anything in the "worth taking now" bucket; then
   `make verify`, pasted as this stage's evidence.

## Risks

**What could this change break?** Every existing `run_graph` caller and
test that supplies `ask=` but not `approve=` (roughly a dozen in
`test_plugin_manifest.py`, plus `build_dispatch`'s production closure in
`plugin_dispatch.py`). None of their fixture manifests route through a
`call` node today, so the new default parameter should be inert for all of
them — step 5 is exactly the check that this is true rather than assumed.
`test_plugin_dispatch.py` is the other named risk: it exercises
`build_dispatch` end to end, which now transitively depends on
`plugin_manifest.run_graph`'s new signature; if any of its fixtures do
route through a `call` node, real stdin (`_default_approve`) would hang the
suite. Checked directly in step 5, before calling anything else done.

**Which step is riskiest?** Step 3 — it edits an existing, working
function's control flow (splitting one guard clause into two, adding a new
branch inside the shared `try`). It is ordered after step 2, so
`_default_approve` is already proven correct in isolation by the time it's
wired in; the only new risk left at step 3 is the wiring itself, not the
default's own logic. It is also ordered before step 4's new tests exist,
so step 3 will visibly break `test_run_graph_refuses_call_nodes` (still
asserting the old shared-guard behavior) until step 4 updates it — expected
red, not a surprise regression, and the reason steps 3 and 4 are adjacent
rather than separated by other work.

**Drift back toward a rejected alternative?** Checked against spec.md's
`## Rejected alternatives`: plain positional args on `ApproveFn`, not an
`ApprovalRequest` dataclass (step 1, confirmed above). `approve` defaulted,
not a mandatory keyword like `ask` (step 3, confirmed above — and step 5 is
what proves the default was safe to add, not just convenient). Gate lives
inside `run_graph`'s loop, not in `plugin_dispatch.build_dispatch`'s entry
point (step 3 only touches `plugin_manifest.py`; step 5 confirms
`plugin_dispatch.py` needs zero changes). No new module-level state
anywhere in this plan — `_default_approve` is a pure per-call function,
unlike hermes's session-keyed wait-state dicts spec.md declined.

## Proof

- `test_default_approve_accepts_yes`, `test_default_approve_rejects_anything_else`
  — `_default_approve` covered in isolation, `builtins.input` monkeypatched,
  no real stdin touched.
- `test_run_graph_refuses_call_nodes` (updated) — approval granted still
  ends in "not runnable yet", now proven to have actually asked first.
- `test_run_graph_call_node_declined` — decline ends the walk with
  `failed_node` set and `detail == "declined"`, distinguishable from "not
  runnable yet".
- `test_run_graph_call_node_records_what_was_asked` — `approve` is called
  with the real plugin name, node name, and in-flight value, not stubs of
  its own.
- `tests/unit/test_plugin_dispatch.py` — every existing test passes with
  zero modification, proving the design's central claim (no change needed
  there).
- `make verify` output, ending `VERIFY OK`, pasted as this stage's evidence.
