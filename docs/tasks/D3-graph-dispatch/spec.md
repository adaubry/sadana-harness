# Spec: A plugin's graph actually runs

Intent: docs/tasks/D3-graph-dispatch/intent.md

## Requirements

1. A pure function derives, from a set of already-validated installed
   plugins, everything a conversation template needs to advertise those
   plugins' actions and later resolve a call back to the right one: one
   catalog line and one tool definition per declared action, plus a lookup
   from an action's name back to which plugin and which of its declared
   entries it is. Traces to intent's Proposed outcome, first paragraph, and
   to `plugin_blueprint.md §8`'s "each `[[entry]]` contributes one
   `PluginCatalogEntry`" / "one `ToolSpec`".
2. A function reads what is actually installed, keeping only the plugins
   whose own file has already passed its honesty check — nothing here
   re-runs or second-guesses that check. Traces to intent's Constraints
   ("A plugin's file has already been checked for honesty... trusts a
   result that already passed them").
3. A function walks a validated plugin's declared steps, starting from a
   named entry point, executing each one in turn: a step that transforms
   data runs its own code and passes a new value forward; a step that
   decides between named paths runs its own code and follows exactly the
   one path it names; a step that consults a model for judgement actually
   spawns a bounded sub-task and uses what it reports back; a step that
   ends the run does. Traces to intent's Proposed outcome, first paragraph.
4. Every step taken is recorded, in order, as it actually happened: which
   step, whether it went fine, which path was taken where there was a
   choice. Traces to intent's Proposed outcome ("hands back a real,
   structured answer... whether the run finished normally, and, if it
   didn't, exactly where it stopped").
5. When a step fails — its own code raises, a deciding step names a path
   that was never declared, or a judgement step's sub-task doesn't finish
   cleanly — the walk stops there. The answer names exactly which step,
   in a single safe sentence with nothing like a stack trace in it; it is
   never an unhandled error reaching whoever asked for the run. Traces to
   intent's Constraints ("always hands back a real, structured answer...
   never lets an unexplained error crash outward") and closes
   `plugin_blueprint.md §12` Open question 4.
6. A step that transforms data or decides between paths is given only what
   the step immediately before it produced in that same run — never
   anything from earlier. Traces to intent's Constraints (decided directly
   with the user during planning).
7. A request naming an action that no currently-installed, validated
   plugin backs gets a specific, structured answer saying so, the same
   shape as any other outcome — not a crash, not silence. Traces to
   intent's Proposed outcome, second paragraph.
8. None of the steps this item executes reaches outside sadana's own
   process. A step kind that would (or one this vocabulary doesn't cover
   yet) is recognized and ends the run cleanly, naming that step, rather
   than being run or silently skipped. Traces to intent's Constraints ("No
   declared step is allowed to reach outside sadana's own process").
9. One real, callable function exists with exactly the shape the turn loop
   already expects for resolving a request into a result, usable end to
   end — proven by a standalone script exercising an installed plugin
   whose steps include a judgement step, run against a real model in
   addition to the unit suite's offline coverage of every other step kind.
   Traces to intent's Proposed outcome, third paragraph, and to CLAUDE.md's
   rule to prove a block's first real external round trip outside
   `make test`.

## Design

**Policies applied.** `testing-conventions` (below, and in Acceptance
criteria): the network/model boundary this item introduces is proven by a
standalone script, never inside `make test`; every other behavior is unit
tests over `tmp_path` plugin directories and fakes, never real files or
real dispatch closures reused across tests. No `project-structure` or
`reference-lookup` skill exists in this repository (same gap D1 and D2's
own specs already recorded); this design follows `plugin_blueprint.md`
directly and consults the reference corpus by reading hermes's own files
(below).

### Guideline 1 — what the reference corpus already tried

Read for this item: `hermes_cli/agent_plugins.py`, `hermes_cli/plugins.py`
(`PluginManager`), `agent/pet/manifest.py`, `agent/moa_loop.py`,
`tools/delegate_tool.py`, `tools/managed_tool_gateway.py`.

