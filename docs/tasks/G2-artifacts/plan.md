# Plan: handing back a link as itself, not as prose (from intent.md 2026-09-07)

Work item: `G2-artifacts`. Spec: `docs/tasks/G2-artifacts/spec.md`.

## Context

`intent.md` names the problem: a `call` node can already reach outside and
get back a link worth handing to a person, but `run_graph` hardcodes
`artifacts=()` on every `DagResult` regardless of what any node did.
`spec.md` designs the fix: when a `call` node's body returns a
`plugins.Artifact`, record it and thread its `.ref` (not the raw object)
forward as the value — scoped to `call` only, no new body signature, no
change to `plugin_dispatch.py`/`conversation.py`/`eval_harness.py`.

Already found and reused, not re-derived here: `run_graph`'s current `call`
branch (`src/sadana/plugin_manifest.py:350-358`, from G1):
```python
elif node.kind == "call":
    approved = await approve(manifest.name, node.name, value)
    if not approved:
        return failed(node, "declined")

    def run_body(n: plugins.Node = node, v: object = value) -> object:
        return _resolve_body(plugin_dir, n, modules)(v)

    value = await asyncio.to_thread(run_body)
```
`trace`'s declaration (`:316`), `result()` (`:320-323`, hardcodes
`artifacts=()`), and the shared post-`try` trace-append
(`:366`, `detail=None` always) are the three other exact points this touches.
`plugins.Artifact` already exists (`plugins.py:114`) with `kind`/`name`/`ref`.

One deliberate refinement beyond spec.md's literal acceptance-criteria
wording: spec.md suggests proving requirement 4 (a plain value still
produces no artifact) by "re-running G1's own
`test_run_graph_call_node_runs_its_body_and_threads_the_result` unmodified."
That proves nothing broke, but that test never asserted `artifacts == ()`
even before this change, so it can't positively prove the plain-value path
stays inert. Adding one assertion line to it is cheap and actually proves
the requirement, rather than merely not-disproving it — done in step 5
below, called out here so it isn't a silent deviation from spec.md's
wording.

## Files that change

- `src/sadana/plugin_manifest.py` — `run_graph`: new `artifacts` local
  accumulator; new `detail` local (mirrors `port`'s existing per-iteration
  pattern); the `call` branch gains an `isinstance` check after the
  threaded body call; `result()`'s hardcoded `artifacts=()` becomes
  `artifacts=tuple(artifacts)`; the shared post-`try` `trace.append(...)`
  uses `detail=detail` instead of the literal `detail=None`.
- `tests/unit/test_plugin_manifest.py`:
  - Import `Artifact` from `sadana.plugins`.
  - New `test_run_graph_call_node_emits_a_link_artifact`.
  - New `test_run_graph_compute_node_returning_artifact_is_not_recorded`.
  - One added assertion line in the existing
    `test_run_graph_call_node_runs_its_body_and_threads_the_result`
    (`result.artifacts == ()`) — see Context above.
- No change: `src/sadana/plugins.py` (the `Artifact`/`DagResult` shapes
  already exist), `src/sadana/plugin_dispatch.py`,
  `tests/unit/test_plugin_dispatch.py` — run unchanged, as proof nothing
  downstream of `run_graph` needed touching.

## Order of work

1. **`plugin_manifest.py`: the core change.**
   ```python
   trace: list[plugins.NodeTrace] = []
   artifacts: list[plugins.Artifact] = []          # new
   value: object = arguments
   current = entry.start

   def result(text: str, failed_node: str | None) -> plugins.DagResult:
       return plugins.DagResult(
           plugin=manifest.name, entry=entry.tool, text=text,
           artifacts=tuple(artifacts), trace=tuple(trace), failed_node=failed_node,
       )
   ```
   In the loop, alongside `port: str | None = None`:
   ```python
   port: str | None = None
   detail: str | None = None                       # new
   ```
   In the `call` branch, after `value = await asyncio.to_thread(run_body)`:
   ```python
   if isinstance(value, plugins.Artifact):
       artifacts.append(value)
       detail = f"emitted {value.kind} artifact {value.name!r}"
       value = value.ref
   ```
   And the shared post-`try` line becomes:
   ```python
   trace.append(plugins.NodeTrace(node=node.name, kind=node.kind, visit=0, ok=True, port=port, detail=detail))
   ```
   `detail` resets to `None` every loop iteration exactly like `port`
   already does, so a previous node's emission never leaks into the next
   node's trace entry — this is the one real correctness property to
   double check once written (step 6 confirms it: any existing test whose
   node sequence includes more than one node, e.g.
   `test_run_graph_walks_compute_ask_route_and_stop_together`, still shows
   `detail=None` for every non-`call` step).

