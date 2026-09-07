# Spec: handing back a link as itself, not as prose

Intent: docs/tasks/G2-artifacts/intent.md

## Requirements

1. When a `call` node's approved body returns a `plugins.Artifact`, that
   artifact actually ends up in the run's `DagResult.artifacts` — replacing
   the `artifacts=()` every result carries today regardless of what any
   node did. Traces to Proposed outcome ("a run can hand that link back as
   its own labeled thing").
2. Only `call` nodes can emit an Artifact this way. `compute`, `route`,
   `ask`, and `stop` are unaffected. Traces to Problem's own framing (a
   link comes from a step that already reached outside) — nothing else in
   the vocabulary reaches outside at all.
3. When a `call` node's body returns an `Artifact`, the value the next node
   receives is the artifact's own `ref` — not the raw `Artifact` value
   itself. Traces to Proposed outcome ("not something that has to be
   parsed back out of prose to be used again") and `plugin_blueprint.md
   §10` Risk 4 (`DagResult.text` must not become a dumping ground for
   un-structured values).
4. When a `call` node's body returns a plain, non-`Artifact` value,
   behavior is byte-identical to before this item (G1's own behavior) — no
   artifact recorded, the value threads exactly as it already does. Traces
   to intent's Constraints (this item only adds a case, it does not change
   the existing one).
5. Nothing here creates, resolves, or writes to any notion of "the run's
   own output directory," and nothing here writes a file. Traces to
   Constraints ("Declines writing a file anywhere").

## Design

**Where this attaches.** `src/sadana/plugin_manifest.py:run_graph`'s `call`
branch (G1) already resolves and runs the node's body off the event loop:

```python
elif node.kind == "call":
    approved = await approve(manifest.name, node.name, value)
    if not approved:
        return failed(node, "declined")

    def run_body(n: plugins.Node = node, v: object = value) -> object:
        return _resolve_body(plugin_dir, n, modules)(v)

    value = await asyncio.to_thread(run_body)
```

This item adds one check immediately after the threaded call returns:

```python
    value = await asyncio.to_thread(run_body)
    if isinstance(value, plugins.Artifact):
        artifacts.append(value)
        detail = f"emitted {value.kind} artifact {value.name!r}"
        value = value.ref
```

`run_graph` gains a new local accumulator, `artifacts: list[plugins.Artifact]
= []`, declared alongside the existing `trace: list[plugins.NodeTrace] = []`
(both are per-call, function-local, and die with the walk — no new
lifetime, no new class). `detail`, currently implicit (`None`) for every
successful node, becomes an explicit local (default `None`, same as `port`
already is) so the `call` branch can set it without touching any other
branch's shape; the existing unconditional
`trace.append(plugins.NodeTrace(..., detail=None))` after the `try` block
becomes `detail=detail`. The `result()` closure's hardcoded `artifacts=()`
becomes `artifacts=tuple(artifacts)`.

**The return value already carries the artifact — nothing new is asked of
a plugin author beyond "return one specific type when you have one."** A
`call` node's body contract stays exactly what `compute`'s already is:
`Callable[[object], object]`, one value in, one value used next. No second
parameter, no callback, no new import required in `plugins.py` or
`plugin_manifest.py` (`Artifact` already lives in `plugins.py`, from D1).
Whether a given call to `sadana.execution.run_http` is worth wrapping in an
`Artifact` before returning it is the plugin author's own decision, made in
their own `init.py`, the same way every other body decision already is.

**No change to `plugin_dispatch.py`, `conversation.py`, or
`eval_harness.py`.** `DagResult.artifacts` has existed as a typed field
since D1; nothing downstream reads it yet, the same way nothing forced
`.trace` to be surfaced the moment it was added. This item makes the field
truthful, not consumed — consumption is a later item's concern (closer to
item 9's eval, which grades `.trace`, or a future turn-loop change that
chooses to render `.artifacts` to a person).

## Interface

**In:** a `call` node's body, already resolved and run exactly as G1
built it; its return value.
**Out:** if the return value is a `plugins.Artifact`, it is appended to
`DagResult.artifacts` and the value threaded to the next node becomes
`artifact.ref`. If the return value is anything else, behavior is
unchanged from G1: it becomes the next node's value directly, and
`DagResult.artifacts` for that step contributes nothing.
**Errors:** none new. A body that raises while constructing or returning
an `Artifact` is caught by the same `except Exception` this branch already
has (G1); no special case is added for this item's own check.

## Acceptance criteria

- [ ] A `call` node whose body returns `plugins.Artifact(kind="link",
      name=..., ref=...)` produces a `DagResult` whose `artifacts` tuple
      contains exactly that artifact — covered by a unit test chaining
      into a `stop` node and asserting both `result.artifacts` and
      `result.text` (the latter equal to the artifact's own `ref`, proving
      requirement 3).
- [ ] A `call` node whose body returns a plain value (a `dict`, as G1's own
      existing test already covers) still produces `artifacts == ()` and
      threads the plain value unchanged — covered by re-running G1's own
      `test_run_graph_call_node_runs_its_body_and_threads_the_result`
      unmodified (it must still pass byte-for-byte), proving requirement 4.
- [ ] `compute`, `route`, `ask`, and `stop` node walks never populate
      `artifacts`, even if their own body happened to return an `Artifact`
      value — covered by a unit test using a `compute` node whose body
      returns an `Artifact`, asserting `result.artifacts == ()` (the value
      still threads as an opaque object, exactly as any other `compute`
      return value would — `compute` never inspects what it returns).
- [ ] The `NodeTrace` for an artifact-emitting `call` node has a non-`None`
      `detail` naming the artifact's kind and name; the `NodeTrace` for a
      plain-value `call` node keeps `detail=None`, unchanged from G1.
- [ ] `src/sadana/plugin_dispatch.py`, `tests/unit/test_plugin_dispatch.py`
      pass unchanged — no `Artifact`-returning fixture exists there, the
      same regression check F1/G1 already established as a pattern.
- [ ] `make verify` ends `VERIFY OK`.

## Non-goals

- Writing a file, or creating/resolving "the run's own output directory"
  — intent's own Constraints; a separate, later work item.
- Letting `compute`, `route`, `ask`, or `stop` emit an Artifact — see
  Rejected alternatives; `plugin_blueprint.md §6`'s literal "any node may
  emit" is not fully built here.
- Validating that an `Artifact`'s `ref` is a real, reachable link — see
  Concerns.
- Surfacing `DagResult.artifacts` anywhere outside the `DagResult` itself
  (a rendered message, a UI, a log) — a separate, later concern, the same
  way `.trace` is not surfaced anywhere today either.

## Rejected alternatives

**A second channel into a `call` node's body — an `emit: Callable[[Artifact],
None]` parameter alongside `value`, or a similar callback** — declined.
This would change the body's call contract for every future `call`-node
author beyond what `compute`'s own contract already asks (one value in,
one value out), for a need this project's own reference research (below)
suggests isn't necessary: hermes-agent's own analogous surface
(`../hermes-agent/agent/image_gen_provider.py`, read in full) hands back an
image's URL or path as one field in the same flat return dict every other
result field already rides in (`success_response()`, lines 347-379) —
there is no separate "emit" channel there either, because the value a
caller already returns is where a reference belongs. The `isinstance`
check on the existing return value gets the identical outcome with zero
signature change.

**Any node kind may emit an Artifact, matching `plugin_blueprint.md §6`'s
literal wording** ("Any node may emit an `Artifact`... A dedicated output
kind would suggest artifacts only appear at the end, which is false for
anything that writes a file mid-run") — declined for now, not forever.
`compute` and `route` are defined as pure (the same line that keeps them
separate from `call` at all); `ask` is a subagent judgement call with
nothing artifact-shaped in its own contract today. Extending the
`isinstance` check to every branch is a seam built for node kinds that
have no real reason to produce one yet — this project's own rule declines
building a registry, or in this case a cross-cutting check, for a
population of one real case (`call`). Revisit the moment a second kind
genuinely needs to emit an artifact mid-run, which is exactly what the
blueprint's own "not only at the end" reasoning is guarding room for.

**Threading the raw `Artifact` object forward as `value`, instead of
extracting `.ref`** — declined. If the artifact-emitting node were
terminal, `_coerce_text`'s existing fallback (`str(value)`) would render a
raw dataclass repr into `DagResult.text` — reopening exactly the
"`DagResult.text` becomes a dumping ground" risk (`plugin_blueprint.md
§10` Risk 4) this item exists partly to avoid. Extracting `.ref` keeps
downstream text a clean string, matching what a `compute` node's own
plain-value contract already promises every caller.

**Rejecting or validating a `kind="file"` Artifact a body might
construct** — declined. Nothing in this project writes files today or
resolves "the run's own output directory" (intent's own Constraint), so a
body that constructs a `file`-kind `Artifact` pointing at a path it
manages entirely itself is not actually prevented by anything this item's
Constraints require — it simply isn't a case this project has built
support for yet. Adding a rejection check would be dead validation for a
scenario that can't meaningfully arise under current first-party trust;
the later file-writing item is free to add real handling when the
underlying concept exists.

## Concerns

**The same trust boundary G1 already accepted, now load-bearing for
content as well as behavior.** Nothing verifies a `call` body's returned
`Artifact.ref` is an actual reachable link, or that its `kind` honestly
describes what it is — the type only shapes what crosses the boundary, not
what it contains. Accepted for the identical reason G1 accepted the
parallel gap for `call` bodies generally: every plugin today is
first-party, and real enforcement is a sandboxing question this project
has already deferred (E1's own intent).

**A plain `dict`/`list` return is not an `Artifact`, and this item does
not go looking for one.** `_coerce_text`'s existing dict/list-to-JSON
coercion (unchanged by this item) means a `call` body can return something
that merely *looks* link-shaped (e.g. `{"url": "https://..."}`) without it
ever becoming a `DagResult.artifacts` entry — only a real
`isinstance(value, plugins.Artifact)` triggers this item's behavior, never
a heuristic over dict keys. Worth naming so a reviewer doesn't expect the
looser case to also work.

**Policy conformance.** `testing-conventions` applies: new tests use fake
`init.py` bodies that construct and return a real `plugins.Artifact`
directly (imported the same way every other test in this file imports
from `sadana.plugins`) — no network touched, matching the existing
`call`/`compute` tests exactly. `project-structure` and `reference-lookup`
do not exist as separate skills in this repository (same finding
E1/F1/G1's own specs made); CLAUDE.md's "Our methodology for learning from
the reference" is this project's actual reference-lookup policy, and
guideline 1's research above (hermes's `image_gen_provider.py`) follows it.

**No other policy conflict found.** The change is additive to a single
existing branch, the state it introduces (`artifacts: list[...]`) has the
identical shape and lifetime as the `trace` list it sits beside, and the
decision to scope emission to `call` only is cheap to reverse (extending
the same `isinstance` check to another branch later touches only that
branch, not this one). Nowhere in this design did two of the project's own
rules pull in different directions.
