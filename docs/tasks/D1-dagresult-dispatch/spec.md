# Spec: A plugin's result stops being a single line of text

Intent: docs/tasks/D1-dagresult-dispatch/intent.md

## Requirements

1. A new frozen dataclass carries what a plugin run produced: whether it
   reached a terminal step, what the model should read as the tool result,
   what it produced (if anything, as links or files), and a step-by-step
   record of what happened along the way. Traces to intent's Proposed
   outcome: "a structured answer instead of a bare string."
2. `dispatch`'s declared type changes, everywhere it is declared, from
   promising a plain string to promising this new structured answer.
   Traces to intent's Proposed outcome and Constraints ("every place... moves
   to the new answer shape").
3. Every one of the three already-built functions that either calls
   `dispatch` directly or threads it through to something that does is
   updated to the new type, with no change to what any of them decides or
   returns beyond that. Traces to intent's Affected users and systems ("the
   three places in the already-built turn-handling system").
4. Wherever the running system today takes whatever `dispatch` handed back
   and turns it into the text a model reads as a tool result, it instead
   reads that text off the new structured answer, in the same place, with
   no other change to how a tool result reaches the transcript. Traces to
   intent's Proposed outcome ("nothing in the repository is still treating a
   plugin's outcome as indistinguishable from a plugin's error" — the render
   point is exactly where that distinction would otherwise be lost).
5. The one hand-written proof that a plugin can run end to end against a
   live model, and the two example plugins it drives, are updated to answer
   with the new structured shape instead of a bare string. Traces to
   intent's Constraints ("including the one proof script and its two
   example plugins").
6. Nothing about what a plugin is permitted to do — what it can reach,
   whether it can pause and resume — changes. Traces to intent's Constraints
   (no new outside-reaching ability, no pause/resume).
7. No dual signature, flag, or transition period: the old plain-string
   promise is gone from every one of the above in the same change, not
   deprecated alongside the new one. Traces to intent's Constraints ("a
   clean break, not a transition").

## Design