- **`hermes_cli/plugins.py`'s `PluginManager`** resolves a tool name
  through a shared, mutable, process-global registry
  (`tools.registry.registry`) that a plugin's own Python `register(ctx)`
  function mutates in place, inside a transaction with thread locks and a
  topological load-order resolver for inter-plugin dependencies
  (`resolve_plugin_load_order`). It does raise on a name collision at
  registration time (`ValueError`, "...already registered by plugin
  '...'"). **Adopted**: the raise-at-build-time posture for a name
  collision — sadana already has this exact precedent
  (`conversation.build_surface` → `DuplicateToolError`), so this item
  reuses it rather than inventing a second check (below). **Declined**:
  the registry itself, the locks, and the load-order resolver. That
  machinery exists to solve cross-plugin dependency ordering, which only
  matters once plugins can depend on or call each other — intent.md's
  Constraints explicitly decline that for this item. Building any of it
  now would be exactly the hidden-coupling risk CLAUDE.md's methodology
  warns about, for a problem this item doesn't have.
- **`agent/pet/manifest.py`** is unrelated — a cosmetic feature's remote
  catalog fetcher with a TTL cache and a background thread. Confirms,
  rather than contradicts, `plugin_blueprint.md §5.2`: hermes has nothing
  resembling "the graph is data, the node bodies are code."
- **`agent/moa_loop.py`** has no declarative branching. What runs next is
  a fixed sequence of Python calls, and success/failure is decided by
  string-matching a response. Nothing to adopt for `route` — sadana's
  named-port branching (already shipped in `Node`) has no hermes
  precedent to compare against; it is confirmed original here, not
  independently reinvented.
- **`tools/delegate_tool.py`** reports a spawned sub-task's failure as a
  bare `status` string plus a human-rendered one-line message, with the
  real detail discarded to one truncated line; ordinary internal errors
  are widely just swallowed (bare `except Exception`, 19 sites in one
  file, mostly logged or dropped). **Declined outright**, and named as the
  concrete case against that shape: it is what a caller-facing failure
  looks like when nothing enforces a structured outcome, and it is
  precisely the CLAUDE.md rule ("never a raised exception... or any other
  side channel") this item's own Requirement 5 exists to avoid repeating.
- **`tools/managed_tool_gateway.py`** turned out unrelated to tool
  dispatch (vendor-auth/media-upload routing) — a false lead by name only,
  recorded so a future reader doesn't re-check it.
- No hermes file anywhere searched keeps a structured, per-step record of
  a multi-step run (`NodeTrace`/`DagResult.trace`'s job). It is treated as
  sadana-original, motivated by the gaps above, not modeled on any hermes
  file.

### Where this lands

`plugins.py` (pure, no file/network/clock access, no dependency on
`conversation.py` — D1's own invariant) gains:

```python
@dataclass(frozen=True)
class InstalledPlugin:
    """One plugins-root subdirectory that validated cleanly, kept around
    so a dispatch call doesn't re-read plugin.toml on every tool call —
    a plugin is immutable and external (§3.1); nothing this item does can
    change one mid-conversation, so re-validating per call buys no extra
    safety, only waste."""
    name: str
    directory: Path
    manifest: Manifest

AskFn = Callable[[SkillRef, str], Awaitable[str | None]]
# The one seam plugins.py/plugin_manifest.py expose for "consult a model."
# None means the sub-task didn't finish cleanly; a str is what it reported.
# Neither plugins.py nor plugin_manifest.py may import conversation.py
# (D1's invariant, unbroken) — so neither can call run_child directly.
# Whatever supplies this function is conversation.py's problem, not theirs.
```

**Corrected during implementation** (see Concerns): `PluginSet` and
`build_plugin_set` cannot live in `plugins.py` as first drafted above —
they construct `PluginCatalogEntry`/`ToolSpec`, both owned by
`conversation.py`, and `plugins.py` may never import it. Both moved to
`plugin_dispatch.py`, next to `build_dispatch`, where that import is
already allowed:

```python
@dataclass(frozen=True)
class PluginSet:
    """Everything a template's recipe and a real dispatch call both need,
    derived once from a scan of installed, validated plugins. Rebuilt from
    scratch, never patched, the same posture create_conversation() already
    takes toward a ConversationTemplate's recipe."""
    catalog: tuple[PluginCatalogEntry, ...]
    tool_specs: tuple[ToolSpec, ...]
    by_tool: Mapping[str, tuple[plugins.InstalledPlugin, plugins.Entry]]

def build_plugin_set(installed: Iterable[plugins.InstalledPlugin]) -> PluginSet: ...
```

`build_plugin_set` produces exactly one `PluginCatalogEntry` and one
`ToolSpec` per `Entry` across every given plugin (`plugin_blueprint.md
§8`), using `entry.tool` as both a `ToolSpec`'s `key` and `name` (matching
`scripts/prove_conversation_e2e.py`'s own existing convention) and
`entry.purpose` rendered verbatim as `describe` — a plugin's own sentence
has no other tool's name inside it to cross-reference, so there is nothing
for `describe` to compose beyond returning that sentence. It also reads
each entry's schema file off disk (`entry.parameters`, relative to the
plugin's own root) to populate `ToolSpec.parameters` with the parsed JSON
Schema `validate()` already proved is well-formed — not drafted above,
another implementation-time correction (Concerns): `Entry.parameters` is
only ever a path, and a `ToolSpec` needs the parsed schema itself.
**Duplicate `entry.tool` names across two plugins are not checked here.**
`by_tool`'s dict construction would let a second entry silently overwrite
the first, but `tool_specs` is still built unconditionally from every
entry — so the same collision always also produces two `ToolSpec`s
sharing one `name`, and `conversation.build_surface()` already raises
`DuplicateToolError` on exactly that, the moment a template's recipe is
turned into a real conversation, before any dispatch call could ever
observe `by_tool`'s version of events. Requirement 1's "fail loudly" is
satisfied by an existing, already-tested step doing new work, not a
second check — the cheaper of the two available moves (Guideline 3,
below).

`plugin_manifest.py` (I/O — this module's whole job is reading files and
running what a plugin names) gains:

```python
def discover_plugins(plugins_root: Path | None = None) -> tuple[plugins.InstalledPlugin, ...]:
    """Every immediate subdirectory of plugins_root (default:
    plugins._plugins_root()) whose plugin.toml comes back Valid from
    validate(). One that doesn't is silently excluded — the same "not
    trustworthy, don't build on it" outcome §8's checks already produce,
    applied by simply not including it. See Concerns: this drops any
    signal about *why* a plugin didn't show up, deliberately, because
    surfacing that usefully is outside what Requirement 2 asks for."""

async def run_graph(
    plugin_dir: Path, manifest: plugins.Manifest, entry: plugins.Entry,
    arguments: dict, *, ask: plugins.AskFn,
) -> plugins.DagResult:
    """Walks manifest's declared steps from entry.start, exactly as
    validate() already proved they connect (D2's reachability check —
    this function trusts it, same posture toward an already-validated
    Manifest that run_child already has toward an already-validated
    system_prompt). Reuses _load_body_module's existing module-loading and
    caching (one import per init.py per call, not per node) to actually
    call a compute/route node's named function, rather than only checking
    it resolves. Every node kind's own failure — a raised exception, a
    route body naming an undeclared port, a call/each/wait node this
    vocabulary doesn't execute yet, an ask whose sub-task didn't finish
    cleanly — ends the walk at that node: DagResult.failed_node names it,
    DagResult.text is one fixed, generic sentence naming the plugin and
    the step (never the raw exception or its traceback), and nothing
    raises out of this function for any of them."""
```

`run_graph`'s per-node behavior:

- **`compute`**: calls its resolved body with the predecessor's value
  (the entry's raw `arguments: dict` for the first node), the return value
  becomes the next predecessor value.
- **`ask`**: builds `SkillRef(plugin=manifest.name, skill=node.skill)`,
  coerces the predecessor's value to text (already a `str` unchanged;
  `json.dumps` for a `dict`/`list`; `str()` otherwise — one small helper,
  reused for the run's final `text` too, so there is exactly one coercion
  rule, not two), and calls `ask(skill_ref, text)`. `None` back means the
  node failed; a `str` becomes the next predecessor value.
- **`route`**: calls its resolved body with the predecessor's value,
  gets back a port name. `Node.ports` already holds real node names
  (D2's own `DanglingTarget` check proves every one resolves) — a
  returned value that isn't one of `node.ports` is this node's own
  failure, not a structural one D2 could have caught ahead of time,
  because it depends on what the body actually does at run time, not on
  what the manifest declares. The predecessor's value passes through
  unchanged to whichever node the port names; `route` decides, it does
  not transform.
- **`stop`**, or any node with neither `next` nor `ports` declared
  (`plugin_blueprint.md §5.2`: "a node with neither is terminal"): the
  walk ends there, `failed_node=None`, `text` is the coerced predecessor
  value.
- **`call`, `each`, `wait`**: recognized, not executed. Ends the walk at
  that node with `failed_node` set. Nothing in `validate()` forbids
  installing a plugin that declares one of these — this function is the
  first real consumer that would ever try to run it, so it must fail
  closed rather than silently no-op or crash.

A new module, `plugin_dispatch.py`, is the one piece allowed to import
both `conversation.py` and `plugins.py`/`plugin_manifest.py` — neither of
those may import `conversation.py` (D1's stated invariant), and this
item's `ask` seam needs `conversation.run_child`, so something has to sit
on the other side of that boundary. It gains:

```python
@dataclass
class ChildSeqTracker:
    """The one piece of mutable state this item introduces. See Guideline
    4 and Concerns for why it can't be derived instead."""
    next_seq: int

def build_dispatch(
    conversation: Conversation, plugin_set: PluginSet, *,
    stable_prompt: str, provider: str, model: str, now: float,
    persist: Callable[[tuple[Message, ...]], Awaitable[None]] = _noop_persist,
) -> tuple[Callable[[str, dict], Awaitable[plugins.DagResult]], ChildSeqTracker]:
    """Builds one dispatch closure and the tracker it shares with its own
    `ask` callback. The closure: looks `name` up in `plugin_set.by_tool`
    (Requirement 7's "not found" answer if it isn't there); on a hit,
    builds the `ask` callback (closes over `conversation`, the tracker,
    and everything run_child needs) and calls plugin_manifest.run_graph.
    The `ask` callback: builds a ChildSpec (tools=frozenset() always — a
    judgement step is pure judgement, §3.3; nothing in Node gives a
    plugin author a way to ask for more), overlays the tracker's current
    value onto a replace()d snapshot of `conversation` as run_child's
    `parent`, calls run_child, updates the tracker from the returned
    updated parent, and returns final_text on ExitReason.COMPLETED with
    a final_text, None otherwise."""
```

**The caller's obligation**, stated plainly because nothing enforces it
structurally: after a `take_turn()` call made with this dispatch,
reconcile before doing anything else —
`conversation = replace(updated_conversation, next_child_seq=tracker.next_seq)`
— and build a fresh dispatch (and tracker) from that reconciled value
before the next turn. This is exactly `eval_harness.run_task`'s own
existing `dispatch_factory(conversation)` shape, already built anticipating
"a task whose dispatch needs to call `run_child(parent=...)`" (its own
docstring) — `build_dispatch`, partially applied down to
`Callable[[Conversation], DispatchFn]`, drops in without changing
`eval_harness.py`. No caller does this today (no CLI, no multi-turn
driver exists yet); the standalone script for Requirement 9 is the first
one.

**Added during the build-stage self-check** (Concerns): stating the
obligation in a docstring left it enforced by nothing but a reviewer's
attention — `/simplify`'s altitude pass named this directly. `build_dispatch`
is unchanged; `plugin_dispatch.py` also gains a thin wrapper that makes
correct use structural instead of remembered:

```python
async def take_turn_and_reconcile(
    conversation: Conversation, dispatch: DispatchFn, tracker: ChildSeqTracker, *,
    user_input: str, provider: str, model: str, now: float,
    persist: Callable[[tuple[Message, ...]], Awaitable[None]] = _noop_persist,
) -> tuple[TurnResult, Conversation]:
```

It calls `conversation.take_turn` and reconciles `tracker.next_seq` onto
the result before returning — a caller going through it cannot skip the
reconciliation, because there is no second step left to forget. Direct use
of `dispatch`/`tracker` (this project's own unit tests; a caller needing
`take_turn`'s other parameters) is still fully supported — this is the
recommended path, not the only one. The standalone script for
Requirement 9 uses it.

### Guideline 2 — reducing the number of bets

This item is on the seam side of the plugin seam, not downstream of it —
it is what makes "growth means more plugins, not more core behavior" true
in the first place, so the applicable posture is the caveat: **do not
foreclose**, not "minimise future core changes" (there is no plugin-adding
workflow yet to keep untouched). Concretely:

- Adding a plugin never touches this item's own code — `discover_plugins`
  reads whatever is on disk; a second, third, tenth plugin is purely more
  data for it to fold in. This is the one part of the guideline that
  already applies in its stronger form.
- `call`/`each`/`wait` are recognized-but-refused, not designed around.
  Adding real `call` execution later is one more `elif` branch in
  `run_graph`'s per-kind handling, not a restructuring of the walk itself
  — the loop's shape (current node → execute → decide next) doesn't
  change when a fourth executable kind arrives.
- The alternative to Requirement 6 — give every node the whole run's
  accumulated state instead of only its predecessor's output — was live
  and was rejected directly with the user, not only on stylistic grounds:
  it is the bigger bet. Once one node's body reads something from three
  steps back, every future node kind (a visual editor's own drawing of
  the graph, `each`'s per-item state) inherits that coupling permanently;
  predecessor-only keeps every future addition local to the one edge it
  changes.

### Guideline 3 — least step-cost

The central open problem this item had to solve, not just implement, was
`docs/reference/dispatch_closure_state_bug.md`'s own unresolved point:
`dispatch()`'s contract gives a handler no channel to report `run_child`'s
own bookkeeping (`next_child_seq` advancing) back to whoever built the
closure — explicitly left for "whichever future work item builds real
plugin dispatch." Two moves were on the table:

- **Make an existing step heavier**: extend `DagResult` with a spawn-count
  field, and change `run_turn`/`take_turn` (already-closed, foundational
  C7/C8 functions) to thread it through into the `Conversation` they
  return. Rejected: it makes two closed functions permanently carry a
  plugin-specific concept (how many children a dispatch call spawned) they
  have no other reason to know about, for the life of the project, to
  save one small, tightly-scoped mutable value.
- **Add a step**: the `ChildSeqTracker` above — new, small, and owned
  entirely by the new code this item adds. It never touches `run_turn`,
  `take_turn`, or `run_child`'s existing signatures, and its lifetime is
  exactly one dispatch closure. Chosen: it is the cheaper move, and it
  turns a named, open gap into a solved, documented, reusable contract
  instead of a trick every future caller would otherwise have to
  reinvent (as the proof script's own now-fixed bug already showed once).

Requirement 1's duplicate-tool handling (above) is the other clean example:
the cheapest move available was adding no step at all, because an existing
one (`build_surface`) already catches the scenario given the data this
item was already going to produce.

### Guideline 4 — state inventory

- `InstalledPlugin` (`plugins.py`), `PluginSet` (`plugin_dispatch.py`) —
  derived, frozen, rebuilt from a fresh scan whenever needed; nothing
  about them can go stale because nothing holds one longer than a single
  scan's result.
- `ChildSeqTracker.next_seq` — the one genuinely mutable value. Justified
  above (Guideline 3): it exists because `run_turn`'s dispatch contract
  provides no other channel for this specific fact, not because deriving
  it was hard. Scoped to one dispatch closure's lifetime; never persisted,
  never shared across closures, never read by anything this item builds
  except the closure that owns it and the caller reconciling it back onto
  `Conversation` afterward.

## Interface

**In**: `build_plugin_set(installed: Iterable[plugins.InstalledPlugin]) -> PluginSet` (`plugin_dispatch.py`);
`discover_plugins(plugins_root: Path | None = None) -> tuple[plugins.InstalledPlugin, ...]` (`plugin_manifest.py`);
`run_graph(plugin_dir: Path, manifest: plugins.Manifest, entry: plugins.Entry, arguments: dict, *, ask: plugins.AskFn) -> plugins.DagResult` (`plugin_manifest.py`);
`build_dispatch(conversation: Conversation, plugin_set: PluginSet, *, stable_prompt: str, provider: str, model: str, now: float, persist=...) -> tuple[DispatchFn, ChildSeqTracker]` (`plugin_dispatch.py`);
`take_turn_and_reconcile(conversation: Conversation, dispatch: DispatchFn, tracker: ChildSeqTracker, *, user_input: str, provider: str, model: str, now: float, persist=...) -> tuple[TurnResult, Conversation]` (`plugin_dispatch.py`, added during the build-stage self-check — see Design's "Added during the build-stage self-check" note).

**Out**: a `plugins.DagResult` in every case — a clean terminal step, a
failed step, or an unresolved tool name are the same type, distinguished
by `failed_node`. Never an exception for any of those three; `run_graph`
and the closure `build_dispatch` returns are the only functions this item
adds to the request-to-result path, and neither raises for an expected
outcome.

**Errors**: this item's own code raises nothing for a scenario the design
above names as expected. It can still raise for a real bug in this item's
own code (an assertion, a `KeyError` on a node name `validate()` should
have already proven exists) — the same posture `run_child`'s own
`ChildDepthExceeded` already has toward a structural condition versus a run
outcome.

## Acceptance criteria

- [ ] `plugins.py` exports `InstalledPlugin`, `AskFn`.
- [ ] `plugin_manifest.py` exports `discover_plugins`, `run_graph`;
      `run_graph` reuses `_load_body_module` rather than a second
      import mechanism.
- [ ] A new module (`plugin_dispatch.py`) exports `PluginSet`,
      `build_plugin_set`, `ChildSeqTracker`, `build_dispatch`, and
      `take_turn_and_reconcile`; it is the only new module that imports both
      `conversation.py` and `plugins.py`/`plugin_manifest.py`.
- [ ] `plugins.py` and `plugin_manifest.py` import nothing from
      `conversation.py`, before or after this item.
- [ ] `compute`, `route`, and `stop` execution is proven entirely by unit
      tests over `tmp_path` plugin directories with fake bodies — no
      network, no real model call, per `testing-conventions`.
- [ ] A route node whose body returns a value outside its own declared
      `ports` ends the run with `failed_node` set to that node.
- [ ] A node whose own code raises ends the run with `failed_node` set to
      that node and a `text` containing neither the exception's message
      nor a traceback.
- [ ] A `call`, `each`, or `wait` node reached mid-walk ends the run with
      `failed_node` set to that node, never executes, never raises.
- [ ] A tool name absent from `plugin_set.by_tool` returns a `DagResult`
      with `failed_node` set, proven by a unit test — never raised.
- [ ] A unit test builds two `InstalledPlugin` fixtures declaring the same
      `entry.tool`, runs their combined `tool_specs` through the existing
      `conversation.build_surface`, and asserts `DuplicateToolError` —
      proving Requirement 1's "fail loudly" without new duplicate-detection
      code.
- [ ] `ask` node execution has a unit test with a stubbed `AskFn` (no
      network) covering both a `str` and a `None` return.
- [ ] One new, standalone script (outside `make test`, per CLAUDE.md)
      exercises a new, committed fixture plugin (a one-node, `ask`-only
      entry, `plugin_blueprint.md §3.4`'s minimal shape) end to end
      through `build_dispatch`, against a real model, using a real
      `OPENROUTER_API_KEY`. Its output is pasted into this item's own
      `review.md` under `## Evidence`.
- [ ] Neither `scripts/prove_conversation_e2e.py` nor
      `tests/fixtures/plugins/plugin-a`/`plugin-b` is modified by this
      item.
- [ ] `mypy src` passes.
- [ ] `make verify` ends `VERIFY OK`.

## Non-goals

- Executing a `call` node's real effect — waits on EXECUTION (§2.3/§2.4).
- `each` and `wait` node kinds — deferred (§6, §7.2).
- Installing, fetching, or verifying a plugin's tag (§9, unchanged).
- A new production entry point — no CLI, no change to `eval_harness.py`'s
  default `dispatch_factory`. `build_dispatch` is written to drop into
  `eval_harness`'s existing factory shape by partial application, but
  wiring that up is left to whoever next needs it.
- Re-validating an installed plugin's manifest on every dispatch call.
- One plugin's step calling a different plugin's entry tool — an open
  project-level question (`plugin_blueprint.md §12` Open question 2) this
  item does not decide, per intent's Constraints.
- Surfacing *why* a discovered plugin directory failed validation (an
  observability concern, not this item's proposed outcome).

## Rejected alternatives

Covered inline above under each guideline: hermes's stateful plugin
registry (Guideline 1); whole-run-state node inputs and a
DagResult/run_turn/take_turn spawn-count field (Guidelines 2 and 3); a
second duplicate-tool check (Guideline 3).

One more, narrower: reusing `conversation.DuplicateToolError` directly
from `plugins.py` instead of relying on `build_surface`'s own later call
to it. Not needed, once the duplicate scenario turned out to already be
caught downstream (above) — but even if it hadn't been, `plugins.py`
cannot import `conversation.py` (D1's invariant) to reach that exception
class, so a second, `plugins.py`-owned exception type would have been
required regardless. Recorded so a future reader doesn't rediscover the
same import-direction constraint from scratch.

## Concerns

**No real policy conflict, stated plainly rather than left implicit**:
`testing-conventions`' network ban and CLAUDE.md's "prove a real round
trip outside `make test`" rule point in different directions on their
face, but they were already reconciled by convention before this item
(CONV-09's own proof script) — `ask` is the only step kind touching a real
model, so it is the only one this item proves outside the unit suite.
Applying that convention here, not inventing a new resolution.

**The `ChildSeqTracker` mutable cell is a real exception to Guideline 4**,
justified in Design/Guideline 3 above. The risk if a future caller ignores
the reconciliation contract: two children spawned under the same
`node_name` collide on the same key. This surfaces loudly —
`ConversationAlreadyExists` from CONV-08's own `PRIMARY KEY` constraint,
not silent data loss — because that constraint already exists in a closed
block, but a reviewer of any future caller of `build_dispatch` should
still check the reconciliation line is actually there.

**`conversation.py`'s existing catch-all around a dispatch call**
(`run_turn`, around the `TOOL_ROUND` loop: `except Exception as e:
result_text = f"tool_error: {e}"`) already puts a raw exception's text in
front of the model for *any* dispatch failure, predating this item and
applying to any dispatch implementation, not only a plugin's. This item's
own `run_graph`/`build_dispatch` are built so they never raise — every
expected failure is a `DagResult` — so that fallback should never actually
fire for a plugin-shaped failure. It remains a latent gap in `run_turn`
itself; worth a maintain-stage note, not something this item's own scope
covers fixing.

**Silently dropping an invalid plugin from `discover_plugins`'s result**
(Requirement 2) trades a debugging signal for simplicity. Accepted because
surfacing *why* is an observability concern (Non-goals), but named here so
it reads as a decision, not an oversight.

**Two corrections surfaced during the build stage, not this design pass**,
recorded here rather than silently rewriting the Design section above as
if they had been foreseen:

1. `PluginSet`/`build_plugin_set` cannot live in `plugins.py` — they
   construct `PluginCatalogEntry`/`ToolSpec`, both `conversation.py`-owned,
   and `plugins.py` may never import it (this document's own
   import-direction invariant, now in CLAUDE.md). Both moved to
   `plugin_dispatch.py`. No interface or behavior changed, only which file
   two of the four new pieces live in.
2. `build_plugin_set` needs the schema file's *parsed contents* for
   `ToolSpec.parameters`, not the path `Entry.parameters` holds — reading
   it was missing from the first draft above. Read directly, trusting it
   is already valid JSON Schema (`validate()` already proved that for
   every entry an `InstalledPlugin` could ever carry), not re-validated.

**A caller obligation stated only in a docstring is not a caller
obligation enforced** — the build-stage `/simplify` self-check's altitude
pass named this directly against `ChildSeqTracker`'s reconciliation
contract. `take_turn_and_reconcile` (Design, Interface) closes it: a
caller going through it structurally cannot skip the reconciliation step.
Direct `dispatch`/`tracker` access remains available and is what this
item's own unit tests use, since they exercise `dispatch` without a real
`take_turn` call at all.