2. **Import `Artifact`** in `tests/unit/test_plugin_manifest.py`'s existing
   `from sadana.plugins import (...)` block.

3. **New test: `test_run_graph_call_node_emits_a_link_artifact`.** An
   `init.py` body that constructs and returns
   `Artifact(kind="link", name="result", ref="https://example.test/thing")`,
   a `call` node with `next="done"` into a `stop` node,
   `approve=_stub_approve_ok`. Assert `result.failed_node is None`,
   `result.artifacts == (Artifact(kind="link", name="result",
   ref="https://example.test/thing"),)`, `result.text ==
   "https://example.test/thing"` (proving requirement 3 — the `.ref`
   threads forward, not the raw object), and the `call` node's own
   `NodeTrace.detail` is not `None`.

4. **New test:
   `test_run_graph_compute_node_returning_artifact_is_not_recorded`.** A
   `compute` node whose body returns an `Artifact` instance directly
   (`compute` never inspects what it returns — this is exactly the point),
   chained into `stop`. Assert `result.artifacts == ()` and
   `result.failed_node is None` — proving requirement 2 (only `call`
   triggers this behavior) by construction rather than by absence of a
   counter-example.

5. **Strengthen the existing G1 test.** Add
   `assert result.artifacts == ()` to
   `test_run_graph_call_node_runs_its_body_and_threads_the_result` — see
   Context above for why this is worth doing over the spec's literal
   "leave it unmodified" suggestion.

6. **Full regression pass.** Run
   `tests/unit/test_plugin_manifest.py`, `tests/unit/test_plugin_dispatch.py`,
   `tests/unit/test_plugins.py`. All green; specifically re-check
   `test_run_graph_walks_compute_ask_route_and_stop_together`'s trace
   assertions still hold with the new `detail` local in place (it asserts
   node names via `[t.node for t in result.trace]`, not `detail`, but this
   is the test most likely to reveal a `detail` leaking across iterations
   if the reset were wrong).

7. **Self-check and verify.** `/ponytail-review` and `/simplify` against
   the diff; apply anything in the "worth taking now" bucket; then
   `make verify`, pasted as this stage's evidence.

## Risks

**What could this change break?** Every existing `run_graph` test's
`NodeTrace` assertions — the shared post-`try` trace-append line changes
from a literal `detail=None` to `detail=detail`. Any existing test that
asserts a full `NodeTrace(...)` tuple with `detail=None` explicitly
(`test_run_graph_stop_node_ends_the_walk` does this) must still produce
`detail=None` for a `stop` node, which it will, since `detail` is
initialized to `None` each iteration and only the new `call`-branch
`isinstance` check ever sets it otherwise. Step 6 is the empirical check.

**Which step is riskiest?** Step 1 — it touches the two closures
(`result()`, and the shared trace-append) that every single node kind's
success path runs through, not just `call`'s own branch. Mitigated by
`detail`'s reset following `port`'s already-proven pattern exactly (same
declaration point, same per-iteration scope), and by step 6 specifically
re-checking the one existing multi-node-kind test in the suite.

**Drift back toward a rejected alternative?** Checked against spec.md's
`## Rejected alternatives`: no second parameter/callback added to a body's
signature (step 1's code takes only `value` in, as before). No
`isinstance` check added to `compute`/`route`/`ask`/`stop` branches (step
1 only touches the `call` branch; step 4 proves a `compute` node's
`Artifact` return is inert by construction). `.ref` is threaded forward,
not the raw `Artifact` (step 1's own code; step 3 asserts `result.text`
equals the bare ref string). No file-writing or output-directory concept
introduced anywhere in this plan.

## Proof

- `test_run_graph_call_node_emits_a_link_artifact` — a `call` node's
  `Artifact` return ends up in `DagResult.artifacts`, and `.ref` (not the
  object) threads to the next node.
- `test_run_graph_compute_node_returning_artifact_is_not_recorded` — the
  identical return type from a `compute` node produces no artifact.
- `test_run_graph_call_node_runs_its_body_and_threads_the_result`
  (strengthened) — a plain-value `call` node still produces
  `artifacts == ()`.
- `tests/unit/test_plugin_dispatch.py`, `tests/unit/test_plugins.py` —
  unchanged, green.
- `make verify` output, ending `VERIFY OK`, pasted as this stage's
  evidence.
