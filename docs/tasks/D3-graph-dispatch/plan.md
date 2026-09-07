# Plan: A plugin's graph actually runs (from intent.md 2026-09-07)

## Files that change

**New:**
- `src/sadana/plugin_dispatch.py` — `ChildSeqTracker`, `build_dispatch`. The
  only new module allowed to import both `conversation.py` and
  `plugins.py`/`plugin_manifest.py` (spec.md's import-direction invariant,
  now also in CLAUDE.md).
- `tests/unit/test_plugin_dispatch.py` — unit tests for `build_dispatch`'s
  closure and `ChildSeqTracker`, entirely offline (stubbed `run_graph`/
  `run_child`, no real model call).
- `tests/fixtures/plugins/plugin-c/` — a new, committed fixture: one entry,
  one terminal `ask` node, no `init.py` (no compute/route body needed). Used
  only by the standalone script below. Does not touch `plugin-a`/`plugin-b`.
- `scripts/prove_plugin_dispatch_e2e.py` — standalone script (outside
  `make test`), proving the `ask` path end to end against a real model.

**Modified:**
- `src/sadana/plugins.py` — adds `InstalledPlugin`, `AskFn`, `PluginSet`,
  `build_plugin_set`. Pure; no new import of `conversation.py` or any I/O.
- `src/sadana/plugin_manifest.py` — adds `discover_plugins` and `run_graph`.
  `run_graph` reuses the existing `_load_body_module` (already private to
  this module) rather than a second import mechanism.
- `tests/unit/test_plugins.py` — new `build_plugin_set` tests, plus the
  duplicate-entry-tool acceptance test (this test file, not `plugins.py`
  itself, may import `conversation.build_surface`/`DuplicateToolError` —
  the invariant binds the two source modules, not their tests).
- `tests/unit/test_plugin_manifest.py` — new `discover_plugins` tests, and a
  new `run_graph` section covering every node kind and every named failure
  mode.

**Confirmed untouched** (existing consumers of `plugins.py` types, checked
by grep): `src/sadana/conversation.py`, `src/sadana/eval_harness.py`,
`scripts/prove_conversation_e2e.py`, `scripts/prove_context_completion.py`,
`scripts/eval/tasks/plugin_dispatch.py`, `tests/fixtures/plugins/plugin-a`,
`tests/fixtures/plugins/plugin-b`. None of `plugins.py`'s existing types
change shape; this item only adds new names beside them, and `build_dispatch`
is shaped to drop into `eval_harness.run_task`'s existing `dispatch_factory`
parameter by partial application without editing `eval_harness.py`.

## Order of work

1. **`plugins.py`: `InstalledPlugin`, `AskFn`, `PluginSet`, `build_plugin_set`.**
   Pure, zero risk to anything existing, fastest to prove. Tests in
   `test_plugins.py`: one `PluginCatalogEntry` + one `ToolSpec` per `Entry`
   across multiple plugins (`entry.tool` as both `key`/`name`, `entry.purpose`
   verbatim as `describe`'s output); and the duplicate-tool test — two
   `InstalledPlugin`s sharing an `entry.tool`, combined `tool_specs` run
   through the existing `conversation.build_surface`, asserting
   `DuplicateToolError` **and** asserting `build_plugin_set` itself raised
   nothing (proves no second check was added, not just that something
   works). Narrow check: `bash scripts/run_tests.sh tests/unit/test_plugins.py && mypy src`.

2. **`plugin_manifest.py`: `discover_plugins`.** Small, I/O-only, reuses
   the already-closed `validate()` with no new execution semantics. Tests:
   a `tmp_path` root with one plugin that validates and one that doesn't
   (e.g. missing `plugin.toml`) — only the valid one comes back, no signal
   about the excluded one. Narrow check:
   `bash scripts/run_tests.sh tests/unit/test_plugin_manifest.py`.

3. **`plugin_manifest.py`: `run_graph`'s offline path — `compute`, `route`,
   `stop`, and `call`/`each`/`wait` recognized-but-refused.** The walk loop
   itself (current node → execute → decide next) is the largest, most novel
   piece of new logic in this item; landing and fully proving its shape
   before touching `ask` isolates the new algorithm from the new
   integration. Carries exactly one `value` variable through the loop,
   reassigned each iteration — never an accumulating history. Tests: a
   three-node `compute → compute → compute` chain where each body raises
   unless handed exactly the immediately-prior value (proves Requirement 6
   directly, and would catch an accidental "pass everything"
   implementation); `stop` terminal; `route` following a declared port;
   `route` returning an undeclared port → `failed_node` set to that node; a
   raising node body → `failed_node` set, `text` contains neither the
   exception's message nor a traceback; each of `call`/`each`/`wait` reached
   mid-walk → `failed_node` set, body never invoked, nothing raised; `trace`
   populated in walk order for every case above. Narrow check: same test
   file, still fully offline.

4. **`run_graph`'s `ask` node kind**, via a stubbed `plugins.AskFn` (no
   network) — one more branch in the same loop step 3 already proved, not a
   restructuring. Tests: `ask` returning a `str` becomes the next
   predecessor value; `ask` returning `None` → `failed_node` set to that
   node. Narrow check: same test file, still offline.

