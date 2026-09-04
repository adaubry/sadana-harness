# Plan: One real conversation proves every promise this backbone made (from intent.md 2026-09-04)

## Files that change

- `scripts/prove_conversation_e2e.py` (new) — the proof script itself,
  following `scripts/prove_model_access.py`'s established shape (sys.path
  shim, a `MODEL` constant, small `async def` scenario-style functions, a
  `__main__` guard that checks `OPENROUTER_API_KEY` before spending
  anything).
- `tests/fixtures/plugins/plugin-a/skills/plugin-a-skill/SKILL.md` (new) —
  fixture skill body for plugin-a's spawned child. Frontmatter `name:
  plugin-a-skill`, a short `description`, and a body instructing the child
  to summarize its input in one sentence and always include a fixed marker
  word the script's "branch node" checks for.
- `tests/fixtures/plugins/plugin-b/skills/plugin-b-skill/SKILL.md` (new) —
  same shape, plugin-b's own skill, no marker-word requirement (plugin-b
  has no branch step).
- Nothing else. No `src/` changes, no `tests/unit/` changes — this script
  is deliberately outside `make test`'s suite (spec.md § Interface,
  § Non-goals), matching `prove_model_access.py`'s own precedent.

## Order of work

1. **Fixture `SKILL.md` files first.** No code depends on anything except
   these existing on disk at the exact path `load_skill()` expects
   (`<plugins_root>/<plugin>/skills/<skill>/SKILL.md`,
   `src/sadana/conversation.py:1104`). Getting the frontmatter right here
   first (a `name` that matches the directory, a `description` under 1024
   characters) means `load_skill()`'s own validation catches a typo, not a
   real-network round trip later.
2. **The two `ToolSpec`s and their dispatch handlers, exercised locally
   first.** `plugin_a_entry`'s three-step handler (synthesize a fixed
   input → `run_child()` → branch on the child's `final_text`) and
   `plugin_b_entry`'s one-line handler (`run_child()`, return
   `final_text`). Hand-run just the child-spawning path with a local smoke
   check — a fake/monkeypatched `complete()`, no real network — before
   ever touching `OPENROUTER_API_KEY`. This is the riskiest plumbing
   (matching `ChildSpec`, `filter_surface`, `SkillRef` exactly), cheapest
   to get wrong here, against nothing that costs money.
3. **Assemble the `ConversationTemplate`/`TemplateRecipe`** with both
   `PluginCatalogEntry`s and both `ToolSpec`s, and `create_conversation()`
   with `IterationBudget(max_total=5)` (spec.md § Design, "Sizing the
   iteration budget").
4. **Wire the four-turn sequence**, with the assertions from spec.md
   § Assertions inline after each relevant call, and the labelled
   print-per-step output spec.md § Interface describes.
5. **First real run**, `OPENROUTER_API_KEY` sourced by hand — never
   checked into anything, never touched by `make test`'s hermetic env. If
   turns 1-3 consume more than 5 iterations in practice, raise
   `max_total` to match what was actually observed and re-run — spec.md
   § Concerns already names this as expected calibration, not a defect.
6. `/ponytail-review` + `/simplify` self-check against the diff.
7. `make verify` (confirms nothing in the suite broke; this script itself
   is not part of what `make verify` runs).

## Risks

**What could this change break that already works?** Nothing — no
existing file is modified, only two new fixture files and one new script
are added. `make verify`'s own suite has zero new surface to regress
against, since nothing under `src/` or `tests/unit/`/`tests/integration/`
changes.

**Which step is riskiest, and why that one?** Step 5, the first real run —
not because the code is likely wrong (step 2's local smoke check already
exercises the child-spawning plumbing against a fake response), but
because it is the one step whose outcome depends on a real model's actual
behaviour rather than on this codebase's own logic. It is ordered last
among the code-writing steps, and explicitly expected to need at least
one budget adjustment per spec.md § Concerns, rather than treated as a
single pass/fail gate.

**Which options did spec.md already reject, and is this plan drifting
toward one?** Checked spec.md § Rejected alternatives against this plan:
not mocking the provider (step 5 is a real call, no mock anywhere in this
plan); not routing the well-formedness check through CONV-08's sqlite
store (step 4 uses `pending_tool_call_ids()` directly on the in-memory
history, per spec.md); not letting the model choose which plugin to call
(step 4's turn 2/3 prompts name the tool directly, per spec.md § Design);
not using a dotted `plugin.tool` naming convention (step 2 uses
`plugin_a_entry`/`plugin_b_entry`, matching what `ToolSpec`/`build_surface`
actually expect). No drift found.

## Proof

- Step 2's local smoke check (a fake `complete()`, no real network) proves
  the child-spawning wiring is correct before any money is spent — its
  output is pasted into the build conversation as an intermediate check,
  not into `review.md`.
- The real run's full stdout (step 5), once every assertion passes,
  pasted whole into `review.md § Evidence` — this **is** the Test stage's
  deliverable for this work item, per CLAUDE.md's rule on a block's first
  real external round trip.
- `make verify` output, confirming the rest of the suite is undisturbed,
  also pasted into `review.md § Evidence` alongside the real run.
