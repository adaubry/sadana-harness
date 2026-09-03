# Spec: A fixed, honest list of actions a model can be told about

Intent: docs/tasks/C3-tool-surface/intent.md

## Requirements

1. A tool's metadata (what a model needs to know about one action) is a
   value with a description that is computed, not typed as a literal.
   Traces to intent's "Proposed outcome" (a description gets "the name that
   action actually has right now").
2. A description that mentions another tool always reflects that other
   tool's current identity in the same build, never a string written down
   once. Traces to intent's Problem (the staleness hermes's history shows)
   and the existing CLAUDE.md rule this spec discharges: "compose tool
   descriptions that reference other tools at definition-build time from
   the resolved set, ensuring no cross-reference is ever a literal."
3. The built list is a value nobody holding it can watch change out from
   under them. Traces to intent's "Proposed outcome" ("cannot change out
   from under anyone holding it").
4. A caller who needs only some of the actions can get a smaller version
   without the whole thing being rebuilt. Traces to intent's "Proposed
   outcome" (smaller version, "the whole thing" not rebuilt) and the
   blueprint's own CONV-02 test line ("filter never rebuilds").
5. Two tools cannot silently collide — building a list from a set that
   names the same action twice, under either its provider-facing identity
   or its cross-reference identity, fails loudly rather than picking one
   silently. Traces to intent's Constraints (nothing here decides what
   happens later, so ambiguity here must not be allowed to exist at all)
   and the blueprint's own §9 Risk ("a name collision... must fail loudly").
6. Everything is provable with made-up, test-only actions — no real tool,
   no MODEL-ACCESS, no turn loop. Traces to intent's Constraints directly.
7. Nothing here calls an action, batches actions, caps a result, or reads
   a config default — only leaves room for those to be added later. Traces
   to intent's Constraints (three separate declines, stated explicitly).

## Design

**Where this lives.** `src/sadana/conversation.py` — the same flat module
CONV-01 (C2) started, per B1/B2's one-flat-module-per-block convention,
already reaffirmed once in C2's own spec.md against the blueprint's 9-file
package proposal. This work item adds to it, it does not start a new file.

**Learning from the reference (guideline 1).** Read `../hermes-agent`'s
`tools/registry.py` (1335 lines) and `tools/tool_search.py` (1320 lines)
directly, not just the blueprint's summary of them.

- `ToolEntry` (`tools/registry.py:204-233`) carries `name`, `toolset`,
  `schema`, `handler`, `check_fn`, `requires_env`, `is_async`,
  `description`, `emoji`, `max_result_size_chars`, and
  `dynamic_schema_overrides` — eleven fields, most tied to concerns this
  project has already placed elsewhere or hasn't built yet (`toolset` and
  `emoji` are UI/grouping concerns with no consumer here; `check_fn` and
  `requires_env` are availability-probing, a EXECUTION/PLUGIN-SYSTEM
  concern; `is_async` is an implementation detail this project doesn't
  need to declare separately). Declined wholesale; see the `ToolSpec`
  design below for what's actually kept.
- `dynamic_schema_overrides` (`tools/registry.py:226-233`, invoked at
  `:1079-1090` inside `get_definitions()`) is the closest thing hermes has
  to "a tool's description computed from something outside itself" — and
  reading `get_definitions()` (`:1044-1091`) confirms the blueprint's own
  critique directly: the override callable runs **on every
  `get_definitions()` call**, and the calling code
  (`model_tools.get_tool_definitions`, outside this file) is the one
  responsible for caching it correctly, keyed on a config file's mtime —
  a manually-maintained cache-invalidation contract, not a guarantee. This
  spec adopts the *idea* (a callable resolved against live state rather
  than a baked-in literal) and declines the *timing*: here, every
  description is rendered exactly once, at `build_surface()`, producing an
  immutable value — there is no cache to get right because there is
  nothing to invalidate. See Rejected alternatives.