5. **`plugin_dispatch.py`: `ChildSeqTracker` + `build_dispatch`.** The only
   step needing `conversation.py` (`Conversation`, `ChildSpec`, `run_child`,
   `replace`), and the direct resolution of
   `docs/reference/dispatch_closure_state_bug.md`'s previously-unresolved
   gap. Tests in `test_plugin_dispatch.py`, offline: a tool name absent from
   `plugin_set.by_tool` → `DagResult` with `failed_node` set, nothing raised
   (Requirement 7); the tracker's own bookkeeping, with `run_graph`/
   `run_child` stubbed so no real model call happens in the unit suite.
   Narrow check: `bash scripts/run_tests.sh tests/unit/test_plugin_dispatch.py && mypy src`.
   Also run and paste: `grep -n "^from sadana\|^import sadana" src/sadana/plugins.py src/sadana/plugin_manifest.py`
   — confirms neither imports `conversation`.

6. **Checkpoint**: `mypy src` across the full diff, then `make verify`,
   before touching anything network-facing.

7. **The new fixture** (`tests/fixtures/plugins/plugin-c/plugin.toml` +
   `skills/.../SKILL.md`). Nothing before this needs it; it exists only for
   step 8.

8. **The standalone script** (`scripts/prove_plugin_dispatch_e2e.py`), run
   manually against a real `OPENROUTER_API_KEY`, exercising
   `discover_plugins` → `build_plugin_set` → `build_dispatch` → `run_graph`'s
   `ask` path → a real `run_child` call, against `plugin-c`'s one-node entry.
   Output is this item's own Deploy-stage evidence (pasted into `review.md`
   later, not part of this plan).

Per build-skill's phase two, after step 8 a self-check (`/ponytail-review`
+ `/simplify` against the full diff) runs before the final `make verify`;
not a numbered step here since its outcome isn't known yet.

## Risks

**(a) What already-working code could this disturb?** Named, not
hypothetical: `plugin_manifest.validate`/`_load_body_module` are reused,
not modified — `run_graph` calls `_load_body_module` without changing its
signature. Every existing consumer of `plugins.DagResult`/`SkillRef`/
`Entry`/`Manifest` (`conversation.py`, `eval_harness.py`, the three
existing `scripts/prove_*.py`/`scripts/eval/tasks/*.py` files, and every
dispatch-shaped stub across `test_conversation.py`/`test_conversation_store.py`/
`test_eval_harness.py`) keeps working untouched, because none of those
types' existing fields change shape — this item only adds new names beside
them. `conversation.build_surface`/`DuplicateToolError` is reused exactly
as it exists today. `eval_harness.run_task`'s `dispatch_factory` parameter
is not edited. Confirmed today (grep): neither `plugins.py` nor
`plugin_manifest.py` imports `conversation` — step 5 is the only step that
introduces that import anywhere, and it lands in the one module built for
exactly that purpose.

