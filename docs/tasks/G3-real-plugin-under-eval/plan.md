# Plan: a real plugin run, actually checked (from intent.md 2026-09-07)

Work item: `G3-real-plugin-under-eval`. Spec: `docs/tasks/G3-real-plugin-under-eval/spec.md`.

## Context

`intent.md`/`spec.md` close out PLUGINS' execution half (blueprint items 8
and 9, combined by explicit direction) and the whole `plugin_blueprint.md`
work order. Six pieces, in dependency order: real `plugin-a`/`plugin-b`
manifests (with `plugin-a`'s stand-in upgraded to a real `call` node); a
defaulted `approve=` passthrough on `build_dispatch()` (nothing needed it
until a real `call` node existed); a capturing-dispatch wrapper moved into
`eval_harness.py` so `Task.grade` can finally see a turn's `DagResult`s;
the mechanical signature update that ripples to every existing grader;
`scripts/eval/tasks/plugin_dispatch.py` rewired to grade by trace instead
of prose; `scripts/prove_conversation_e2e.py` rewired to drive both real
plugins instead of hand-rolling them; and `plugin_blueprint.md`'s own
status closed out.

Already found and reused, not re-derived here:
- `tests/fixtures/plugins/plugin-a/skills/plugin-a-skill/SKILL.md` and
  `plugin-b/skills/plugin-b-skill/SKILL.md` already exist and are reused
  unchanged — only `plugin.toml`/`schema/`/`init.py` are new.
- `plugin-c`'s own layout (`tests/fixtures/plugins/plugin-c/plugin.toml`,
  `schema/do_thing.json`) is the exact template for `plugin-b`'s (single
  `ask` node, no `init.py`).
- `scripts/prove_execution.py:30`'s live target, `https://example.com`,
  reused verbatim for `plugin-a`'s `call` node body.
- `scripts/prove_plugin_dispatch_e2e.py:84-99`'s `_make_capturing_dispatch`
  is the exact pattern `eval_harness._capturing_dispatch` copies.
- `plugin_dispatch.build_dispatch` (`src/sadana/plugin_dispatch.py:104-133`)
  already has a defaulted `persist` kwarg — `approve` follows the same
  shape, defaulting to `plugin_manifest._default_approve`.
- `eval_harness.Task`/`run_task` (`src/sadana/eval_harness.py:36-141`) —
  `grade`'s `Callable` type gains one parameter; `run_task`'s body gains
  one wrapping step before calling `take_turn`.
- `tests/unit/test_plugin_dispatch.py`'s existing fixture-building style
  (`_conversation()`, `_write_schema()`, hand-built `InstalledPlugin`/
  `Manifest`/`Node`, `monkeypatch.setattr(plugin_dispatch, "run_child", ...)`)
  is what the new `approve`-passthrough test reuses.

## Files that change

- `CLAUDE.md` — the "Please do" rule spec.md establishes: a `Task`'s
  `grade()` reads `DagResult`/`NodeTrace` structure, never a substring
  match against rendered message content.
- `tests/fixtures/plugins/plugin-a/plugin.toml` (new)
- `tests/fixtures/plugins/plugin-a/schema/plugin_a_entry.json` (new)
- `tests/fixtures/plugins/plugin-a/init.py` (new)
- `tests/fixtures/plugins/plugin-b/plugin.toml` (new)
- `tests/fixtures/plugins/plugin-b/schema/plugin_b_entry.json` (new)
- `src/sadana/plugin_dispatch.py` — `build_dispatch` gains `approve=`; a
  new shared `capturing_dispatch()` (moved here during the self-check
  after landing as three separate copies — see Risks) is what
  `eval_harness.py` and `scripts/prove_conversation_e2e.py` both use.
- `tests/unit/test_plugin_dispatch.py` — one new test for the `approve`
  passthrough.
- `tests/unit/test_plugin_manifest.py` (or a new file — see step 2) — one
  new test walking the real `plugin-a`/`plugin-b` fixtures end to end with
  `execution.run_http` monkeypatched and fake `ask`/`approve`.
- `src/sadana/eval_harness.py` — `Task.grade`'s type gains a parameter;
  `run_task` wraps its dispatch (via `plugin_dispatch.capturing_dispatch`)
  and passes the captured tuple to `grade`.
- `tests/unit/test_eval_harness.py` — four existing `grade` functions gain
  the new (unused) parameter; one new test proves `run_task` actually
  threads captured `DagResult`s to `grade`.
- `scripts/prove_eval_harness.py` — one `grade` function gains the new
  parameter.
- `scripts/eval/tasks/plugin_dispatch.py` — `make_dispatch` deleted;
  `main()` rebuilt on `discover_plugins`/`build_plugin_set`/`build_dispatch`;
  `grade` rewritten to read `dag_results` structurally.