- `tool_search.py`'s docstring (`:1-41`) names its own hard-won lesson
  directly: "The catalog is stateless across turns and tools-array
  assemblies. It is rebuilt from the current tool-defs list every time" —
  learned from a session-keyed catalog drifting out of sync with a live,
  mutable registry (OpenClaw issue #84141). That lesson doesn't transfer
  as "rebuild every time" here, because the thing it protects against — a
  cache drifting from a *live, mutating* registry — can't happen to a
  value that is never mutated in the first place; `ToolSurface` in this
  design is built once and never touched again. What *does* transfer, and
  is worth keeping as the seam for later: "the tool surface is a value
  computed once," which is this design's central choice, not an add-on.
  Progressive disclosure itself (the three bridge tools, tiered listing
  budgets) is declined — it exists to solve catalog bloat from thousands
  of MCP tools, which nothing in this project has yet; the blueprint's own
  verdict ("defer") is confirmed correct by reading the file, not just
  trusted.
- `tools/registry.py:1044-1091`'s result shape (`{"type": "function",
  "function": {...}}`) matches, byte-for-byte, what
  `src/sadana/model_providers/openrouter/provider.py:46-47` does with
  `Request.tools` (`body["tools"] = list(request.tools)`, passed straight
  through with no reshaping). This spec's rendered output has to match
  that exact shape — confirmed by reading the provider, not assumed.

**The identity problem the blueprint's own test line requires solving.**
Blueprint §7's CONV-02 line names its own acceptance test: "a description
referencing another tool changes when that tool is renamed in the resolved
set." A tool's metadata in this design is an immutable value — its
provider-facing `name` cannot change in place. So "renamed" has to mean: a
later build passes a *different* `ToolSpec` object with a different
`name`, standing in for the same logical action. For another tool's
`describe()` to notice that substitution and produce a different string,
it needs a way to say "that action" that survives the substitution — and
`name` itself can't be that way, because `name` is exactly the thing that
changed. Blueprint §5.2's own `ToolSpec` shape doesn't name a second
identity field, but without one this test is unwritable. This spec adds
one: `key`.

**`ToolSpec`** (frozen dataclass):

```python
ResolvedNames = Mapping[str, str]  # key -> current name, for this build only

@dataclass(frozen=True)
class ToolSpec:
    key: str                                   # stable; never sent to a provider
    name: str                                  # provider-facing; unique per build; may change across builds
    parameters: dict                           # JSON schema for the tool's arguments
    describe: Callable[[ResolvedNames], str]
```

`key` is required, with no default derived from `name` — a default would
silently defeat the whole point for any author who didn't think to set one
explicitly (CLAUDE.md: "names, not pointers," a deliberate identifier, not
an accident of whatever the current display string happens to be).

No `handler`, `concurrency`, `effects`, or `max_result_chars` field —
blueprint §5.2 lists all four, but every one of them is dispatch-time
policy, and this work item declined dispatch entirely (Plan interview).
Same precedent C2 set for `TurnKey` ("introducing it now would be state
with no reader yet"): these four fields have no reader here, and adding
them later is a pure, additive dataclass change when CONV-05 actually
needs them — cheaper than carrying inert fields no test exercises now.

**`build_surface(specs) -> ToolSurface`:**

```python
ToolSurface = tuple[dict, ...]  # provider-format definitions, rendered once

class DuplicateToolError(Exception): ...