**(b) Riskiest step, and why.** Step 5. Three compounding reasons: it is
the only step crossing the import boundary spec.md names explicitly, so a
mistake there is an architecture violation, not just a bug; it is the
direct resolution of a previously-documented, previously-unsolved bug
(`dispatch_closure_state_bug.md`), whose reconciliation contract
(`conversation = replace(updated, next_child_seq=tracker.next_seq)` after
every `take_turn()` call) is enforced by convention, not the type system —
a future caller, including this item's own step 8 script, can silently
reintroduce the exact bug that document describes if it skips that line;
and it is the one step whose full correctness cannot be proven offline —
the unit tests stub `run_graph`/`run_child` to check the tracker's own
bookkeeping, but only step 8's real script exercises the real `run_child`
call end to end. Second-riskiest: step 8 itself, for the reason CLAUDE.md
already names — nothing in `make test` covers it, so a mistake there
surfaces only on a manual run, not in CI.

**(c) Guarding against drifting back to a rejected alternative.** Checked
against spec.md's `## Rejected alternatives` directly:

- **A duplicate-tool check inside `build_plugin_set`.** The natural-seeming
  implementation raises the moment two entries share `entry.tool` while
  building `by_tool`. Spec.md rejects this — `by_tool`'s construction must
  be a plain last-write-wins mapping, and `conversation.build_surface`
  (already closed) catches the collision downstream. Guard: step 1's
  duplicate-tool test asserts `build_plugin_set` itself raises nothing —
  only the later `build_surface` call does.
- **Threading child-seq through `take_turn`/`run_turn`/`DagResult` instead
  of the tracker.** Explicitly rejected in favor of the cheaper,
  narrower `ChildSeqTracker`. Guard: `conversation.py`'s own source is not
  in the Files-that-change list above at all — if a diff touches it, or
  `DagResult` gains a new field, that's the drift signal.
- **Whole-run-state node inputs.** Rejected directly with the user during
  planning. Guard: `run_graph`'s loop carries exactly one `value` variable;
  step 3's three-node compute-chain test (each body raises unless given
  exactly the immediately-prior value) catches an accidental
  pass-everything implementation.
- **Reusing `conversation.DuplicateToolError` from `plugins.py`.** Not
  live — nothing in this design needs `plugins.py` to name a duplicate
  outcome at all, and the import-direction invariant makes it structurally
  unavailable regardless. Guard: the import grep in step 5.

## Proof

Per requirement (spec.md numbering):

- **R1**: `test_plugins.py`'s `build_plugin_set` tests + the duplicate-tool
  test (step 1).
- **R2**: `test_plugin_manifest.py`'s `discover_plugins` tests (step 2).
- **R3, R4**: `test_plugin_manifest.py`'s `run_graph` happy-path tests per
  node kind, and the `trace`-ordering assertions threaded through each of
  them (steps 3-4).
- **R5**: the raising-body, undeclared-port, and `ask`-returns-`None` tests
  (steps 3-4).
- **R6**: the three-node compute-chain test (step 3).
- **R7**: the unknown-tool-name test in `test_plugin_dispatch.py` (step 5).
- **R8**: the `call`/`each`/`wait`-refused tests (step 3).
- **R9**: `scripts/prove_plugin_dispatch_e2e.py`'s manual run, output pasted
  into `docs/tasks/D3-graph-dispatch/review.md` under `## Evidence` at
  Deploy stage (step 8).

Cross-cutting, pasted as closing evidence:
- `grep -n "^from sadana\|^import sadana" src/sadana/plugins.py src/sadana/plugin_manifest.py`
  showing no `conversation` import in either file.
- `git diff --stat` showing `scripts/prove_conversation_e2e.py` and
  `tests/fixtures/plugins/plugin-a`/`plugin-b` do not appear.
- `mypy src` clean; `make verify` ending `VERIFY OK`.
