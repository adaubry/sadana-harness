# Review: C8-conversation-aggregate — the Conversation aggregate + template (from plan.md 2026-09-03)

Reviewed: HEAD..working tree — 3 files, +414/-0 (CLAUDE.md +1, `src/sadana/conversation.py` +198,
`tests/unit/test_conversation.py` +215), plus the new, untracked
`docs/tasks/C8-conversation-aggregate/{intent,spec,plan}.md`.
Reviewer context: fresh session — no prior context on this work item; first read of intent/spec/plan and
diff happened in this review.
Second opinion: `/ponytail-review`, `/simplify` — both ran (`/simplify` as its own 4-agent
reuse/simplification/efficiency/altitude pass, per its own procedure).

## Evidence

```
$ make verify
docs/tasks/C8-conversation-aggregate: all present artifacts valid
CHAIN OK
trim trailing whitespace.................................................Passed
fix end of files.........................................................Passed
mixed line ending........................................................Passed
check for case conflicts.................................................Passed
check yaml...............................................................Passed
check toml...............................................................Passed
check json...............................................................Passed
check for merge conflicts................................................Passed
check for added large files..............................................Passed
check that scripts with shebangs are executable..........................Passed
check that executables have shebangs.....................................Passed
detect private key.......................................................Passed
ruff.....................................................................Passed
ruff-format...............................................................Passed
shellcheck................................................................Passed
Detect secrets............................................................Passed
LINT OK
Success: no issues found in 4 source files
TYPES OK
▶ hermetic run: TZ=UTC LANG=C.UTF-8 PYTHONHASHSEED=0, empty env
........................................................................ [ 50%]
.......................................................................  [100%]
143 passed in 0.95s
TESTS OK
VERIFY OK
```

## Findings

Three passes run cold (bugs, security, compliance) plus a second opinion
(`/ponytail-review`, `/simplify`), all detailed below. Bugs pass: clean —
every acceptance criterion is discharged by a named test, no logic gap
found. Security pass: clean — no secrets, no injection surface, no
external I/O in this diff at all (pure in-memory dataclasses and
functions). One Important compliance finding, already fixed in this
branch (a plan.md documentation gap, not a code defect); three cosmetic
Nits; details below.

### Important

- **[Compliance] `CLAUDE.md` is changed by this diff but is not listed in `plan.md` § Files
  that change, and the artifact chain carries no record that the change was actually
  confirmed.** `spec.md` (lines 464–487) proposes a one-line CLAUDE.md amendment and ends with
  "Please confirm whether to add this line to CLAUDE.md before I continue to the build stage" —
  spec.md itself treats this as a gate on continuing, not a decision the build stage gets to make
  on its own. `plan.md` § Files that change (lines 24–33) names only `src/sadana/conversation.py`
  and `tests/unit/test_conversation.py`; it says nothing about `CLAUDE.md`, and neither `plan.md`'s
  Context section nor any trace section records that the maintainer answered the question spec.md
  asked. The line that was actually added to `CLAUDE.md` (line 27) matches the proposed text
  verbatim, so the *content* is not in question — but this review has no artifact showing the
  confirmation happened, only the edit itself. Per this stage's own procedure (§3: "A file touched
  that the plan did not name is an Important finding"), this is exactly that case, and it is also
  the kind of self-authorized step CLAUDE.md's "Do not… approve your own work or merge on your own
  judgement" line exists to prevent one level up from code review. Resolution needed from the
  maintainer: either confirm the amendment was in fact approved (and this is a plan.md documentation
  gap only), or treat the CLAUDE.md edit as unapproved and pull it out of this diff pending a
  decision.

  **Post-review fix:** Adam did approve the amendment explicitly in conversation immediately after
  `spec.md` asked ("yes"), before the build stage began — this review's finding is correct that no
  *artifact* recorded it, not that it didn't happen. `plan.md` § Files that change now lists
  `CLAUDE.md` and states this explicitly, so the diff and the plan agree. The underlying gap this
  finding surfaced — a spec-stage approval that never made it into a durable artifact — is fixed;
  whether that's sufficient, or whether it should have blocked continuing to build in the first
  place, is still the maintainer's call, left to `## Decision` below rather than resolved here.

