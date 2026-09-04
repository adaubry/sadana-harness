# Plan: A closed, single-skill sub-task can be spawned and reports back cleanly (from intent.md 2026-09-04)

## Context

`spec.md` is approved and already pins every type, function signature, and
line of the core algorithm — this plan is about landing that code in a
safe order with proof at each step, not re-deriving the design.

Prior art checked against `docs/reference/hermes_core_blocks_kind.csv`
(DELEGATION block, 15 files): `tools/delegate_tool.py` is already fully
audited in `spec.md`'s Design section (adopted the fresh-agent-with-own-
budget shape, declined embedding the task in the system prompt). One more
check done here: `agent/subagent_lifecycle.py:65-75,251` stores a child's
`depth` as an explicit field on a `SubagentHandle`, read off a mutable
`_delegate_depth` attribute set elsewhere on the agent object — this
confirms `spec.md`'s decision to derive depth from the conversation key
instead is a deliberate departure from hermes's pattern, not an oversight
of it (hermes's approach is exactly the mutable-attribute coupling
blueprint §2.3 already named as the reason not to copy `agent_init.py`
wholesale).

## Files that change

- `src/sadana/conversation.py` (existing, 1068 lines) — add a new
  `# ── CONV-07: child conversation ──` section at the end, plus one new
  field on the existing `Conversation` dataclass.
- `tests/unit/test_conversation.py` (existing, 1117 lines) — add tests
  under a new section, reusing existing fixtures (`_template`, `_budget`,
  `_fake_dispatch_ok`, `_fake_compress_none`, `_text_response`,
  `monkeypatch.setattr(model_access, "send", ...)`) rather than inventing
  new ones.

No new files. `spec.md`'s Design section already decided against a
package-of-modules split for this work item.

## Order of work

1. **`Conversation.next_child_seq: int = 0`.** One field, one line, added
   with a default so every existing keyword-based `Conversation`
   construction (including every C8 test) is unaffected. Proof: existing
   `test_conversation.py` C8 tests still pass unmodified — confirms the
   addition is additive, before anything else depends on it.

2. **Skill loading, standalone.** `SkillRef`, `SkillLoadError`,
   `_plugins_root`, `_skill_path`, a private frontmatter parser, and
   `load_skill`. Nothing here touches `Conversation` or `run_child` — it
   is a pure filesystem-in, string-out function. Land and test it alone
   first: valid SKILL.md, missing file, missing/malformed frontmatter,
   name mismatch, missing description, description over 1024 chars — six
   cases, each a `tmp_path`-based fixture per `testing-conventions`
   (never the real state directory), with `SADANA_PLUGINS_DIR`
   monkeypatched to the fixture root.

3. **Depth and key helpers.** `_child_key`, `_conversation_depth`,
   `ChildDepthExceeded`. Pure string functions, tested directly: depth 0
   for a bare key, depth 1 after one `/child/.../`, two children of the
   same parent get keys differing only in their trailing sequence.

4. **Child budget/depth config defaults.** `child_iteration_budget_from_config`
   (`SADANA_CONVERSATION_CHILD_MAX_ITERATIONS`, default 20) and
   `child_max_depth_from_config` (`SADANA_CONVERSATION_CHILD_MAX_DEPTH`,
   default 2), mirroring the existing `iteration_budget_from_config`
   pattern exactly (same negative-value `ValueError`). Tested the same
   way the existing C4 tests already test their counterparts —
   `monkeypatch.setenv`/`delenv`, never the real environment.

5. **`ChildSpec`, `ChildResult` (alias), `_CHILD_TASK_FRAMING`, `run_child`.**
   The integration step: wires steps 1-4 together with the already-existing
   `filter_surface` (C3), `turn_prompt_hash` (C7), and `take_turn` (C8).
   This is the riskiest step — see Risks — landed last, once every piece
   it calls has already been proven independently.

6. **Acceptance-criteria tests against `run_child` itself**, straight from
   `spec.md`'s checklist: tool restriction ends the child's turn with
   `INVALID_TOOL_CALLS` rather than reaching `dispatch`; a caller-supplied
   budget is used verbatim (no clamping against the config default even
   when it exceeds it); `spec.budget=None` falls back to the config
   default; the child's `wall_clock_budget` is the identical value
   (`is`, not just `==`) the parent had; a caller-supplied model is passed
   to `take_turn`, `None` passes the caller's own `model` through;
   exceeding `child_max_depth_from_config()` raises `ChildDepthExceeded`
   before `dispatch` or `model_access.send` is ever called; `run_child`
   never appends to `parent.messages` and the only diff between `parent`
   and the returned updated parent is `next_child_seq`; two children
   spawned from the same skill and `stable_prompt` produce byte-identical
   `system_prompt` regardless of `spec.input`.

7. **`make verify`.** Full suite, pasted as this stage's proof.

## Risks

**What could this change break?** The only existing structure this touches
is the `Conversation` dataclass (step 1) and the module's growing size
(already flagged in `spec.md` §Concerns, not re-litigated here). No test in
`test_conversation.py` uses `dataclasses.asdict`/`astuple` on `Conversation`
(checked directly — grep found none), so a defaulted new field cannot
silently break an equality or shape assertion elsewhere. `run_turn`/
`take_turn` themselves are not modified, only called — the turn loop's own
eight `ExitReason`s and their existing tests are untouched.

**Which step is riskiest?** Step 5, `run_child` — the only step composing
multiple subsystems (budget defaults, `filter_surface`, `turn_prompt_hash`,
`take_turn`) into one `async` call. Mitigation: it is deliberately landed
last, after steps 1-4 are each independently proven, and its own tests
(step 6) reuse the exact monkeypatch pattern already proven in
`test_take_turn_completed_updates_conversation` (`monkeypatch.setattr(
model_access, "send", ...)`, `_fake_dispatch_ok`, `_fake_compress_none`,
`asyncio.run(...)`) rather than a new fake-provider mechanism.

**Drift check against `spec.md`'s Rejected alternatives.** Explicit
checklist to re-run before calling step 5 done: (a) `SkillRef` stays
symbolic, never becomes a `Path` parameter anywhere in the implementation;
(b) no config value clamps a caller-supplied `spec.budget`; (c) `spec.input`
is passed to `take_turn`'s `user_input` only — never interpolated into
`_CHILD_TASK_FRAMING` or the assembled `system_prompt`; (d) no new
`ChildResult` dataclass — it stays a bare `ChildResult = TurnResult` alias;
(e) no `depth` field added to `Conversation` — only `_conversation_depth`
derives it from the key; (f) `dispatch` is passed through unwrapped, no
tool-name re-check added around it.

## Proof

`make verify` output, pasted in full, ending `VERIFY OK`, with the new
`test_conversation.py` cases visible in the test count. Specifically:
`load_skill`'s six cases (step 2), the depth/key helpers (step 3), the two
config-default functions (step 4), and every acceptance-criteria case from
`spec.md` against `run_child` (step 6) all green. No new dependency added
to `pyproject.toml` (the frontmatter parser is hand-rolled, per `spec.md`'s
Design section — zero runtime dependencies before this work item, zero
after).
