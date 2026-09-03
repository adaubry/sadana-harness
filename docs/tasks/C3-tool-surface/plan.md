# Plan: A fixed, honest list of actions a model can be told about (from intent.md 2026-09-03)

## Files that change

- `src/sadana/conversation.py` — same file C2 added to; this work item
  appends to it, not a new file. New additions: `ResolvedNames` (type
  alias, `Mapping[str, str]`), `ToolSpec` (frozen dataclass: `key`, `name`,
  `parameters`, `describe`), `DuplicateToolError`, `ToolSurface` (type
  alias, `tuple[dict, ...]`), `build_surface(specs)`, `filter_surface(surface,
  names)`, `surface_hash(surface)`.
- `tests/unit/test_conversation.py` — same file C2 added to; new tests
  appended, one per `spec.md` acceptance criterion.

## Design recap (from spec.md, restated so this plan stands alone)

```python
ResolvedNames = Mapping[str, str]  # key -> current name, for this build only

@dataclass(frozen=True)
class ToolSpec:
    key: str                                   # stable; never sent to a provider
    name: str                                  # provider-facing; unique per build
    parameters: dict                           # JSON schema for the tool's arguments
    describe: Callable[[ResolvedNames], str]

class DuplicateToolError(Exception): ...

ToolSurface = tuple[dict, ...]  # provider-format definitions, rendered once

def build_surface(specs: Iterable[ToolSpec]) -> ToolSurface: ...
def filter_surface(surface: ToolSurface, names: frozenset[str]) -> ToolSurface: ...
def surface_hash(surface: ToolSurface) -> str: ...
```

`build_surface`:
1. Materializes `specs` into a tuple first (two-pass — a tool can
   reference another tool defined later in the iterable, so every key must
   be known before any `describe()` call).
2. Raises `DuplicateToolError` if any two specs share a `name`, or if any
   two share a `key` — before rendering anything.
3. Builds `resolved_names = {s.key: s.name for s in specs}` once.
4. Calls `spec.describe(resolved_names)` exactly once per spec, in order,
   assembling `{"type": "function", "function": {"name": spec.name,
   "description": <result>, "parameters": spec.parameters}}` per spec —
   this exact shape matches what
   `src/sadana/model_providers/openrouter/provider.py:46-47` already does
   with `Request.tools` (passed straight through, `body["tools"] =
   list(request.tools)`), confirmed by reading that file.
5. Returns the tuple. `ToolSpec`/`describe` objects are not retained past
   this call.

`filter_surface` selects definitions whose `"name"` is in `names`,
preserving order — pure tuple filtering, no `describe` re-invoked (nothing
to re-invoke; step 5 already discarded the specs). An empty match is a
valid empty tuple, not an error.

`surface_hash` is `sha256` over the definitions serialized with sorted
keys and no whitespace (`json.dumps(list(surface), sort_keys=True,
separators=(",", ":"))`), derived fresh each call — not stored on
anything, same choice C2 made for `MessageKey.msg_seq`.

## Order of work

1. **Add `ResolvedNames`, `ToolSpec`, `DuplicateToolError`, `ToolSurface`,
   `build_surface`, `filter_surface`, `surface_hash` to
   `src/sadana/conversation.py`**, after the existing CONV-01 content
   (`repair()`), with a short section-comment marking the CONV-02
   addition — mirrors how `spec.md` itself treats this as an addition to
   the same file rather than a rewrite. Needs a new import,
   `collections.abc.Mapping` (or `typing.Mapping`) and `Iterable`, plus
   `hashlib` and `json` for `surface_hash`.
2. **Append tests to `tests/unit/test_conversation.py`**, one per
   `spec.md` acceptance criterion, under a new `# ── tool surface ──`
   section following the existing sections. Run `make test` scoped to
   this file first, then the full narrow check.
3. **Run `make verify`** and paste its output as this stage's evidence.

## Risks

**What could this change break?** Nothing existing. `build_surface`,
`filter_surface`, `surface_hash`, and the new types are pure additions to
`conversation.py` — no existing function (`pending_tool_call_ids`,
`append`, `repair`) is touched, and nothing in `src/sadana/` imports
anything from this module yet (confirmed at C2's own Build stage and still
true — nothing new calls into `conversation.py` between C2 and now). The
only shared fixture in play, `tests/conftest.py`'s state-dir isolation, is
inert here too — no filesystem/network/clock use.

**Which step is riskiest?** Step 1's `key`/`name` collision check and the
two-pass ordering (materialize before rendering any description) —
getting either wrong is subtle: checking for a `name` collision but not a
`key` collision (or vice versa) passes every test that doesn't specifically
construct that case, and rendering descriptions in a single pass instead of
two would make the acceptance criterion "a tool can reference one defined
later in the iterable" fail only for that specific ordering, not in
general. Mitigation: the test list below includes both collision cases
separately (not just "duplicate specs raise") and a forward-reference case
(a tool early in the list references a key belonging to a tool later in
the list) as its own explicit test, not folded into the rename test.

**Is this drifting back toward anything `spec.md` already rejected?**
Checked against all five Rejected Alternatives entries: no `handler`/
`concurrency`/`effects`/`max_result_chars` field on `ToolSpec` (the
eleven-field `ToolEntry` shape, declined), no `ToolSurface` wrapper class
(`tuple[dict, ...]` type alias only), no progressive-disclosure machinery,
no per-call re-rendering of descriptions (`build_surface` renders each
`describe()` exactly once, ever), no default for `key` derived from `name`
(required field, no default value at all in the dataclass).

## Proof

`tests/unit/test_conversation.py` covers, at minimum, one test per
`spec.md` acceptance-criteria checkbox:

- `build_surface` raises `DuplicateToolError` for two specs sharing a
  `name` (different `key`s).
- `build_surface` raises `DuplicateToolError` for two specs sharing a
  `key` (different `name`s) — the case a `name`-only check would miss.
- A spec earlier in the input list can reference (via `resolved_names`) a
  `key` belonging to a spec later in the same input list — the
  two-pass/forward-reference case, tested on its own.
- Building with `ToolSpec(key="export", name="export_csv", ...)` then
  again with `ToolSpec(key="export", name="export_data", ...)` — same key,
  different name — produces a different rendered description for another
  spec whose `describe` looks up `"export"`. This is blueprint §7's own
  named CONV-02 test.
- `filter_surface` returns only matching-`name` definitions, in original
  order.
- `filter_surface` never invokes `describe` — proven with a `describe`
  that increments a closure-captured counter; the counter is unchanged
  after any number of `filter_surface` calls following one `build_surface`
  call.
- `filter_surface` with no matching names returns `()`, not an error.
- `surface_hash` is equal for the same definitions built with different
  dict key insertion order, and different when any rendered field
  (name/description/parameters) differs.
- Every test constructs its own `ToolSpec` values by hand — no
  `model_access`, no turn loop, no real tool.

`make verify` run at the end, pasted in full, ending `VERIFY OK`, is this
stage's evidence.