**New module, not an addition to `conversation.py`.** This project already
gives each block its own module — `config.py`, `context.py`,
`model_access.py`, `conversation.py` — and two existing docstrings in
`conversation.py` already flag that the fields this item defines don't
belong there: `ToolSpec`'s says handler/effects fields "would have no reader
here," and `PluginCatalogEntry`'s says "a future PLUGINS block is the real
producer of this data." This item *is* that future block starting. The three
dataclasses land in a new `src/sadana/plugins.py`, with the same header style
`context.py` uses (a docstring naming this spec, and why nothing here can
create an import cycle — this module has no dependency on `conversation.py`
at all, so the question doesn't even arise in this direction).

```python
@dataclass(frozen=True)
class Artifact:
    kind: Literal["link", "file"]
    name: str
    ref: str  # a URL, or a path under the run's own output directory

@dataclass(frozen=True)
class NodeTrace:
    node: str
    kind: Literal["compute", "ask", "route", "stop", "call", "each", "wait"]
    visit: int  # 0-based; every trace entry today has exactly one visit
    ok: bool
    port: str | None
    detail: str | None

@dataclass(frozen=True)
class DagResult:
    plugin: str
    entry: str
    text: str
    artifacts: tuple[Artifact, ...]
    trace: tuple[NodeTrace, ...]
    failed_node: str | None
```

`Artifact.kind` and `NodeTrace.kind` are `Literal`, not a bare `str` with a
comment — reusing the pattern this codebase already has for a small closed
string set (`Message.role: Literal["user", "assistant", "tool"]` in
`conversation.py`), rather than writing a second way to express the same
idea. `NodeTrace.kind` lists all seven node kinds the design material names
(`§6`), not only the four with no outside effect — a trace entry has to be
able to name any node kind once a walker exists (items 4-6), and narrowing
the type now would just widen it again later for no reason.

**`dispatch`'s declared type**, in all three places it appears
(`run_turn`, `take_turn`, `run_child` in `conversation.py`), changes from
`Callable[[str, dict], Awaitable[str]]` to
`Callable[[str, dict], Awaitable[plugins.DagResult]]`. `take_turn` and
`run_child` only ever thread this value through to `run_turn` — neither
inspects a dispatch result itself — so this is a signature-only change for
both.

**The one place that reads a dispatch result** is `run_turn`'s own
tool-call loop:

```python
raw_result = await dispatch(tc["name"], tc["arguments"])
result_text = str(raw_result)
```

becomes

```python
result: plugins.DagResult = await dispatch(tc["name"], tc["arguments"])
result_text = result.text
```

The surrounding exception handler (`except Exception as e: result_text =
f"tool_error: {e}"`) is unchanged — it exists for the case a dispatch
implementation itself misbehaves (raises), which is a different scenario
from a plugin's own DAG reaching a non-terminal stop and reporting that
through `DagResult.failed_node`. `DagResult` does not replace that
try/except; it replaces what happens when `dispatch` returns normally.
Everything downstream of `result_text` — capping, `context.after_tool_result`,
appending to the transcript — is untouched: `DagResult.text` is a plain
string the instant it's read, so nothing past this line needs to know
`DagResult` exists.

**`scripts/prove_conversation_e2e.py`'s `dispatch` closure** changes its
return type and every `return` inside it. This closure's three branches
(`plugin_a_entry`, `plugin_b_entry`, the unreachable "unknown tool"
fallback) already have hand-written comments naming three DAG-shaped steps
for `plugin_a_entry` — "node 1: webhook-ish input node," "node 2:
subagent-with-skill node," a branch on the child's reply — which is exactly
what `§1` of the design material points to as "the first plugin already
ran." Rather than collapsing that structure into an always-empty `trace=()`,
each branch's `return` becomes a `DagResult` whose `trace` names those same
three already-existing steps as `NodeTrace` entries (`ok=True` for a
"fetch," "ask," "route" kind respectively, `port` set to whichever branch
was actually taken). `plugin_b_entry`'s single-step flow gets one
`NodeTrace`. The unreachable fallback branch returns a `DagResult` with
`failed_node` set (to `"entry"` — there is no real entry node to name,
because dispatch was called with a tool name no plugin owns) rather than
`trace=()`, since a truly unreached run is exactly what `failed_node` exists
to say. No branch produces an `Artifact` — nothing in this proof ever
produces a file or a link, and inventing one to exercise the field would be
fabricating evidence the script doesn't actually have.

**`docs/reference/dispatch_closure_state_bug.md`** already carries a note
(the "Does not address at all" section) that the interface gap this item
closes is left "for whichever future work item builds real plugin dispatch."
That file is a hand-authored reference document, not a closed work item's
own artifact — CLAUDE.md's rule against editing a closed work item's
artifacts protects `docs/tasks/CONV-10-*`, not this file. It already carries
one prior `## Resolution (CONV-10-dispatch-parent-propagation)` section for
exactly this purpose; this item adds a matching
`## Resolution (D1-dagresult-dispatch)` section recording that the gap it
named is now closed, in the same style, once the code lands.

## Interface

**In:** nothing new enters from outside this item — no new caller, no new
configuration, no new file on disk.

**Out (the new public shape, `src/sadana/plugins.py`):** `Artifact`,
`NodeTrace`, `DagResult` — three frozen dataclasses, importable as
`from sadana import plugins`.

**Changed (existing public shape, `src/sadana/conversation.py`):**
`run_turn`, `take_turn`, `run_child` each keep their existing parameter name
(`dispatch`) and position; only the type promised for what it returns
changes. No parameter is added or removed anywhere in this item.

**Errors:** none of this item's own code raises anything new. `DagResult`
carries a run's own failure (`failed_node`) as data, not an exception — a
dispatch implementation that still wants to signal "something in my own
plumbing broke, not the plugin's logic" still raises, exactly as today, and
`run_turn`'s existing `except Exception` still catches it. Nothing in this
item changes what counts as a raise versus a returned failure; it only gives
"the plugin's own logic reached an expected non-terminal stop" a way to be
data instead of forcing it to also be an exception.

**Invariant a future caller can rely on:** `DagResult.text` is always
present and always safe to render as-is, whether or not `failed_node` is
set. `run_turn` renders it unconditionally; a `DagResult` that leaves `text`
empty on a failure path would render an empty tool result, so every producer
of a `DagResult` — including this item's own proof-script fixtures — sets a
non-empty `text` on every path, failure included.

## Acceptance criteria

- [ ] `src/sadana/plugins.py` exists, exporting `Artifact`, `NodeTrace`,
      `DagResult`, all `@dataclass(frozen=True)`.
- [ ] `run_turn`, `take_turn`, `run_child` in `conversation.py` all declare
      `dispatch: Callable[[str, dict], Awaitable[plugins.DagResult]]`; no
      remaining declaration anywhere in `src/` promises
      `Awaitable[str]` for a dispatch-shaped callable.
- [ ] `run_turn`'s tool-call loop reads `result.text` from the awaited
      `dispatch(...)` call and no longer calls `str()` on it.
- [ ] `scripts/prove_conversation_e2e.py`'s `dispatch` closure returns
      `plugins.DagResult` on every path (`plugin_a_entry`, `plugin_b_entry`,
      the unknown-tool fallback), each with a non-empty `text`.
- [ ] `mypy src` passes with the new signatures — no `Any`/`# type: ignore`
      introduced to paper over the type change.
- [ ] `docs/reference/dispatch_closure_state_bug.md` carries a new
      `## Resolution (D1-dagresult-dispatch)` section.
- [ ] No file under `docs/tasks/CONV-10-dispatch-parent-propagation/` is
      edited.
- [ ] `make verify` ends `VERIFY OK`.

## Non-goals

- Parsing a plugin's own manifest file, or validating one — separate work
  item (`D2-manifest-validation`), reading the same design material.
- A graph walker, or any node kind actually running — later items, after
  the execution half's own prerequisites land.
- Anything reading `DagResult.trace` or `.artifacts` for grading or logging
  — no such reader exists yet; this item only ensures the data is real and
  truthful where it is already produced by hand.
- Giving a plugin any new ability to reach outside the process, or to pause
  and resume — unchanged by this item.

## Rejected alternatives

**(Guideline 1 — reference corpus.)** hermes-agent's own tool-dispatch path
(`tools/registry.py`'s `dispatch()`, called from `hermes_cli/plugins.py`'s
`dispatch_tool()`) also returns a plain string — a JSON-encoded one
(`json.dumps(result, ...)`), not free text, but a string nonetheless, parsed
back out by whatever reads it rather than checked by a type checker.
Declining this: JSON-in-a-string keeps exactly the two problems `§7.1` of
the design material names — success and failure remain the same static
type, and nothing stops a producer from omitting a field the reader assumed
was there. This project already has a working alternative to "encode
structure in a string and hope the reader agrees" — `model_access.py`'s
`classify()` returning one of six named `Outcome` values — and a frozen
dataclass is the same idea applied here, not a new one invented for this
item.

**(Guideline 2 — reduce the number of bets, pre-seam case.)** No manifest,
no graph, no installed plugin exists yet — this item sits before the seam
guideline 2 describes, so its narrow form applies: don't foreclose, don't
build ahead of a consumer. `DagResult` doesn't get a `version` field, a
`metadata: dict[str, Any]` escape hatch, or any place for an unspecified
future need to live, on that basis — every field traces to something the
design material already names as needed (`§7.1`), and an escape hatch here
would be exactly the "speculative infrastructure with no consumer" this
guideline warns against, just relocated into one field instead of one
class.

**(Guideline 3 — least step-cost.)** The central choice — a new return type
replacing a string — makes an existing step (the one call to `dispatch`,
and the one place its result is read) heavier, rather than adding a new
step or making an existing one harder to call. A cheaper-looking
alternative was considered and declined: leave the string return and adopt
a convention (a JSON-string, a sentinel prefix like `"ERROR: "`) for callers
to parse. That would avoid a new module and a signature change, but it is
the "make a step harder" move, not a cheaper one — it pushes a parsing
obligation onto every future reader of a dispatch result, forever, to save
one signature change now, and it is the exact pattern rejected above under
guideline 1.

**(Guideline 4 — minimise mutable state.)** This item introduces no mutable
state. `Artifact`, `NodeTrace`, `DagResult` are all `frozen=True`,
constructed fresh by whatever calls `dispatch` and read once by `run_turn`;
none is retained past the tool-result message it renders into. There is
nothing here to inventory beyond that — no lifetime, no migration, nothing
that can go stale, because nothing is stored.

## Open questions

None carried forward from `intent.md`'s own open question (what a person
should be told when a `failed_node` is set) — this item's acceptance
criteria already require every producer to supply a non-empty, human-usable
`text` regardless of `failed_node`, which is the concrete form that question
takes here. What a *future, real* graph walker should compose into `text`
on a failure it detects itself is not decided here, since no such walker
exists yet in this item's scope.

