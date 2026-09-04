# Review: A closed, single-skill sub-task can be spawned and reports back cleanly (from plan.md 2026-09-04)

Reviewed: HEAD (9181cb0)..working tree — 3 files, +588/-0
Reviewer context: same session as build (limitation noted) — this session
had no memory of writing the code (fresh Deploy-stage invocation with only
intent.md/spec.md/plan.md and the diff as input), but it is not a separate
subagent/session from whatever wrote the code, so the cold-read requirement
is only partially satisfied. Read every file top to bottom before writing
any finding, and cross-checked every claim by reading the actual diff lines
cited below rather than trusting spec.md's own prose.
Second opinion: `/ponytail-review` (run directly, this session) and
`/simplify` (its four review angles run as four parallel subagents,
reuse/simplification/efficiency/altitude) — both ran, after the sections
below were already drafted.

## Evidence

```
docs/tasks/C9-child-conversation: all present artifacts valid
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
check that scripts with shebangs are executable...........................Passed
check that executables have shebangs.......................................Passed
detect private key..........................................................Passed
ruff.........................................................................Passed
ruff-format...................................................................Passed
shellcheck.....................................................................Passed
Detect secrets.................................................................Passed
LINT OK
Success: no issues found in 4 source files
TYPES OK
▶ hermetic run: TZ=UTC LANG=C.UTF-8 PYTHONHASHSEED=0, empty env
........................................................................ [ 42%]
........................................................................ [ 84%]
..........................                                               [100%]
170 passed in 1.01s
TESTS OK
VERIFY OK
```

## Scope

`git diff --stat HEAD`:

```
 CLAUDE.md                       |   1 +
 src/sadana/conversation.py      | 241 ++++++++++++++++++++++++++++
 tests/unit/test_conversation.py | 346 ++++++++++++++++++++++++++++++++++++++++
 3 files changed, 588 insertions(+)
```

Matches `plan.md` § Files that change exactly:
`src/sadana/conversation.py` (new `# ── CONV-07: child conversation ──`
section plus one new defaulted field on `Conversation`) and
`tests/unit/test_conversation.py` (new tests under a new section, plus
updated imports). `CLAUDE.md`'s one-line addition is a general project rule
generalizing the intent-interview's "no config ceiling on a self-exhausting
budget" decision (`intent.md` § Changed during planning) into a reusable
project-wide principle — not code, and not a file `plan.md` named, but a
natural byproduct of the same decision `plan.md` § Risks (f) already checks
for; not a deviation worth flagging.

No file `plan.md` named went untouched, and no file outside that list was
touched.

## Findings

Three passes run: Bugs, Security, Compliance (against `intent.md`,
`spec.md`, `plan.md`). Then `/ponytail-review` and `/simplify` (4 angles)
as a second opinion, triaged below.

### Important

