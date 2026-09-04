# Spec: One real conversation proves every promise this backbone made

Intent: docs/tasks/CONV-09-deploy-evidence/intent.md

## Requirements

1. A standalone script, not part of `make test`, drives one `Conversation`
   through four `take_turn()` calls against a real OpenRouter model. (Intent
   §Proposed outcome, §Constraints.)
2. Turn 1 is a plain exchange with no plugin involved. Turn 2 invokes a
   3-step fixture plugin ("plugin-a") whose entry tool spawns a child
   conversation partway through. Turn 3 invokes a second, minimal fixture
   plugin ("plugin-b") whose entry tool is nothing but a child spawn. Both
   plugins are mounted on the same conversation at once. (Intent §Proposed
   outcome; blueprint §1.2.)
3. The script asserts, in code, that the conversation's system-prompt hash
   is identical after turns 1, 2 and 3 — not merely eyeballed from printed
   output. (Intent §Proposed outcome — "shown to be identical, byte for
   byte"; blueprint §1.2.)
4. The script asserts, in code, that the child conversation spawned by
   plugin-a's entry tool has its own key, its own message history, its own
   iteration budget, and a tool surface that provably excludes at least one
   tool the parent conversation has — not just a smaller budget number, a
   structurally verified absence. (Intent §Proposed outcome — "provably
   unable to do anything beyond the narrow slice.")
5. The script sizes the parent conversation's iteration budget so that a
   fourth turn — attempted deliberately, asking nothing new of the plugins
   — exhausts it, and asserts the fourth turn's `ExitReason` is
   `BUDGET_EXHAUSTED`. (Intent §Proposed outcome; blueprint §1.2.)
6. After the fourth turn, the script asserts the conversation's full message
   history is well-formed: no tool call without exactly one paired result.
   (Intent §Proposed outcome — "none are left dangling"; blueprint §1.2.)
7. Every assertion above prints a clearly labelled pass/fail line as it
   runs, so the script's full stdout is the one paste-able piece of
   evidence a reviewing person reads start to finish — nothing about the
   proof depends on a reader re-deriving a claim from raw output by eye.
   (Intent §Proposed outcome, last sentence.)
8. The two fixture plugins are hand-written Python satisfying only the
   `ToolSpec` and `ChildSpec` shapes those two blocks already define —
   nothing here builds a plugin loader, a manifest format, or a real DAG
   engine. (Intent §Constraints; blueprint §7 item 9.)
9. The script calls exactly one real model: `openrouter` /
   `deepseek/deepseek-v4-flash-0731`, the same pairing
   `scripts/prove_model_access.py` already established as this project's
   real-round-trip default. (Intent §Constraints.)

## Design

Nothing in `src/` changes. Every function this script calls —
`create_conversation`, `take_turn`, `run_child`, `build_surface`,
`filter_surface`, `pending_tool_call_ids`, `turn_prompt_hash` — already
exists, closed, from C7 through C9. This work item is pure wiring: the
first time all of it runs together, against a real model, in one place.
That absence of new production code is itself notable enough to call out
explicitly rather than let a reader wonder whether something was missed.

### Files

```
scripts/prove_conversation_e2e.py                          (new)
tests/fixtures/plugins/plugin-a/skills/plugin-a-skill/SKILL.md   (new)
tests/fixtures/plugins/plugin-b/skills/plugin-b-skill/SKILL.md   (new)
```

`scripts/prove_conversation_e2e.py` follows `scripts/prove_model_access.py`'s
established shape: a `sys.path` shim to import `sadana` without an install,
a module-level `MODEL` constant, small `async def` scenario functions, one
`if __name__ == "__main__":` guard that checks `OPENROUTER_API_KEY` before
spending anything.

The two `SKILL.md` files are the only thing on disk `load_skill()` actually
reads (`conversation.py:1137`, via `_skill_path()`'s fixed
`<plugins_root>/<plugin>/skills/<skill>/SKILL.md` layout, `conversation.py:1104`).
The script points `SADANA_PLUGINS_DIR` at `tests/fixtures/plugins` before
either plugin's tool is ever invoked. Nothing else about a plugin — its
catalog entry, its entry tool's schema, its dispatch logic — is read from
disk; those are hand-written directly in the script, matching requirement 8
and the blueprint's own Open Question 1 recommendation (a manifest file
format is the PLUGINS block's job, not this one's).

### The two fixture plugins

**plugin-a**, three steps, all inside one tool's dispatch handler (the
blueprint's "3-node DAG" — simulated as sequential Python, since no DAG
engine exists to run real nodes):