## Compliance pass

Checked item by item against `plan.md` § Proof and `spec.md` § Acceptance criteria; every item is
discharged by a named test, all in `tests/unit/test_conversation.py`:

- prompt_sha256-at-creation invariant → `test_create_conversation_prompt_sha256_matches_turn_prompt_hash` (:930)
- catalog changes the hash → `test_create_conversation_catalog_changes_the_hash` (:936)
- `defer_invalidation` returns a new value, original untouched → `test_defer_invalidation_returns_new_value_original_untouched` (:947)
- a deferred change is invisible to an already-created `Conversation` → `test_defer_invalidation_is_invisible_to_an_already_created_conversation` (:958)
- promotion on the next `create_conversation`, `pending_recipe` cleared → `test_create_conversation_promotes_pending_recipe_and_clears_it` (:968)
- promotion persists across a further `create_conversation` with no new defer → `test_create_conversation_reuses_promoted_recipe_without_further_defer` (:979)
- `take_turn` COMPLETED path (messages, `next_turn_seq`, `iteration_budget.used`) → `test_take_turn_completed_updates_conversation` (:989)
- `take_turn` compression path (epoch 1, self-consistent hash, no `PromptDriftError` on the next call) → `test_take_turn_compression_rotates_prompt_and_stays_self_consistent` (:1015)
- two sequential `take_turn` calls share one history → `test_take_turn_twice_accumulates_one_shared_history` (:1063)
- `rotate_prompt` is the only epoch-mutating path → `test_rotate_prompt_is_the_only_epoch_mutating_path` (:1105)

All nine `spec.md` § Acceptance criteria bullets map one-for-one to the tests above; `VERIFY OK`
covers only "the suite passes," the mapping above is what actually discharges each Proof item.

`spec.md` § Rejected alternatives checked directly against the diff — none crept back in:
`Conversation` holds `template_name: TemplateName = str` only, never a `ConversationTemplate`
(`conversation.py:946`); `pending_recipe` is a single `TemplateRecipe | None`, no per-field pending
slots; both dataclasses stay frozen and every mutator (`defer_invalidation`, `create_conversation`,
`rotate_prompt`, `take_turn`) returns a new value via `replace()`/a fresh constructor, never mutates
in place; `take_turn` only ever forwards `conversation`'s own stored `system_prompt`/`prompt_sha256`,
never recomputes from a template; `rotate_prompt`'s `reason` parameter is read at the call site and
not written into any field (`del reason`, line 1013).

Five design principles, applied to this diff:

1. **Learn from the reference first.** `spec.md`'s "Learning from the reference" section audits two
   hermes files (`agent/conversation_loop.py:916`'s stale-fingerprint rebuild and
   `agent/agent_init.py:536-620`'s constructor) and declines both for stated, specific reasons rather
   than silently ignoring them. Satisfied.
2. **Reduce the number of bets.** The chosen "structural removal" design (`Conversation` holds a
   `str` name, not a live template reference) is cheaper than the guard-based alternative and makes
   requirement 4 true by construction rather than by convention — fewer bets than either alternative
   `spec.md` names. Satisfied; confirmed independently by the altitude review pass below.
3. **More plugins, not more core.** `PluginCatalogEntry`/`TemplateRecipe` are plain data with no
   loader, no registry, no execution path — matches the stated Non-goal. No plugin seam invented
   ahead of a loader; no behavior misplaced in core that belongs in a plugin. Satisfied.
4. **Catch the scenario at the least step-cost.** The altitude review pass (below) confirms
   `take_turn`'s compression-branch check is the correct seam, not a special case bolted onto C7 —
   `run_turn` (C7) has no concept of `Conversation`/`prompt_epoch`, and C7's own spec explicitly
   scopes it as not to be touched here. Satisfied.
5. **Minimise mutable state.** Both new dataclasses are frozen; nothing here introduces a mutable
   counter or an object mutated in place. Satisfied.

## Second opinion

Both commands ran after the passes above were written down.

**`/ponytail-review`** (run directly, terminal-review style rather than the diff-scan launcher):
one real candidate — `PromptRotationReason` (an `Enum` with exactly one member) threaded through
`rotate_prompt` as a `reason` parameter that is accepted and then `del`-ed, never stored or read.
Read as pure yagni in isolation. **Resolved, not a finding**: `spec.md`'s Design section and
Rejected alternatives (lines 172-176, 417-424) explicitly anticipated this exact shape and tied it
to `docs/reference/conversation_block_blueprint.md`'s own named property ("the reason is an enum
with one member so adding a second is a visible diff") — the spec already argued for keeping the
parameter without storing it, for a stated reason. Spec held.

**`/simplify`** (4 parallel passes: reuse, simplification, efficiency, altitude):

- **Reuse pass**: no reinvention found. `create_conversation`/`rotate_prompt` correctly reuse
  `build_surface`/`turn_prompt_hash` rather than re-deriving them; `take_turn` delegates the entire
  turn to the existing `run_turn` rather than duplicating loop/dispatch logic; tests reuse the
  file's existing `_spec`/`_fake_dispatch_ok`/`_fake_compress_none`/`_text_response` fixtures.
  Confirms my own compliance-pass reading; nothing to add.
- **Simplification pass**: independently found the same `PromptRotationReason`/`del reason` point
  (resolved above) plus three smaller candidates, triaged below.
- **Efficiency pass**: no real waste found; flagged the same minor `replace()` allocation as the
  simplification pass (below). No I/O, one `await` per turn, no closures captured.
- **Altitude pass**: checked the two real depth questions this diff raises — (a) whether
  `Conversation` holding only `template_name` is the right depth for making a deferred change
  provably invisible, versus a shallower guard in `take_turn`, and (b) whether `take_turn`'s
  compression-branch check belongs here or should have been pushed into `run_turn` (C7) itself.
  Both resolve in favor of the diff as written, for the reasons in design principles 2 and 4 above.
  No finding.

### Nits

- [Simplification] `conversation.py:961` and `:987` both implement the identical "`\n\n`-join,
  skipping empty parts" idiom independently (`_render_context`'s catalog/message join, then
  `create_conversation`'s stable-prompt/context join). Two call sites, ~3 lines each; a
  `_join_nonempty(*parts)` helper would remove the duplication. Cosmetic only, confirmed by both
  the reuse and simplification passes.
- [Efficiency] `create_conversation` (`conversation.py:982-983`) always calls `replace(template,
  recipe=recipe, pending_recipe=None)`, allocating a new value-equal `ConversationTemplate` even on
  the common path where `pending_recipe` is already `None`. Harmless (frozen dataclass, small
  object) but avoidable with an `if template.pending_recipe is not None:` guard. Raised
  independently by the simplification, efficiency, and altitude passes — converging signal, still
  cosmetic.
- [Simplification] The six `take_turn(...)` call sites in `tests/unit/test_conversation.py` (e.g.
  :1003, :1035, :1052, :1071, :1082, :1107) each repeat the identical
  `provider="p", model="m", dispatch=_fake_dispatch_ok, compress=_fake_compress_none, now=0.0`
  block. The file already solved this shape once for `run_turn` with a `_run(surface, **overrides)`
  helper (`tests/unit/test_conversation.py:544-562`); the new tests don't reuse that pattern for
  `take_turn`. Real, but purely a test-file maintainability nit — does not affect what's covered or
  `make verify`'s result.

### Raised, not findings

- `/simplify`'s simplification pass also flagged `Conversation.template_name` as currently
  write-only (set at `conversation.py:992`, never read back anywhere in this diff). Noted for
  visibility only — it's the natural key CLAUDE.md's "use names, not pointers" rule (and this
  diff's own proposed amendment) calls for, meant for a future CONV-08 store to look conversations
  up by; not a defect in this work item.

## Decision

Approved by Adam, 2026-09-04, with the Important compliance finding
resolved in this branch (plan.md now lists CLAUDE.md under Files that
change and records that the amendment was approved in conversation before
the build stage began).