- **[Compliance] The depth-derivation caveat in `spec.md` and the code
  comment undersells its own scope.** `_conversation_depth`
  (`conversation.py:1177-1185`) counts `"child"` path segments in
  `ConversationKey`, which is a bare `str` with no format constraint
  (`ConversationKey = str`, `conversation.py:31`) and is caller-chosen at
  `create_conversation(template, key, ...)` (`conversation.py:966-968`) —
  not just something `run_child` mints. The docstring's caveat, and
  `spec.md`'s matching text ("a `node_name` that is itself exactly
  `"child"` would be miscounted by one... not worth a runtime check for a
  value nothing external ever supplies today"), only discusses
  `node_name` — a value `run_child` itself controls one segment of. It
  does not mention that a caller's own **root** key can independently
  contain a literal `"child"` segment (e.g. a project or workspace keyed
  `"org/child/finance"`), which `_conversation_depth` would count before
  any `run_child` call ever happens, silently starting a fresh top-level
  conversation at derived depth 1 instead of 0 and consuming one hop of
  `child_max_depth_from_config()`'s default-2 budget before the first
  real spawn. `test_run_child_raises_child_depth_exceeded_before_calling_provider_or_dispatch`
  (`test_conversation.py`) actually constructs exactly this shape —
  `create_conversation(_template(), "root/child/nodeA/0", ...)` — to
  simulate depth 1, which is proof the mechanism is sensitive to this,
  not just a hypothetical. The effect is fail-safe (over-counts, so a
  spawn is refused *earlier* than the configured maximum intends, never
  later — no depth-guard bypass), so this is not a security hole, but it
  is a genuine logic gap the stated caveat doesn't cover, and it is
  exactly the kind of load-bearing simplification CLAUDE.md asks to be
  named precisely ("name the step that is still load-bearing and say
  why"). Confirmed independently by this session's own compliance re-read
  of `spec.md`'s Design section against the code, then again by
  `/simplify`'s altitude-angle subagent, which found the same root cause
  from a different starting question ("is depth-from-key a sound
  mechanism, run against the actual `ConversationKey` type"). Worth a
  human decision: either broaden the caveat's own wording to name the
  real scope (root keys, not just `node_name`), or add the narrow guard
  the caveat already considered and declined (reject a key/`node_name`
  containing a literal `"child"` segment) — no code fix implied by this
  finding itself, since `intent.md` and `spec.md` both explicitly treat
  the current mechanism as correct for today's only caller (this
  project's own tests, which never construct such a key for real use).

### Nits

- **[Compliance] `child_iteration_budget_from_config`
  (`conversation.py:1188-1197`) is a line-for-line structural copy of
  the pre-existing `iteration_budget_from_config`
  (`conversation.py:265-273`)** — same read-env-int → negative-check →
  build-`IterationBudget` shape, differing only in env var name and
  default. Confirmed independently by this session's own
  `/ponytail-review`-style pass and by `/simplify`'s simplification-angle
  subagent. A shared `_budget_from_config(env_var, default)` helper would
  remove the duplicate negative-value contract (currently two places to
  keep the error-message wording and `< 0` check in sync) — a real but
  small reduction, not a design-principle violation (`iteration_budget_from_config`
  predates this work item, so this is one new instance of an existing
  pattern, not a new one). Not required before merge.
- **[Bugs] `load_skill` (`conversation.py:1144-1146`) calls
  `path.exists()` then `path.read_text()`** — two filesystem round-trips
  (`stat` + open/read) where `try: path.read_text() except
  FileNotFoundError` would do it in one, and would also catch the same
  race a separate `exists()` check cannot (file removed between the two
  calls). No test exercises that race and none needs to — `spec.md`'s own
  acceptance criteria only require the missing-file case to raise
  `SkillLoadError`, which the current code already does correctly; this
  is a style/efficiency nit, not a correctness gap.

### Raised, not findings

- `/simplify`'s efficiency-angle subagent suggested `functools.lru_cache`
  on `load_skill` to avoid re-reading/re-parsing the same `SKILL.md` for
  repeated spawns of the same skill. This changes behavior (staleness
  semantics — a cached body would survive a plugin update mid-process)
  and adds new module-level mutable state `spec.md` never asked for;
  belongs in a future work item once a caller that actually spawns many
  children of the same skill exists, not in this review.
- The same subagent suggested wrapping `load_skill`'s blocking read in
  `asyncio.to_thread` so concurrent `run_child` calls (e.g. via
  `asyncio.gather`) don't stall the event loop on file I/O. No caller in
  this codebase spawns children concurrently today — `intent.md` and
  `spec.md` both scope the orchestration engine that would do so as out
  of scope for this work item. A candidate for that future work item's
  `intent.md`, not this one.
- `/simplify`'s simplification-angle subagent flagged `_plugins_root()`
  and `_skill_path()` (`conversation.py:1091-1109`) as two single-caller
  helpers that could fold into one. `spec.md`'s Design section already
  discusses and justifies exactly this split ("Only what populates
  `_plugins_root()` needs to change when a real installer exists; this
  function and every caller of it don't.") — spec held, not a finding.
- `/simplify`'s altitude-angle subagent noted that `run_turn`'s existing
  valid/invalid tool-call partition (`conversation.py:787-802`, unchanged
  by this diff) only sets `ExitReason.INVALID_TOOL_CALLS` when a batch has
  *zero* valid calls — a mixed batch (one allowed tool, one blocked tool)
  continues the turn rather than exiting, and `test_run_child_restricts_tool_surface_to_spec_tools`
  only exercises the all-invalid case for a child. Containment itself
  still holds (`dispatch` never receives a name outside `spec.tools`,
  confirmed by reading `valid_names`'s derivation from `run_child`'s own
  filtered `tool_surface`), so this is a coverage gap in `run_turn`
  (C7's existing code), not a `run_child` defect. Not a finding for this
  diff.

### Compliance pass, checked and found clean

- **`spec.md` § Acceptance criteria (all 11)**: every item verified
  against a specifically matching test —
  `test_run_child_key_embeds_parent_and_node_name`,
  `test_run_child_two_spawns_produce_distinct_sequential_keys`,
  `test_run_child_restricts_tool_surface_to_spec_tools`,
  `test_run_child_uses_given_budget_verbatim_even_over_config_default`,
  `test_run_child_uses_config_default_budget_when_none_given`,
  `test_run_child_wall_clock_budget_is_the_same_object_as_parent` (uses
  `is`, not `==`, matching the acceptance criterion's own wording),
  `test_run_child_uses_given_model_over_parents`,
  `test_run_child_inherits_parents_model_when_none_given`,
  `test_run_child_raises_child_depth_exceeded_before_calling_provider_or_dispatch`,
  `test_run_child_never_mutates_parent_messages_only_bumps_next_child_seq`,
  `test_load_skill_*` (7 cases covering valid/missing-file/missing-
  delimiter/unclosed/name-mismatch/missing-description/too-long-
  description), `test_run_child_system_prompt_is_byte_identical_regardless_of_input`.
- **`plan.md` § Proof**: `make verify` output above ends `VERIFY OK` with
  170 tests passing, including the 27 new cases named in `plan.md`'s
  Order of work (7 `load_skill`, 3 depth/key, 6 config-default, 11
  `run_child` acceptance-criteria tests) — all present and green.
- **`spec.md` § Rejected alternatives re-check**: `SkillRef` stays
  symbolic (`plugin: str`, `skill: str`, `conversation.py:1078-1085`) —
  no `Path` parameter anywhere in its interface. No config value clamps
  `spec.budget` — `run_child` uses `spec.budget if spec.budget is not
  None else child_iteration_budget_from_config()` verbatim
  (`conversation.py:1295`), confirmed by
  `test_run_child_uses_given_budget_verbatim_even_over_config_default`.
  `spec.input` reaches only `take_turn`'s `user_input` parameter
  (`conversation.py:1301`), never `_CHILD_TASK_FRAMING` or
  `system_prompt`'s assembly — confirmed by
  `test_run_child_system_prompt_is_byte_identical_regardless_of_input`.
  `ChildResult` stays a bare `TurnResult` alias
  (`conversation.py:1230-1232`), no new dataclass. No `depth` field added
  to `Conversation` — only `_conversation_depth` derives it (see Important
  finding above for the one gap in this decision). `dispatch` is passed
  through unwrapped in `run_child`'s call to `take_turn`
  (`conversation.py:1298`) — no tool-name re-check layered around it.
- **`plan.md` § Risks drift checklist (a-f)**: re-ran all six directly
  against the diff — all hold, matching the Rejected-alternatives
  re-check above plus confirming `run_turn`/`take_turn` themselves are
  byte-for-byte unmodified (only called, never edited).
- **Design principles**: (1) reference corpus — `spec.md`'s Design section
  explicitly audits `tools/delegate_tool.py` and `_build_child_system_prompt`,
  adopting the fresh-agent/own-budget/derived-depth shape and declining
  the task-in-system-prompt shape with a stated reason; not silently
  ignored. (2) reduce bets — the no-config-ceiling and symbolic-`SkillRef`
  decisions are both the *fewer*-bets choice (avoids inventing a second
  cap or a filesystem-coupled type before either is needed), and both are
  explicitly sanctioned in `intent.md`/`spec.md`, not smuggled in. (3)
  more plugins not more core — this work item is itself infrastructure a
  future plugin engine will call, not a plugin itself; `spec.md`'s
  Non-goals section explicitly keeps the orchestration engine and
  plugin-install machinery out, so no premature plugin seam was invented.
  (4) least step-cost — tool restriction reuses `run_turn`'s existing
  valid/invalid partition rather than adding a second check
  (`conversation.py:787-802` unchanged); no new step added to the common
  turn-loop path. (5) minimal mutable state — `next_child_seq` is one
  monotonic `int` field advanced through `dataclasses.replace` (never
  mutated in place, confirmed at `conversation.py:1278`), matching
  `next_turn_seq`'s existing shape; `SkillRef`, `ChildSpec`, and the new
  exceptions are all frozen/stateless. All five clean.
- **Security pass**: `load_skill` reads only a path built from
  `SADANA_PLUGINS_DIR`/`ref.plugin`/`ref.skill` — both `SkillRef` fields
  are caller-supplied strings joined directly into a `Path` with no
  sanitization of `..` segments, but the only caller today is this
  project's own tests with fixture-controlled `SkillRef` values (no
  externally-facing input reaches this yet, per `intent.md`'s explicit
  "nobody outside the repo depends on this yet" and "building the
  orchestration engine... is out of scope"). Worth a caller-side
  validation once a real plugin catalog exists, but not a finding against
  *this* work item's stated scope. No secrets, network, or subprocess
  surface added.

## Decision

Approved by Adam, 2026-09-04. The Important finding (the depth-derivation
caveat's scope) was not required as a merge condition — the effect is
fail-safe and no code fix was implied by the finding itself; left as a
known, documented gap rather than fixed in this branch.