1. *webhook-ish input node* — synthesizes a fixed input string standing in
   for data that would, in a real system, have arrived over a webhook. No
   real webhook exists; this is a literal stand-in, not a partial
   implementation of one.
2. *subagent-with-skill node* — calls `run_child()` with that input, a
   `ChildSpec` naming plugin-a's skill, and `tools=frozenset()` — the child
   is handed zero tools, the plainest possible version of "a subset of the
   parent's surface." The child's own `IterationBudget` is passed
   explicitly and kept small (a handful of iterations), distinct from the
   parent's.
3. *branch node* — inspects the child's `final_text` for a fixed marker
   the fixture skill is instructed to always include, and returns one of
   two different tool-result strings depending on whether it's present.
   The branch always has a defined outcome either way; there is no third,
   unhandled case.

**plugin-b**, the degenerate case: its entry tool's dispatch handler is
nothing but `run_child()` with plugin-b's own skill and its own
(different) fixed tool subset, and returns the child's `final_text`
directly as the tool result — exactly the shape blueprint §5.4 describes
for "one skill, one node."

Both entry tools are plain `ToolSpec`s built directly in the script
(`key`/`name` = `plugin_a_entry` / `plugin_b_entry` — underscores, not the
blueprint's dotted `<plugin>.<tool>` convention, because nothing in this
codebase's actual `ToolSpec`/`build_surface` enforces or expects a
namespace-prefixed name; `conversation.py:150-176`'s own docstrings never
mention one). `PluginCatalogEntry` for each is included in the
`TemplateRecipe` so the parent's system prompt names both plugins in its
context tier (`conversation.py:959-963`), matching how a real, running
conversation would actually present a mounted plugin to the model.

### Turn sequence and prompts

| Turn | User input | Expected model behaviour |
| ---- | ---------- | ------------------------- |
| 1 | Plain instruction, no tool mentioned (e.g. "Reply with a short one-sentence greeting and nothing else.") | No tool call; `ExitReason.COMPLETED`. |
| 2 | Direct instruction naming the tool (e.g. "Call the plugin_a_entry tool now.") | One tool call to `plugin_a_entry`; `ExitReason.COMPLETED`. |
| 3 | Same shape, naming `plugin_b_entry`. | One tool call to `plugin_b_entry`; `ExitReason.COMPLETED`. |
| 4 | Same shape as turn 1 (content is irrelevant — the budget check happens before any model call). | `ExitReason.BUDGET_EXHAUSTED`. |

Turn prompts name the tool directly rather than leaving tool selection to
the model's own judgement. This is a prompt-engineering choice, not a
correctness workaround: intent §Constraints already draws the line between
"the proof asks the model to do something and it doesn't reliably comply"
(worth recording plainly) and steering what's asked in the first place
(ordinary scenario design). A model asked to pick between two plugins on
its own is a different, harder proof than "does the wiring work when the
model does what it's told" — the latter is what this work item's
requirements actually ask for.

### Sizing the iteration budget