- `scripts/prove_conversation_e2e.py` — hand-written `dispatch()` replaced
  by the real mechanism; turn 2/3 assertions rewritten against captured
  `DagResult`s; child-isolation assertions dropped (already covered by
  `tests/unit/test_conversation.py`).
- `docs/reference/plugin_blueprint.md` — `Status` line and a closing note.
- No change: `src/sadana/plugin_manifest.py`, `src/sadana/plugins.py`,
  `src/sadana/conversation.py` — every mechanism this item uses (the
  `call`/`route`/`ask` walk, `Artifact` recording, `DagResult`) already
  exists from F1/G1/G2; this item only wires real content and real
  callers onto it.

## Order of work

1. **`plugin-a`'s real manifest, schema, and `init.py`** — the TOML,
   JSON schema, and Python body from spec.md's Design §1, written
   verbatim into `tests/fixtures/plugins/plugin-a/`.

2. **`plugin-b`'s real manifest and schema** — same, into
   `tests/fixtures/plugins/plugin-b/`, mirroring `plugin-c`'s existing
   ask-only layout exactly (no `init.py`).

3. **Prove both real fixtures in one unit test, network faked, model
   faked.** New test (placed in `tests/unit/test_plugin_manifest.py`,
   alongside the other `run_graph` tests, since it exercises `run_graph`
   directly): `discover_plugins(plugins_root=FIXTURES_ROOT)` (the real
   `tests/fixtures/plugins` directory, via `plugin_manifest.discover_plugins`'s
   existing `plugins_root` override — no env var mutation needed) finds
   both as `Valid`; `monkeypatch.setattr(execution, "run_http", ...)`
   returns a canned `Success`; a fake `ask` returns a string containing
   `"ACKNOWLEDGED"`; `run_graph` walks `plugin-a`'s real manifest end to
   end, producing the exact trace and `artifacts` spec.md's acceptance
   criteria name. A second call with the fake `ask` NOT acknowledging
   proves the `not_acknowledged` branch. A third call with a fake
   `approve` returning `False` proves the declined path. `plugin-b` gets
   one call proving its single `ask` node. This is the step that proves
   the shipped files are actually correct, before anything else builds on
   them.