def build_surface(specs: Iterable[ToolSpec]) -> ToolSurface: ...
```

`ToolSurface` is a type alias, not a wrapper class — the same choice C2
made for `Transcript` (declined a stateful protocol/class there; declined
one here for the same reason, see Rejected alternatives). There is exactly
one thing a caller needs from a built surface: the provider-format
definitions list, which `model_access.Request.tools` already accepts
verbatim as `tuple[dict, ...]`. Wrapping that in a class would add a type
with one field and no behavior a free function can't already provide.

`build_surface`:

1. Materializes `specs` into a tuple (two-pass: a tool can reference
   another tool defined later in the same iterable, so every key must be
   known before any `describe()` runs).
2. Raises `DuplicateToolError` if any two specs share a `name`, or if any
   two share a `key` — checked as two separate collisions, since either
   one alone would make the result ambiguous (two definitions with the
   same provider-facing name, or a `resolved_names` lookup that can't tell
   which of two tools a key means).
3. Builds `resolved_names: ResolvedNames = {s.key: s.name for s in specs}`
   once.
4. Calls `spec.describe(resolved_names)` exactly once per spec, and
   assembles `{"type": "function", "function": {"name": spec.name,
   "description": <that string>, "parameters": spec.parameters}}` for
   each, in the order given.
5. Returns the tuple of those dicts. Nothing about the original `ToolSpec`
   objects — `key`, `describe`, the callable itself — survives past this
   call. Once definitions are rendered, this module has no further use for
   them, which is a direct, welcome consequence of declining dispatch: a
   design that kept `handler` around would have to keep the whole
   `ToolSpec` alive for `dispatch()` to later call; this one doesn't.

**`filter_surface(surface, names) -> ToolSurface`:**

```python
def filter_surface(surface: ToolSurface, names: frozenset[str]) -> ToolSurface: ...
```

Selects the definitions whose `"name"` is in `names`, preserving order.
Nothing is re-rendered — there is nothing left to re-render, since
`ToolSpec`/`describe` objects were already discarded in step 5 above. This
is what "filter never rebuilds" means concretely: not "the code happens to
avoid extra work" but "the thing filtering would need to rebuild from
isn't reachable from here." An empty result (`names` matches nothing) is a
valid, un-erroring return, not a failure — a child conversation with an
empty tool subset is a legitimate shape per the blueprint's own
`ChildSpec.tools: frozenset[str]` (may be empty).

**`surface_hash(surface) -> str`:**

```python
def surface_hash(surface: ToolSurface) -> str: ...
```

`sha256` of the definitions serialized with sorted keys and no
whitespace — deterministic regardless of dict-construction order. Derived
on demand from `surface` itself, not stored on any object — same choice
C2 made for `MessageKey.msg_seq` (derived from `len(messages) - 1`, never
a counter). This is needed later by CONV-06's prompt-stability assertion
(`sha256(system_prompt + definitions_json)`, blueprint §4.4); this work
item only produces the piece of that hash it owns, over its own value.

## Interface

**In:** `build_surface` takes any `Iterable[ToolSpec]`. `filter_surface`
and `surface_hash` take a `ToolSurface` (`tuple[dict, ...]`) produced only
by `build_surface`.

**Out:** `build_surface` and `filter_surface` return `ToolSurface` or raise
`DuplicateToolError` (build only — filtering an already-valid surface
cannot introduce a new collision). `surface_hash` returns `str`, never
raises.

**Errors:** `DuplicateToolError` is the only exception type this addition
defines. Raised before any description is rendered (the duplicate check
runs before step 3), so a caller never receives a partially-built surface.

## Acceptance criteria

- [ ] `build_surface` raises `DuplicateToolError` when two specs share a
      `name`.
- [ ] `build_surface` raises `DuplicateToolError` when two specs share a
      `key`, even with different `name`s.
- [ ] A spec's `describe(resolved_names)` sees every spec's current
      `key -> name` in the same build, including specs that appear later
      in the input than it does.
- [ ] Building once with `ToolSpec(key="export", name="export_csv", ...)`
      and again with `ToolSpec(key="export", name="export_data", ...)` —
      same key, different name — produces a different rendered description
      for any other spec whose `describe` looks up that key. This is
      blueprint §7's own named test for CONV-02.
- [ ] `filter_surface` returns only the definitions whose `"name"` is in
      the requested set, in their original order.
- [ ] `filter_surface` does not invoke any `describe` callable — provable
      with a `describe` that increments a counter on every call, showing
      the counter is unchanged after any number of `filter_surface` calls.
- [ ] `filter_surface` with a `names` set matching nothing returns an
      empty tuple, not an error.
- [ ] `surface_hash` returns the same value for the same definitions
      regardless of dict key insertion order, and a different value when
      any rendered field differs.
- [ ] Every test constructs its own made-up `ToolSpec` values — no real
      tool, no `model_access`, no turn loop.

## Non-goals

- Calling, dispatching, or otherwise invoking any tool's handler — there
  is no `handler` field at all in this work item's `ToolSpec`.
- Batching, concurrency policy, result-size capping, or error
  normalization for a tool call — all CONV-05's tool-round job.
- Anything tied to a live `Conversation` object, mounting a plugin, or
  deferred invalidation of an existing surface — CONV-06.
- Resolving a config-driven default for anything — CONV-03 owns config
  keys; this work item has no field that would need one (no
  `max_result_chars`).
- JSON-Schema validation of a spec's `parameters` — passed through as a
  plain `dict`, same trust-boundary posture C2 took with `tool_calls`.

## Rejected alternatives

**Copying `ToolEntry`'s eleven-field shape, declined.** Read directly
(`tools/registry.py:204-233`) rather than assumed from the blueprint's
summary. Eight of its eleven fields (`toolset`, `handler`, `check_fn`,
`requires_env`, `is_async`, `emoji`, `max_result_size_chars`,
`dynamic_schema_overrides`) belong to concerns this work item explicitly
declined (dispatch, availability probing, UI grouping, config defaults) or
adopted in a different shape (`dynamic_schema_overrides`'s idea, not its
per-call timing). Kept only what a value with a computed description
actually needs: `name`, `parameters`, `describe` — plus `key`, which
hermes's shape doesn't have at all, added because the stated acceptance
test is unwritable without it.

**A `ToolSurface` class wrapping the definitions list, declined.** Same
reasoning C2 already used against a stateful `Transcript` protocol: a
`Protocol`/class earns its place once a second implementation exists to
abstract over. There is exactly one shape here — `tuple[dict, ...]`,
already exactly what `model_access.Request.tools` accepts — and no method
a free function can't provide as cheaply. `filter_surface` and
`surface_hash` take the tuple directly; nothing is lost by not having a
`.filter()`/`.hash()` method, and nothing is gained.

**Progressive tool disclosure (hermes's `tool_search.py`), declined.**
Confirmed by reading the file, not just the blueprint's verdict: it exists
to solve catalog bloat from thousands of MCP-server tools
(`tool_search.py:17-29`, tiered listing budgets down to a bare
per-server summary at ~3,300 tools). Nothing in this project has an MCP
client or anywhere near that many tools. The one thing worth keeping from
it — "the tool surface is a value computed once" — isn't a feature to add
later on top of this design; it already *is* this design's central choice,
so there's nothing deferred to name beyond "if catalog size ever becomes a
problem, it wraps `ToolSurface`, not this module."

**`dynamic_schema_overrides`'s per-call timing, declined.** Adopted the
idea (a description computed from live state, not a literal); declined
having it re-run on every read. `get_definitions()`
(`tools/registry.py:1044-1091`) calls every entry's override callable on
every single invocation, pushing correct caching onto whichever caller
asks for definitions most recently (`tools/registry.py:1074-1078`'s
comment names the caller's own memoization scheme explicitly). Rendering
once, at `build_surface()`, into an immutable value removes the caching
problem instead of solving it correctly — there is no second invocation to
keep in sync with the first.

**A default for `key` derived from `name`, declined.** Would silently
defeat the guarantee for any author who didn't think to set `key`
explicitly — the exact accidental-literal failure mode this work item
exists to close, reintroduced through a default instead of a typo.

## Concerns

**`ToolSpec` carries a `parameters: dict` field, so — like `Message` in
C2 — the frozen dataclass's auto-generated `__hash__` will raise
`TypeError` if anything ever calls `hash()` on it.** Nothing in this
design does: `ToolSpec` objects are discarded inside `build_surface` once
definitions are rendered (see Design, step 5), so there's no set/dict use
anywhere that would trigger it. Same disclosed, unfixed posture as C2's
`Message` — noted for consistency rather than left to be rediscovered.

**The `key`/`name` split is this spec's one real invention, not something
lifted from the blueprint or the reference corpus.** It's a small, cheap
addition (one field, checked once at build time), but it's worth a second
pair of eyes precisely because nothing else in this project or hermes
already validates the idea — if a simpler mechanism satisfies the same
acceptance test, this is where that alternative would show up.

No other unresolved policy conflict. `testing-conventions` is the only
policy skill present and it applies cleanly (pure functions, no network,
no clock, no filesystem, no source-reading); `project-structure` and
`reference-lookup` still don't exist in this project, stated explicitly
per the same posture C2 took.
