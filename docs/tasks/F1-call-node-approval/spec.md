# Spec: asking before a step reaches outside

Intent: docs/tasks/F1-call-node-approval/intent.md

## Requirements

1. When the walk reaches a `call` node, before anything else happens for it,
   the run asks whether it may proceed and waits for an answer before doing
   anything further with that node. Traces to Proposed outcome ("Before a
   step that reaches outside the process runs, the person is asked whether
   it may proceed").
2. If the answer is no, the run stops at that node and reports that it was
   declined — not "not runnable yet", not silently skipped, not continued
   past. Traces to Proposed outcome ("If they decline, the run stops there
   and reports that it was declined").
3. `ask`, `compute`, `route`, and `stop` nodes are never asked about — the
   check exists for exactly the one kind whose definition is an outside
   effect. Traces to Proposed outcome ("A step with no outside effect is
   never interrupted this way") and Constraints (every outward-reaching
   step is asked about, full stop — no finer per-step exemption, and no
   widening the check to kinds that were never in question either).
4. The check happens once, inside the same call that is already running —
   nothing here introduces a way to pause a run and come back to it later.
   Traces to Constraints ("The check must be answerable in the same moment
   the run is happening").
5. No decision is remembered across calls, no automatic rule decides on the
   person's behalf, and this item does not fix what the asking interface
   looks like beyond "it blocks until answered." Traces to Constraints
   (declines an allowlist, an auto-approval policy, and a specific
   interface).

## Design

**Where this attaches.** `src/sadana/plugin_manifest.py:run_graph` already
branches on `node.kind` once per step of the walk (D3/D4/E1's own
machinery); today `call` shares a branch with `each`/`wait` that
unconditionally returns `failed(node, "... steps are not runnable yet")`
(`plugin_manifest.py:304-305`). `call` is a real, well-defined kind that
happens to have no body-execution mechanism wired to it yet — `each`/`wait`
have no execution mechanism *or* a real definition yet (`each` is deferred,
`wait` is Phase 2, per `plugin_blueprint.md §6`). Splitting `call` out of
that shared branch is the actual work of this item; `each`/`wait` keep the
unconditional refusal exactly as it is.

**The callable, mirroring an existing one exactly.** `plugins.py` already
has `AskFn = Callable[[SkillRef, str], Awaitable[str | None]]` — an
injected, judgement-carrying callable the graph walker calls and awaits,
with no registry or base class around it. This item adds one sibling:

```python
ApproveFn = Callable[[str, str, object], Awaitable[bool]]
# (plugin name, node name, the value about to reach that node) -> may it run?
```

next to `AskFn` in `plugins.py`. Same file, same shape, same reasoning —
`plugins.py` stays the pure/data half; the callable's *type* is data-shaped
description of a boundary, not the boundary itself.

**The gate, inside `run_graph`'s existing try block.** `run_graph` gains one
new keyword parameter, `approve: plugins.ApproveFn = _default_approve`, and
its node-kind dispatch grows one branch, placed alongside `compute`/`route`/
`ask`/`stop` inside the same `try` that already turns any node's stray
exception into a uniform `failed()` outcome:

```python
elif node.kind == "call":
    if not await approve(manifest.name, node.name, value):
        return failed(node, "declined")
    return failed(node, "not runnable yet")
```

`each`/`wait` stay in the earlier unconditional-refusal guard, now naming
only those two kinds. Approval runs *before* anything that would resolve or
invoke the node's body — there is no body-execution path for `call` yet
(EXECUTION/E1 built only the underlying outward-request mechanism, not its
wiring into a `call` node — E1's own intent and spec both decline that
wiring explicitly), so an approved `call` node still ends the walk with
`failed_node` set. This is the real shape of `plugin_blueprint.md §2.4`'s
own step order: SAFETY (step 15, this item) is built *before* PLUGINS'
execution half wires real `call`-node bodies (step 16, item 6, not yet
started) — the gate exists first, as a seam item 6 will run behind, not the
other way around. See Concerns.

**The default implementation, in the I/O half.** `plugin_manifest.py`
already carries this block's real-I/O code (`load_skill`, schema/module
resolution) separately from `plugins.py`'s pure data and parsing, per
CLAUDE.md's rule that a module touching real I/O is its own file regardless
of size. The default `approve` belongs there, as the one concrete "ask a
real person" implementation for this project's one real caller today:

```python
async def _default_approve(plugin: str, node: str, value: object) -> bool:
    prompt = f"{plugin}'s {node!r} step wants to run with input {value!r}. Allow it? [y/N] "
    answer = await asyncio.to_thread(input, prompt)
    return answer.strip().lower() in ("y", "yes")
```

`asyncio.to_thread` (stdlib) keeps the blocking `input()` call off the event
loop the rest of a `dispatch` call is running on — the same reason
`run_child`'s own model calls are awaited rather than run inline. No new
dependency; nothing to register.

**Nothing changes in `plugin_dispatch.py`.** `build_dispatch`'s `dispatch()`
closure calls `run_graph(...)` today without an `approve=` argument and
needs no change: the new default *is* the real production behavior for this
project's one caller — a person at a WSL terminal, synchronously, which is
exactly what intent's Affected users and Constraints describe. A future
non-interactive caller (see Concerns) is the one that will need to pass its
own `approve`.

## Interface

**In:** `approve(plugin: str, node: str, value: object) -> bool`, awaited
exactly once per `call` node the walk actually reaches — never for
`ask`/`compute`/`route`/`stop`, and never more than once for the same node
(the walk cannot revisit a node; see `run_graph`'s own acyclicity note).
**Out:** `True` lets the walk fall through to the existing "not runnable
yet" outcome for that node; `False` ends the walk at that node with
`failed_node` set and a trace `detail` of `"declined"`, distinguishable from
`"call steps are not runnable yet"`.
**Errors:** none raised across this boundary. An exception from `approve`
itself is caught by `run_graph`'s existing per-node `try`/`except` and
turned into the same generic `failed()` outcome any other node's stray
exception already produces — no special case for this callable.

## Acceptance criteria

- [ ] `plugins.ApproveFn` exists next to `AskFn`, same shape of definition.
- [ ] `run_graph` takes `approve: plugins.ApproveFn = _default_approve` and
      calls it exactly once, only when the walk reaches a `call` node,
      before anything else happens for that node.
- [ ] Declining ends the walk at that node: `failed_node` set, trace
      `detail == "declined"`.
- [ ] Approving still ends the walk at that node (no execution mechanism
      exists yet): `failed_node` set, trace `detail` containing "not
      runnable yet" — same outcome text the pre-existing behavior already
      produced, now reached only after being asked.
- [ ] `ask`/`compute`/`route`/`stop` node walks never call `approve` —
      covered by asserting an `approve` stub that raises if called is never
      triggered on any existing non-`call` test path.
- [ ] `_default_approve` is covered by a unit test that monkeypatches
      `builtins.input` (never drives real stdin) for both a "yes" and a
      "no" answer, including at least one non-exact-match answer (e.g.
      empty string, "yeah") resolving to `False` — approval fails closed.
- [ ] `test_run_graph_refuses_call_nodes` (`tests/unit/test_plugin_manifest.py:488`)
      updated to pass an explicit fake `approve` that returns `True`,
      rather than relying on the new default, so the unit suite never
      touches real stdin; its existing assertion ("not runnable yet")
      stays and now also implies the fake was actually awaited.
- [ ] Two new tests alongside it: one asserting the declined path
      (`approve` returns `False` → `detail == "declined"`, and the fake
      `approve` was called with the reached node's own name and the value
      flowing into it); one asserting the approved path falls through to
      the existing "not runnable yet" outcome.
- [ ] `tests/unit/test_plugin_dispatch.py`'s existing tests pass unchanged —
      no `approve=` threaded through `build_dispatch`/`run_graph` there, by
      design (Design, "Nothing changes in `plugin_dispatch.py`").
- [ ] `make verify` ends `VERIFY OK`.

## Non-goals

- Wiring a `call` node's body to actually execute anything — PLUGINS'
  execution half, item 6, a separate, later work item (and, per
  `plugin_blueprint.md §2.4`, one that comes *after* this one).
- Remembering a past decision, an auto-approval policy, or any particular
  interface beyond "blocks until answered" — intent's own Constraints.
- A per-node exemption from the gate — intent's own Constraints, decided
  during planning.
- Approval that outlives one `dispatch` call (async/durable approval) —
  intent's own Open questions; `plugin_blueprint.md §7.2` forecloses it for
  the whole block, not just this item.

## Rejected alternatives

**Hermes's `tools/approval.py`** (`../hermes-agent/tools/approval.py`,
~264KB — too large to read whole; surveyed via `grep -n "^def \|^class "`
and read in full around its session/timing machinery,
`tools/approval.py:2598-2720`) — declined outright, matching the user's own
framing of this work item. It carries: per-session-key locks and dicts
tracking human-wait windows with an eviction policy under a 256-session cap;
a wait-time ceiling shared with an unrelated deadline system elsewhere in
the codebase so the two "cannot drift apart"; separate detection functions
for cron, gateway, single-query, and unattended-platform approval contexts;
a denial-breaker threshold with its own reset function; a gateway
notify/resolve callback registry. Every one of these exists to answer a
question this item's own intent explicitly declines — concurrent sessions,
unattended/cron/gateway callers, a shared timeout budget, remembering past
denials. None of it is "approval done differently," it is approval answering
questions this project doesn't have yet; adopting any piece of it would mean
importing that population before a second member of it exists, which
CLAUDE.md's own rule on registries and seams already declines by name.

**Hermes's `agent/tool_guardrails.py`** — read in full
(`../hermes-agent/agent/tool_guardrails.py`, 855 lines). Same SAFETY block,
but a different question: a per-turn loop detector (repeated/failing/
idempotent-call heuristics, runaway-loop caps on specific tool names), never
a human-in-the-loop approval gate. Noted here so it doesn't read as
overlooked — nothing in it is analogous enough to adopt or decline against.

**Gating at `plugin_dispatch.build_dispatch`'s `dispatch()` entry point**
instead of inside `run_graph`'s per-node loop — declined. It would ask once
per entry call regardless of whether that particular run's path ever
reaches a `call` node (a `route` can send the walk down a call-free branch),
asking too broadly and on the wrong node's behalf. `run_graph` already
performs the exact per-node-kind check this needs; adding a second, coarser
check one layer up is guideline 3's "add a step" in the wrong place when a
cheaper, exactly-targeted one already exists in the loop that owns node
kinds.

**An `ApprovalRequest` dataclass argument** instead of plain positional
`(plugin, node, value)` — declined. `AskFn`'s own precedent
(`Callable[[SkillRef, str], Awaitable[str | None]]`) already uses positional
arguments for an injected judgement callable in this exact module; no
second `ApproveFn` caller exists yet to justify grouping three values into a
named type ("no unrequested abstractions" — CLAUDE.md).

**No default for `approve`, mirroring `ask`'s own required keyword** —
declined. `ask` has no default because there is no safe default judgement;
"block on a real human via stdin" is a genuine, safe default meaning for
approval in this project's current single-operator, synchronous-only shape
— closer to `persist`'s own defaulted keyword in `build_dispatch` than to
`ask`'s mandatory one. A mandatory keyword would force every existing
`run_graph` call site that never reaches a `call` node (roughly a dozen, in
`tests/unit/test_plugin_manifest.py`) to pass a value for a concern they
don't touch, for zero behavioral gain.

## Concerns

**"Approved" and "declined" both end the walk the same way today, and that
is deliberate, not a leftover bug.** Until PLUGINS' execution half (item 6)
wires a real body to `call` nodes, the model-visible `text` is the same
generic sentence either way (`DagResult.text` never leaks `NodeTrace.detail`
to the model, by `run_graph`'s own existing docstring guarantee); only the
trace's `detail` field distinguishes "declined" from "not runnable yet" —
and that distinction is invisible outside a test or a transcript reader
today. This is the real shape of building the gate before the thing it
gates exists to run, per `plugin_blueprint.md §2.4`'s own step order
(SAFETY before PLUGINS execution half). Naming it here so whoever reviews
this against intent.md doesn't read "approved but nothing happened" as
incomplete work — it is exactly item 6's job to make "approved" mean
something more than that, later.

**`_default_approve` blocks on real stdin — correct for today's one
interactive caller, a hazard for a future one.** A future non-interactive
`dispatch` caller that installs a real `call`-carrying plugin (a scheduled
job, a batch eval) and does not override `approve` will hang forever on
`input()`. Nothing today is exposed: `eval_harness.py`'s own dispatch
(`_no_tools_dispatch`) never reaches `run_graph` at all. Recorded so
whoever builds that caller knows this default is not safe to inherit
silently.

**Policy conformance, named explicitly.** `testing-conventions` applies and
is followed above (real stdin never touched in the unit suite;
`_default_approve`'s own test mocks `builtins.input`, the actual I/O
boundary, rather than faking an environment fact). It does not, however,
name terminal/stdin I/O as a banned resource the way it names network,
clock, and filesystem — treating "mock the boundary call" as the right
extension of its existing rule is a judgment call this spec is making
explicitly, not a rule the skill states outright. `project-structure` and
`reference-lookup` don't exist as separate skills in this repository (same
finding E1's own spec made); CLAUDE.md's "Our methodology for learning from
the reference" is this project's actual reference-lookup policy, and
guideline 1's research above follows it.

**No other policy conflict found.** The new interface is one callable with
one safe default; the decision to default it rather than require it is
cheap to reverse (a later caller can always pass its own); nowhere in this
design did two of the project's own stated rules pull in different
directions.
