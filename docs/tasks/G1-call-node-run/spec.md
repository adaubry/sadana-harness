# Spec: letting an approved step actually run

Intent: docs/tasks/G1-call-node-run/intent.md

## Requirements

1. Once `approve()` returns `True` for a `call` node, the walk actually runs
   that node's body and uses what it returns as the value the next node
   receives — replacing the unconditional `failed(node, "call steps are not
   runnable yet")` an approved `call` node hits today. Traces to Proposed
   outcome ("an approved step finally does something instead of stopping
   right after being approved").
2. A `call` node's body is resolved and shaped exactly like every other
   node's body — `module:function` in the plugin's own `init.py`, given
   only the immediate predecessor's value, returning the value the next
   node receives. Traces to Proposed outcome ("what came back become the
   input to whatever step comes next") and CLAUDE.md's existing rule that a
   plugin graph's node receives only its immediate predecessor's output.
3. Nothing about what a step is allowed to reach outside to is widened,
   narrowed, or re-decided here — a `call` node's body is free to do
   whatever in-process, first-party code it is written to do, the same way
   a `compute` node's body already is. Traces to Constraints ("Nothing here
   reopens what 'reaching outside' is allowed to mean... does not widen it,
   add a second way to reach outside, or add sandboxing").
4. The declined path is unchanged: a `call` node whose `approve()` returns
   `False` still ends the walk with `detail == "declined"`, without
   resolving or attempting to run its body. Traces to Constraints (nothing
   here reopens the approval gate F1 already built).
5. Nothing here returns an `Artifact`, and nothing here touches
   `scripts/prove_conversation_e2e.py`. Traces to Constraints (both named
   explicitly as separate, later concerns).

## Design

**Where this attaches.** `src/sadana/plugin_manifest.py:run_graph`'s `call`
branch today (landed in F1) is:

```python
elif node.kind == "call":
    approved = await approve(manifest.name, node.name, value)
    return failed(node, f"{node.kind} steps are not runnable yet" if approved else "declined")
```

The declined path stays exactly as it is. The approved path changes from an
unconditional `failed()` to actually running the node's body — the same
`_resolve_body(plugin_dir, node, modules)` machinery `compute` and `route`
already share, D2's own load-time validation already checked it resolves
(`_check_body` runs for any node with `node.body` set, not kind-specific —
`call` nodes have always been covered by it), and D3/D4 already proved for
`compute`'s own body:

```python
elif node.kind == "call":
    approved = await approve(manifest.name, node.name, value)
    if not approved:
        return failed(node, "declined")
    value = await asyncio.to_thread(_resolve_body(plugin_dir, node, modules), value)
```

**The one real decision: off the event loop, unlike `compute`.** `compute`'s
body is invoked in-line (`value = _resolve_body(...)(value)`) because it is
assumed fast and pure — `plugin_blueprint.md §6`'s own reasoning for keeping
`compute`/`call` separate kinds is exactly this line: `compute` is pure,
`call` reaches outside. `call` is the one kind whose entire reason to exist
is doing something that can block — a real outward HTTP request
(`src/sadana/execution.py:run_http`) is a synchronous, blocking call by its
own design (E1's spec: stdlib `urllib`, matching `openrouter/provider.py`'s
own transport). Running it in-line inside `run_graph`'s `async def` would
block the event loop for the duration of every outward request — the exact
hazard `_default_approve` (F1) was built to avoid for a different blocking
call in this same function, via `asyncio.to_thread`. This item applies that
already-established pattern to the new kind that is *definitionally* the
one expected to need it, rather than re-deriving a new answer.

**`run_graph` gains no new import.** `sadana.execution` is never imported
by `plugin_manifest.py` or `plugins.py`. A `call` node's body is, from
`run_graph`'s point of view, an opaque `Callable[[object], object]` — the
exact same contract `compute`'s body already has. Whether that function's
own code calls `sadana.execution.run_http`, does nothing at all, or (today,
nothing stops it) does something else entirely in-process is the plugin
author's own business, resolved through `init.py` the same way `compute`'s
body already is. This is what keeps the answer to intent's own live design
question — does the walker need to know `execution.py` exists — a plain
no: the walker's job is "resolve a body, run it without blocking, thread
its result forward," identically for `compute` and `call`, and it was
never `plugin_manifest.py`'s job to know what a body's code does.

**No new module.** This is a small edit to `run_graph`'s existing `call`
branch, not a new file — there is no real I/O added to `plugin_manifest.py`
itself (it still never touches the network or disk beyond what `_resolve_body`
already does to import `init.py`); the real I/O, if any, lives inside
whatever a specific plugin's own `init.py` body does, in that plugin's own
file, which is exactly where CLAUDE.md's "a module that touches real I/O is
its own file" rule already puts it — `execution.py`, not this module.

## Interface

**In:** a `call` node whose `approve()` already returned `True`; `value`,
the immediate predecessor's output (identical to `compute`'s own input
contract).
**Out:** the body's return value becomes the next node's `value`, exactly
as `compute`'s already does. Declining still returns `failed(node,
"declined")`, unchanged from F1.
**Errors:** none raised across this item's own boundary. Any exception the
body raises (or that `asyncio.to_thread` propagates from it) is caught by
`run_graph`'s existing per-node `try`/`except Exception` — the same
`failed(node, f"node raised {type(e).__name__}")` outcome any other
raising body already produces, with no special case for `call`.

## Acceptance criteria

- [ ] An approved `call` node's body is resolved via `_resolve_body` and
      run via `await asyncio.to_thread(...)`, never invoked in-line.
- [ ] A successful body's return value becomes the value the next node in
      the walk receives — covered by a unit test chaining a `call` node
      into a `stop` node and asserting the final `text`.
- [ ] A declined `call` node still ends with `detail == "declined"` and
      never resolves or invokes its body — covered by extending
      `test_run_graph_call_node_declined` (or an equivalent assertion) to
      confirm the body function was never called (e.g. a body that raises
      if invoked, mirroring the existing "`approve` must not be called for
      compute/ask/route/stop" test's own technique, applied in reverse).
- [ ] A raising `call` body ends the walk the same generic way any other
      node's raising body already does (`failed_node` set, no traceback in
      `text`) — covered by a unit test using an `init.py` function that
      raises.
- [ ] `src/sadana/plugin_manifest.py` and `src/sadana/plugins.py` import
      nothing from `sadana.execution` — grep-checkable, and the fact this
      criterion is checkable at all is the proof the walker stayed
      body-agnostic.
- [ ] `tests/unit/test_plugin_dispatch.py` passes unchanged — no `call`
      node exists in its fixtures, so this item's change is inert there,
      the same regression check F1 already established as a pattern.
- [ ] `make verify` ends `VERIFY OK`.

## Non-goals

- Returning an `Artifact` from a `call` node — PLUGINS' execution half,
  item 7, a separate later work item.
- Porting `scripts/prove_conversation_e2e.py`'s hand-written DAG into real
  plugins — item 8, a separate later work item; this item does not touch
  that script.
- Widening what a `call` body may reach outside to, or adding a second
  outward mechanism beside `execution.run_http` — EXECUTION's own scope
  (E1), unchanged here.
- Async `call` bodies (`async def`, awaited directly) — bodies stay plain
  sync callables, matching `compute`'s existing contract; see Rejected
  alternatives.
- Enforcing that a `call` node's body actually goes through
  `execution.run_http` rather than doing something else in-process — see
  Concerns; not decidable without sandboxing, which is out of scope by the
  same constraint E1 already declined it under.

## Rejected alternatives

**`plugin_manifest.py` importing `sadana.execution` and dispatching a
`call` node's body through `run_http` directly**, rather than treating the
body as an opaque callable the way `compute`'s already is — declined. This
would hard-code one specific outward mechanism into the walker itself,
contradicting `plugin_blueprint.md §5.2`'s "one shape all the way down" (a
`call` node's body is code the walker never inspects, exactly like
`compute`'s) and would force every future outward mechanism — a second
EXECUTION backend, if one is ever designed — to touch `plugin_manifest.py`
again instead of arriving as a new `init.py` function a plugin author
simply imports. The existing pattern already generalizes correctly; `call`
differs from `compute` only in "must not block the event loop," not in
"what code it may contain."

**Invoking a `call` node's body in-line, exactly like `compute`**
(`value = _resolve_body(...)(value)`, no thread offload) — declined. `call`
is definitionally the one kind with an outside effect (§6), and this
project's own EXECUTION mechanism (`run_http`) is a genuinely blocking
call. Leaving it unwrapped would block the whole event loop for the
duration of every outward request, silently reintroducing the exact hazard
F1's own `_default_approve` was built specifically to avoid for a different
blocking call in this same function. Consistency with `compute`'s shape is
not worth reopening a hazard this project already named and fixed once.

**Requiring a `call` node's body to be `async def`, awaited by `run_graph`
directly instead of run via `asyncio.to_thread`** — declined.
`execution.run_http` is deliberately synchronous (E1's spec: stdlib only,
matching `openrouter/provider.py`'s own sync transport); requiring plugin
authors to write async bodies for `call` while `compute`/`route` stay plain
sync functions would be an inconsistent authoring experience bought for no
real benefit — `asyncio.to_thread` gets the identical non-blocking property
without asking a `call` node's author for anything `compute`'s author
doesn't already provide.

**A new live external round-trip proof script as part of this item's
evidence** — declined. E1 already produced that proof for `run_http` itself
as EXECUTION's own deploy evidence; this item wires an already-proven
mechanism into the walker, and the wiring itself is fully provable with the
same synchronous fake-body technique the existing `compute`/`route` unit
tests already use. Porting the real end-to-end proof
(`scripts/prove_conversation_e2e.py`) into two real plugins is item 8's
explicit job, not this one's — building it here would be exactly the kind
of bundling intent.md already split apart.

## Concerns

**The paved path is still not an enforced boundary, and this is the item
where an author's own code first gets connected to it.** Nothing here
verifies a `call` node's body actually calls `execution.run_http` rather
than opening a raw socket, shelling out, or doing anything else in-process
— the same gap E1's own spec already named about `run_http` itself
(sandboxing is explicitly out of scope, per E1's intent). Worth restating
here specifically because this is the first work item where that gap
becomes reachable rather than theoretical: a plugin's `init.py` can now
genuinely do something when a `call` node runs.

**The approval gate's integrity depends on an honest `kind` declaration,
not on runtime enforcement, and this item makes that load-bearing for the
first time.** Because `run_graph` treats `call` and `compute` identically
except for the approval check and the thread-offload, nothing stops a
plugin author from declaring a node `kind = "compute"` while its body
actually calls `execution.run_http` — sidestepping F1's gate entirely with
no structural violation anywhere. Before this item, that fact was inert
(neither kind could reach outside, so misdeclaring one bought nothing);
after this item, it is a real, if currently low-stakes, trust boundary.
Every plugin today is first-party (E1/F1's own constraint, unchanged here),
so this is an accepted boundary, not a new one — but a reviewer should see
it named at the point it starts to matter rather than discover it later.

**Policy conformance.** `testing-conventions` applies: the new tests use a
synchronous fake `init.py` function and never touch the network, matching
the existing `compute`/`route` tests in `tests/unit/test_plugin_manifest.py`
exactly — no new policy tension. `project-structure` and `reference-lookup`
do not exist as separate skills in this repository (same finding E1's and
F1's own specs made); CLAUDE.md's "Our methodology for learning from the
reference" is this project's actual reference-lookup policy, and it is
what guideline 1's research above follows.

**No CLAUDE.md-binding rule established by this spec.** Unlike F1, this
item adds no new invariant beyond what F1's own CLAUDE.md line already
states (`call` bodies run only after `approve()` returns `True`) — this
item is the mechanical fulfillment of that line, not a new rule.

**No other policy conflict found.** The change is a small, mechanical
extension of an already-proven pattern (`compute`'s own body resolution)
plus one already-proven mitigation (`asyncio.to_thread`, from F1) applied
to a new call site; nowhere in this design did two of the project's own
rules pull in different directions.