`IterationBudget` is per-conversation, not per-turn (C8's own design), so
it accumulates naturally across turns 1-3 without the script doing
anything special. A turn with no tool call costs one `MODEL_CALL`
iteration; a turn with exactly one tool round costs two (one `MODEL_CALL`
that returns the tool call, one more that returns the final text after
the tool result is appended). Expected cost: turn 1 → 1, turn 2 → 2, turn
3 → 2, total 5. The script builds the parent's `IterationBudget` with
`max_total=5` — enough for the expected path through turns 1-3, nothing
left over for turn 4's own `MODEL_CALL`, so `consume_iteration()`
(`conversation.py:256`) returns `None` at the very start of turn 4's loop
and `run_turn` exits `BUDGET_EXHAUSTED` before spending anything on that
turn.

This number is a real-world calibration, not a derived constant — CLAUDE.md's
own point about hardware needing a tuning knob a minimal model can't see
applies here to a real model's real behaviour too. If turns 1-3 cost more
than 5 in an actual run (the model takes an extra round trip somewhere),
`max_total` is raised to match what was actually observed and the script
is re-run; the target is "turn 4 provably can't proceed," not "exactly 5."
Documented here so a future reader knows 5 is a tuned value, not a
structural constant to preserve.

### Assertions

Every requirement 3-6 claim is a plain Python `assert` with a message
naming what failed, immediately after the relevant `take_turn()`/`run_child()`
call — not deferred to the end, so a failure points at the turn that
caused it:

- Requirement 3: capture `conversation.prompt_sha256` once after
  `create_conversation()`; assert it's unchanged after turns 1, 2, 3.
- Requirement 4: assert the child `Conversation` returned by `run_child()`
  has a `key` distinct from the parent's, a non-empty `messages` tuple
  (shared between both fixture plugins' dispatch branches as one small
  `_assert_child_isolated()` helper, since both need exactly these two
  checks), and — plugin-a's branch only — `tool_surface == ()`. Plugin-a's
  child is given `tools=frozenset()`, so an empty tuple is the strongest
  possible "excludes every tool the parent has" proof already, checked
  directly rather than inferred from a smaller budget number; a
  name-exclusion check over an already-known-empty tuple would only
  restate that, so only the one assertion is made.
- Requirement 5: assert turn 4's `TurnResult.exit_reason ==
  ExitReason.BUDGET_EXHAUSTED`.
- Requirement 6: assert `pending_tool_call_ids(final_messages) ==
  frozenset()` (`conversation.py:70`, already exists, reused rather than
  re-implemented) on the parent conversation's final message history.

## Interface

Not a library — nothing imports this script. Its contract is behavioural:

- Run with `OPENROUTER_API_KEY` unset → prints an error to stderr, exits 1,
  makes zero network calls (same posture as `prove_model_access.py`'s own
  guard).
- Run with a valid key → prints a labelled section per turn and per
  assertion, in order; exits 0 only if every `assert` passed; a failed
  `assert` raises `AssertionError`, which propagates and exits non-zero
  with Python's own traceback — no custom error handling wraps it, since a
  proof script's job is to fail loudly, not to degrade gracefully.
- All output goes to stdout except the missing-key error; the whole run's
  stdout is what gets pasted into `review.md § Evidence`.

## Acceptance criteria

- [ ] Running the script with `OPENROUTER_API_KEY` unset exits non-zero
      before any network call.
- [ ] Running the script for real completes turns 1-3 with
      `ExitReason.COMPLETED` each time.
- [ ] The script's own assertion confirms `prompt_sha256` is identical
      after turns 1, 2 and 3.
- [ ] The script's own assertion confirms plugin-a's spawned child has a
      distinct key, a non-empty message history, and a `tool_surface` that
      excludes `plugin_b_entry`.
- [ ] The script's own assertion confirms turn 4 exits
      `ExitReason.BUDGET_EXHAUSTED`.
- [ ] The script's own assertion confirms the final message history has no
      unpaired tool call.
- [ ] The full run's stdout, pasted whole, becomes `review.md § Evidence`
      for this work item.
- [ ] `make verify` still ends `VERIFY OK` (this script is never invoked by
      the suite; the fixture `SKILL.md` files are inert data, not code
      `make verify` needs to touch).

## Non-goals

- A real plugin loader, manifest format, or discovery mechanism (intent
  §Constraints; blueprint's own PLUGIN-SYSTEM block, not this one).
- A real DAG engine. Plugin-a's "3 nodes" are sequential Python inside one
  dispatch handler, not a general node-graph executor.
- Durable storage. CONV-08's sqlite store is not exercised here — nothing
  in the blueprint's §1.2 milestone or this intent asks this proof to
  survive a restart; pulling it in would be an unrelated bet this work
  item doesn't need to take.
- Automating this script into `make test` or any CI-equivalent path.
  Real, billed network calls stay opt-in and manual, matching
  `testing-conventions`' network ban and this project's one existing
  precedent (`prove_model_access.py`).
- Proving the model *reliably* chooses the right plugin unprompted. This
  proof directs tool choice explicitly (see §Design, "Turn sequence").

## Rejected alternatives

- **Mocking the provider instead of calling a real one.** This is
  precisely the thing CLAUDE.md's own rule ("prove a block's first real
  external round trip with a standalone script... rather than relaxing
  testing-conventions' network ban in the unit suite") exists to prevent
  substituting. A mocked run would prove the wiring compiles, not that any
  of this project's promises hold against something that doesn't follow
  the script.
- **A `messages`-table-style structural check for pairing**, i.e. querying
  a persisted store the way CONV-08 could. Declined: this proof's
  well-formedness check (requirement 6) is about the in-memory transcript
  `take_turn()` actually produced, and `pending_tool_call_ids()` already
  answers that directly — routing it through a database this proof
  doesn't otherwise use would be an unrelated dependency for no added
  confidence.
- **Letting the model choose which plugin to call, turn by turn**,
  matching the blueprint's more open-ended framing ("the model picks a
  plugin"). Declined for this specific proof (see §Non-goals) — it turns a
  wiring proof into a model-capability proof, a different and harder
  claim than what intent.md and blueprint §1.2's numbered milestone
  actually ask this work item to establish.
- **Prefixed tool names (`plugin_a.entry`) matching the blueprint's
  suggested `<plugin>.<tool>` convention.** Declined: nothing in the
  actual `ToolSpec`/`build_surface` implementation (C3) expects, enforces,
  or even mentions a namespace separator — introducing one here would be
  inventing a convention this proof alone would have to explain, rather
  than reusing what C3 already shipped as-is.

## Concerns

- **Real-model non-determinism is this proof's central risk, not a side
  effect of it.** Every assertion above is deterministic given the
  model's tool-calling behaviour, but that behaviour itself is not fully
  controlled — a model can, despite direct instruction, reply with prose
  instead of a tool call, or take an extra round trip. If that happens,
  the right response is to record it (per intent §Constraints) and adjust
  the prompt or the budget sizing, never to loosen an assertion until it
  passes. This is named here so a reviewer reads a failed run as
  information, not as this work item's own defect by default.
- **The iteration-budget sizing (5) is empirically tuned, not verified
  against the real model before this document was written** — this spec
  is produced before the first real run. If turns 1-3 cost more than
  expected, `max_total` needs raising during build, and that adjustment
  is exactly the kind of real-world calibration knob CLAUDE.md already
  asks for; it is not a sign the design above is wrong.
- No policy skill named `project-structure` or `reference-lookup` exists
  in this project (same gap CONV-08's spec.md already recorded).
  `testing-conventions` was checked and found not to bind this work item
  at all: this script is explicitly outside `make test`'s suite, by the
  same rule CLAUDE.md and `prove_model_access.py` already established, so
  the fixture `SKILL.md` files are plain data assets, not test code
  subject to `tests/unit/test_<module>.py` naming.