4. **`build_dispatch`'s `approve=` passthrough** —
   `src/sadana/plugin_dispatch.py`'s exact one-line signature/body change
   from spec.md's Design §2. New test in `tests/unit/test_plugin_dispatch.py`
   (mirroring `test_build_dispatch_ask_updates_the_tracker_from_run_childs_updated_parent`'s
   fixture style): a hand-built `PluginSet` with one `call`-kind node
   (its own `init.py` written to `tmp_path`, matching G1/G2's own test
   style), an explicit fake `approve` — proves `build_dispatch` threads it
   through rather than silently falling back to `_default_approve` (which
   would hang the test on real stdin if the passthrough were missing —
   the test's own timeout is the tell).

5. **`eval_harness._capturing_dispatch` and `Task.grade`'s new
   parameter.** `src/sadana/eval_harness.py`'s exact addition from
   spec.md's Design §3. New test in `tests/unit/test_eval_harness.py`:
   a `dispatch_factory` returning a dispatch that yields one known
   `DagResult`; assert the `grade` function received exactly that tuple
   as its third argument.

6. **Update the four existing `grade` functions in
   `tests/unit/test_eval_harness.py`** to accept the new third parameter
   (named `_dag_results`, unused) — mechanical, no behavior change. Run
   this file alone before moving on, since step 5's own new test lives
   here too.

7. **Update `scripts/prove_eval_harness.py`'s one `grade` function**
   the same mechanical way.

8. **Rewrite `scripts/eval/tasks/plugin_dispatch.py`** per spec.md's
   Design §5: delete `make_dispatch`; `main()` calls
   `discover_plugins()`/`build_plugin_set()`/`build_dispatch(...,
   approve=auto_approve)` against the real `plugin-a` fixture; `grade`
   reads `dag_results[-1].trace` for a `port == "acknowledged"` entry.
   This is a manual-run script, not part of `make test` — its own
   correctness is proven by running it live in the Deploy stage, but the
   rewrite should be checked for import/syntax correctness now (`python
   -c "import ast; ast.parse(open('scripts/eval/tasks/plugin_dispatch.py').read())"`
   or simply attempting the import) before Deploy.

9. **Rewrite `scripts/prove_conversation_e2e.py`** per spec.md's
   Design §4: drop the hand-written `dispatch()`; build the real
   `plugin_set` once, but rebuild `dispatch`/`tracker` fresh before
   *every* turn (all four, not just 2/3) via `plugin_dispatch.build_dispatch`
   — reusing one dispatch closure across turns would close over a stale
   `conversation` (`build_dispatch`'s own docstring;
   `docs/reference/dispatch_closure_state_bug.md`), the same discipline
   `scripts/prove_plugin_dispatch_e2e.py` already follows; add
   `auto_approve` and its `approved_calls` list; wrap turns 2/3's fresh
   dispatch in a capturing-dispatch pair each (via the shared
   `plugin_dispatch.capturing_dispatch`); rewrite their assertions against
   the captured `DagResult` (trace shape, `artifacts`) instead of the
   internal `child`/`tool_surface` objects; drop
   `_assert_child_isolated`/the `tool_surface == ()` check (already
   covered by `tests/unit/test_conversation.py`, per spec.md's Design §4
   and Concerns). Turns 1 and 4 keep their existing user-facing behavior
   and assertions unchanged — they never call a plugin — but still need
   their own freshly-built dispatch, per the rule above.

10. **`docs/reference/plugin_blueprint.md`**: `Status: draft for design`
    → `Status: implemented`; a short closing note after §11's table
    naming items 1-9 closed and the step-17 checkpoint met, citing this
    work item.

11. **Full regression pass.** Run
    `tests/unit/test_plugin_manifest.py`, `tests/unit/test_plugin_dispatch.py`,
    `tests/unit/test_eval_harness.py`, `tests/unit/test_plugins.py`. All
    green.

12. **Self-check and verify.** `/ponytail-review` and `/simplify` against
    the diff; apply anything in the "worth taking now" bucket; then
    `make verify`. The two live scripts are run manually afterward, with
    a real `OPENROUTER_API_KEY`, and their output pasted as this item's
    Deploy evidence — `make verify` itself never touches them (testing-
    conventions' network ban).

## Risks

**What could this change break?** `Task.grade`'s widened `Callable` type
is the one change with real fan-out: every existing `Task` instance's
`grade` function (four in `test_eval_harness.py`, one in
`prove_eval_harness.py`) needs the mechanical third parameter or `mypy`
fails at `make verify`'s type-check stage, not silently at runtime — this
is caught early, in step 6/7, before the rest of the plan builds on top.
`build_dispatch`'s new `approve` kwarg is defaulted, so every existing
`test_plugin_dispatch.py` call site (none of which builds a `call`-kind
fixture) needs no change — step 4's own new test is additive, not a
modification to an existing one.

**Which step is riskiest?** Step 3 — it's the only step that exercises
the real, shipped `plugin-a`/`plugin-b` files end to end, and it's the
step every later script (8, 9) trusts implicitly. If `plugin-a`'s
`classify()` or node wiring has a mistake, step 3's own test catches it
with a fake network and a fake model, cheaply — the alternative is
discovering it only in step 9's live run, burning a real model call and a
real network round trip to find a typo in a TOML file. Ordered first
among the "new content" steps for exactly this reason.

**Drift back toward a rejected alternative?** Checked against spec.md's
`## Rejected alternatives`: no field added to `TurnResult`, no change to
`conversation.py`'s turn loop (steps 4-7 touch only `plugin_dispatch.py`
and `eval_harness.py`). No field added to `TaskRun` (step 5's own code
only threads `dag_results` through the ephemeral `grade()` call, nothing
persisted). `scripts/eval/tasks/plugin_dispatch.py` loses its own
hand-written dispatch entirely in step 8, not just a patched `grade` —
no second, independent copy of `plugin-a`'s logic survives. `plugin-a`'s
`call` node targets `https://example.com`, not a new endpoint (step 1).

## Proof

- Step 3's own test — real `plugin-a`/`plugin-b` fixtures, discovered and
  walked end to end (acknowledged branch, not-acknowledged branch,
  declined-approval branch, `plugin-b`'s single node), network and model
  faked.
- Step 4's own test — `build_dispatch`'s `approve` passthrough actually
  reaches `run_graph`.
- Step 5's own test — `run_task` actually threads captured `DagResult`s
  to `grade`.
- `tests/unit/test_eval_harness.py`, `tests/unit/test_plugin_dispatch.py`
  — existing tests green after their mechanical signature updates.
- `make verify` output, ending `VERIFY OK`, pasted as part of this
  stage's evidence.
- **Live evidence, gathered after `make verify`, pasted into Deploy's
  `## Evidence` alongside it**: `scripts/prove_conversation_e2e.py`'s
  full output (all four turns, `ALL ASSERTIONS PASSED`) and
  `scripts/eval/tasks/plugin_dispatch.py`'s full output (`score=1.0`,
  `ALL ASSERTIONS PASSED`), both run manually with a real
  `OPENROUTER_API_KEY`.