## Concerns

**Policy skills consulted.** `testing-conventions` was loaded and applied —
see the network/round-trip point below. `project-structure` and
`reference-lookup` are named by `design-skill` but do not exist as skills in
`.claude/skills/` in this repository (only `build-skill`, `deploy-skill`,
`design-skill`, `plan-skill`, `testing-conventions` do); this design instead
follows `docs/reference/plugin_blueprint.md` directly as the design
authority for this block, and consults the reference corpus (guideline 1,
above) by reading hermes-agent's own dispatch path directly rather than
through a lookup skill that isn't present.

`scripts/prove_conversation_e2e.py` is a hand-run proof (network, real
model), not exercised by `make test` — testing-conventions bars the network
from the unit suite, and this script is already the project's stated
exception, proven separately and pasted as Deploy-stage evidence rather than
covered by the automated suite. That means this item's riskiest piece — did
the fixtures' new `DagResult` construction actually work against a live
model, not just type-check — is proven the same way C10/C11's own network
round trips were: a standalone run, its output pasted into `review.md`'s
`## Evidence`, not a unit test. I'm confident this is the right call, not a
gap, because `testing-conventions`' own "never fake the environment" rule
and CLAUDE.md's rule to prove a first real external round trip with a
standalone script both already settle it the same way for this exact
script.

The one real, unresolved tension: `NodeTrace.kind` is typed as
`Literal["compute", "ask", "route", "stop", "call", "each", "wait"]` even
though this item never actually produces a `"wait"` node (§7.2 forecloses it
entirely for now) and never produces a `"call"` node from real code (only
`"compute"`-ish and `"ask"`-ish hand-written proof-script steps exist).
Listing all seven is a bet that the closed set named in the design material
won't need a member added before a walker exists to enforce it for real; the
alternative (typing only the kinds this item actually produces, `Literal["fetch", "ask", "route"]` matching the proof script's own three hand-named
steps) is narrower and cheaper to write today, but would make every later
item that adds a kind edit this same `Literal` a second time. I kept the
full seven because the design material (`§6`) already treats that list as
closed and decided, not open — narrowing it here would be re-litigating a
decision this item didn't make.
