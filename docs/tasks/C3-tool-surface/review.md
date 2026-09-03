# Review: A fixed, honest list of actions a model can be told about (from plan.md 2026-09-03)

Reviewed: 116e59c..HEAD (working tree) — 5 files, +722/-0
Reviewer context: same session as build — cold-read requirement satisfied by
delegating the actual review passes (steps 1-5 below) to a fresh subagent
with no prior context beyond intent.md/spec.md/plan.md and the diff; this
session performed the file-list scoping, the second-opinion commands, and
the write-up.
Second opinion: /ponytail-review and /simplify — both run (performed
directly in-session rather than via subagent), after the cold review's
findings were already written down.

## Evidence

```
docs/tasks/C3-tool-surface: all present artifacts valid
CHAIN OK
trim trailing whitespace.................................................Passed
fix end of files.........................................................Passed
mixed line ending.........................................................Passed
check for case conflicts..................................................Passed
check yaml.................................................................Passed
check toml.................................................................Passed
check json.................................................................Passed
check for merge conflicts..................................................Passed
check for added large files................................................Passed
check that scripts with shebangs are executable............................Passed
check that executables have shebangs........................................Passed
detect private key..........................................................Passed
ruff.........................................................................Passed
ruff-format...................................................................Passed
shellcheck.....................................................................Passed
Detect secrets.................................................................Passed
LINT OK
Success: no issues found in 4 source files
TYPES OK
▶ hermetic run: TZ=UTC LANG=C.UTF-8 PYTHONHASHSEED=0, empty env
........................................................................ [ 94%]
....                                                                     [100%]
76 passed in 0.28s
TESTS OK
VERIFY OK
```

## Findings

No Important findings this time — every acceptance criterion, every Proof
bullet, and every Rejected Alternative checked clean under adversarial
testing (collision detection, forward-reference rendering, hash
determinism across nested dict reordering). One Nit, confirmed
independently by both second-opinion commands.

### Nits

- [Simplification/Reuse/Efficiency, confirmed by both `/ponytail-review`
  and `/simplify`] `build_surface`'s duplicate-`name` and duplicate-`key`
  checks (`conversation.py:190-198`) repeat the same
  count-then-filter-then-sort logic twice, differing only in which
  attribute they read. `collections.Counter` would replace both blocks
  with one small helper (`{v for v, c in Counter(values).items() if c >
  1}`), removing the duplication and the `O(n²)` `list.count()` calls in
  the same move. Not applied here; listed for the decision below.
- [Simplification] `surface_hash` (`conversation.py:227`) converts
  `surface` (already a tuple) to a `list` before `json.dumps` —
  `json.dumps` serializes a tuple to a JSON array directly, so the
  conversion is a no-op step. Cosmetic; not applied.

### Checked and found clean

- **Bugs pass** (adversarial, via direct execution, not just reading):
  `key`==`name` across two different specs does not falsely collide — the
  two duplicate checks are genuinely independent namespaces, matching
  spec.md's explicit design. Two-pass rendering was proven non-stale for a
  forward reference. `filter_surface` silently ignores a requested name
  absent from the surface (matches spec.md's stated "un-erroring" return),
  confirmed by direct execution. `surface_hash`'s
  `json.dumps(..., sort_keys=True)` was confirmed, by direct execution
  with a three-level-nested JSON-schema `parameters` shape (not just the
  single-level case the shipped test covers), to sort recursively at every
  depth — the determinism claim holds for realistic tool schemas, not only
  the flat case `test_surface_hash_ignores_dict_key_order` exercises.
- **Security pass**: pure in-memory tuple/dict/dataclass operations, no
  filesystem/network/subprocess/eval. A raising `describe()` propagates
  uncaught out of `build_surface` — checked deliberately, not a finding:
  spec.md's own stated philosophy is fail-loudly, and nothing in
  Requirements or Acceptance criteria asks for a caught/normalized error
  path here.
- **File-list check**: `plan.md` § Files that change names exactly the two
  code files the diff touches (`conversation.py`,
  `tests/unit/test_conversation.py`); the three `docs/tasks/` artifact
  files are this work item's own chain deliverables, not an undocumented
  side effect — no repeat of C2's file-list gap.
- **`spec.md` § Acceptance criteria** (all 9) and **`plan.md` § Proof**
  (same 9, restated): every one discharged by a specifically-matched test,
  verified by reading each test's body, not just its name —
  `test_build_surface_raises_for_duplicate_name`,
  `test_build_surface_raises_for_duplicate_key`,
  `test_build_surface_resolves_a_forward_reference`,
  `test_build_surface_rename_changes_a_referencing_description` (the
  blueprint's own named CONV-02 test — confirmed it actually builds twice
  and diffs the two renders),
  `test_filter_surface_keeps_only_matching_names_in_order`,
  `test_filter_surface_never_reinvokes_describe`,
  `test_filter_surface_with_no_match_returns_empty_tuple`,
  `test_surface_hash_ignores_dict_key_order`,
  `test_surface_hash_changes_when_a_field_differs`.
- **`spec.md` § Rejected alternatives re-check** (all 5): `ToolSpec` has
  exactly 4 fields, no `handler`/`concurrency`/`effects`/
  `max_result_chars`; `ToolSurface` is a bare `tuple[dict, ...]` alias, no
  wrapper class; no progressive-disclosure machinery anywhere; `describe()`
  is called exactly once per spec, never again by `filter_surface`; `key`
  has no default. None crept back into the diff.
- **Design principles**: reference-corpus learning confirmed directly
  against `model_providers/openrouter/provider.py:46-47` (the rendered
  shape matches what's passed straight through to the request body, no
  reshaping); reducing bets (the narrow 4-field `ToolSpec`, no inert
  dispatch fields); reject-at-least-step-cost (both duplicate checks run
  strictly before any `describe()` call — rejected before anything is
  rendered, not after); minimizing mutable state (`surface_hash` is a pure
  function, nothing cached or stored). All clean.
- `make typecheck` (mypy) passes on the new code with zero issues; no new
  third-party dependency; no existing function in `conversation.py`
  (`append`, `repair`, `pending_tool_call_ids`) was touched.

## Decision

Approved by Adam, 2026-09-03, as-is. The one Nit (the duplicate name/key
collision-check block) is unfixed by request — no Important findings, no
correctness defects; `make verify` stayed green throughout.
