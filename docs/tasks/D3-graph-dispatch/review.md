# Review: A plugin's graph actually runs (from plan.md 2026-09-07)

Reviewed: HEAD..working tree (all changes are uncommitted; there is no
`<base>..HEAD` range yet) — 14 files, +1909/-4
Reviewer context: fresh session, no memory of writing this code — spawned
solely to run the Deploy stage cold, per deploy-skill §1.
Second opinion: none — ran during build (self-check, `/ponytail-review` +
`/simplify` per plan.md's own final step), not repeated here by design.

## Evidence

Re-run after the cycle-detection fix below (was 316 tests at first review;
now 318):

```
$ make verify
docs/tasks/D3-graph-dispatch: all present artifacts valid
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
ruff-format..............................................................Passed
shellcheck...............................................................Passed
Detect secrets...........................................................Passed
docs/reference/ citations resolve to tracked files.......................Passed
LINT OK
Success: no issues found in 11 source files
TYPES OK
▶ hermetic run: TZ=UTC LANG=C.UTF-8 PYTHONHASHSEED=0, empty env
........................................................................ [ 22%]
........................................................................ [ 45%]
........................................................................ [ 67%]
........................................................................ [ 90%]
..............................                                          [100%]
318 passed in 2.68s
TESTS OK
VERIFY OK
```

Cross-cutting proof grep (plan.md's own closing evidence item):

```
$ grep -n "^from sadana\|^import sadana" src/sadana/plugins.py src/sadana/plugin_manifest.py
src/sadana/plugin_manifest.py:20:from sadana import plugins
src/sadana/plugins.py:24:from sadana import config
```

Neither imports `conversation`.

**Requirement 9's real-model evidence** (`scripts/prove_plugin_dispatch_e2e.py`),
re-run live after the cycle-detection fix, using the real `OPENROUTER_API_KEY`
in `.env`:

```
discover_plugins() found: ['plugin-c']
catalog entry_tools: ['plugin_c_entry']

=== turn 1: call plugin_c_entry via the real dispatch ===
exit_reason=ExitReason.COMPLETED final_text='The plugin acknowledged the note "hello from D3" and confirmed with ACKNOWLEDGED.'
[ok] turn 1 completed via a real dispatch()-built walk, not a hand-written closure
[ok] next_child_seq correctly advanced to 1

=== turn 2: call plugin_c_entry again, proving the tracker's reconciliation contract ===
exit_reason=ExitReason.COMPLETED final_text='The plugin acknowledged the second call with "ACKNOWLEDGED".'
[ok] next_child_seq correctly reached 2 — no child-key collision
[ok] final transcript is well-formed: no dangling tool calls

ALL ASSERTIONS PASSED
```

Confirms `discover_plugins()` → `build_plugin_set()` → `build_dispatch()` →
`take_turn_and_reconcile()` against `plugin-c`'s one-node `ask` entry,
`ExitReason.COMPLETED` both times, `next_child_seq` reaching 1 then 2
(the `ChildSeqTracker` reconciliation contract holding across two separate
dispatch closures built from the same reconciled `Conversation`), and no
dangling tool calls in the final transcript — matching spec.md
Requirement 9 and plan.md's Proof section claim for R9. Also confirms the
acyclicity check added below does not disturb `plugin-c`'s own (acyclic)
graph.

## Findings

Three passes run (bugs, security, compliance) cold, plus the
files-touched-vs-plan.md comparison. One Important bug (an unbounded
`run_graph` walk) and one Important compliance scope mismatch (`CLAUDE.md`
touched but not named in plan.md) surfaced; no nits are worth listing
separately. The compliance pass below discharges every plan.md Proof item,
every spec.md Acceptance criterion, and spec.md's Rejected alternatives
against the actual diff.

### Important

- **[Bugs] — Fixed.** `run_graph` (`src/sadana/plugin_manifest.py`, the
  `while True` loop) had no bound on the number of nodes it will visit, and
  D2's `validate()` (`src/sadana/plugin_manifest.py:134-186`, specifically
  `plugins._first_unreachable`) only proved every node is *reachable* from
  an entry's `start` — it never proved the graph is acyclic despite being
  called a DAG throughout the design material. A plugin whose `route` body
  sends execution back to an earlier node made `run_graph` loop forever:
  synchronously (blocking the whole event loop) if the cycle is
  `compute`/`route` only, or as an unbounded stream of real `run_child`
  calls if the cycle touches an `ask` node (sibling spawns under one
  parent never trip `ChildDepthExceeded`).

  Fixed at the root, not with a ceiling on the walk: `validate()` gains an
  eighth check, acyclicity (`plugins.CyclicGraph`, `plugins._first_cycle`
  — a DFS with a recursion-stack set, same shape as `_first_unreachable`),
  run right after reachability. Once a `Manifest` that reaches `Valid` is
  guaranteed acyclic, `run_graph`'s own walk is bounded by construction (at
  most `len(manifest.nodes)` steps, since no node can repeat) — no separate
  runtime ceiling needed, which also avoids double-guarding a scenario a
  structural check now already closes (CLAUDE.md's own rule against that).
  `run_graph`'s docstring now states this trust explicitly, the same way
  it already stated trust in reachability.

  New tests: `test_cyclic_graph_carries_a_node_on_the_cycle`
  (`tests/unit/test_plugins.py`) and
  `test_validate_returns_cyclic_graph_for_a_route_back_edge`
  (`tests/unit/test_plugin_manifest.py`, a `route` node pointed back at its
  own predecessor via `_VALID_TOML.replace(...)`). `make verify` re-run
  clean (318 tests, was 316). `scripts/prove_plugin_dispatch_e2e.py`
  re-run live against a real model after the fix — `ALL ASSERTIONS PASSED`,
  confirming the acyclic `plugin-c` fixture is unaffected.

- **[Compliance] — Accepted, no change.** `CLAUDE.md` is modified in this
  diff (+2 lines: the `plugins.py`/`plugin_manifest.py` import-direction
  rule and the predecessor-only node-input rule) but is not listed in
  plan.md's `## Files that change`. The content itself was never a
  surprise — spec.md's Design section already said both rules are "now
  also in CLAUDE.md" — and the person deciding this review has confirmed
  the omission is fine as-is. No plan.md edit made; recorded here so the
  decision is traceable rather than silent.

### Nits

*(none worth listing separately from the Important items above — the diff
is otherwise tight; padding this section would only dilute the two
findings that matter)*

## Compliance pass detail

Checked item by item, not just green-lit:

- **plan.md `## Proof`**: R1 → `tests/unit/test_plugins.py`'s
  `build_plugin_set` tests + the duplicate-tool test (asserts
  `build_plugin_set` itself raises nothing, and `build_surface` raises
  `DuplicateToolError`). R2 → `tests/unit/test_plugin_manifest.py`'s four
  `discover_plugins` tests (valid-only, non-directory skip, missing root,
  config-default root). R3/R4 → the `run_graph` happy-path tests per node
  kind plus `trace`-ordering assertions in the same file. R5 → the
  raising-body test (asserts neither the exception message nor
  `"Traceback"` appears in `result.text`), the undeclared-port test, and
  the `ask`-returns-`None` test. R6 → the three-node compute-chain test,
  where each stub function asserts its own predecessor value and nothing
  else. R7 → `test_build_dispatch_unknown_tool_returns_a_dag_result_not_an_exception`
  in `tests/unit/test_plugin_dispatch.py`. R8 → the three
  `call`/`each`/`wait`-refused tests. Cross-cutting: the import grep above
  (clean); `git diff --stat` confirms `scripts/prove_conversation_e2e.py`
  and `tests/fixtures/plugins/plugin-a`/`plugin-b` do not appear; `mypy
  src` and `make verify` both pass (Evidence above). All Proof items are
  discharged.
- **spec.md `## Acceptance criteria`**: walked every checkbox against the
  diff — all fourteen are satisfied by name (the same tests cited above),
  except the CLAUDE.md scope point noted as an Important finding, which is
  a plan.md-vs-diff mismatch rather than an unmet acceptance criterion (no
  criterion mentions CLAUDE.md).
- **spec.md `## Rejected alternatives`**: checked each against the diff,
  not just read. Hermes's stateful plugin registry — not present; `plugins.py`
  and `plugin_manifest.py` stay free of any process-global registry.
  Whole-run-state node inputs — `run_graph` threads exactly one `value`
  variable, reassigned each iteration; the compute-chain test would fail
  on an accidental "pass everything" implementation. A `DagResult`/
  `run_turn`/`take_turn` spawn-count field — `conversation.py` does not
  appear in the diff at all, and `DagResult`'s fields are unchanged from
  before this item (checked in `src/sadana/plugins.py`). A second
  duplicate-tool check inside `build_plugin_set` — confirmed absent, and
  the dedicated test proves `build_plugin_set` raises nothing on a
  collision. Reusing `conversation.DuplicateToolError` from `plugins.py` —
  not reachable given the import direction (confirmed by the grep above);
  not attempted.
- **Design principle 1 (learn from the reference first)**: spec.md's
  Guideline 1 documents six specific hermes files read, what was adopted
  (raise-at-registration-time posture, already precedented by
  `DuplicateToolError`) and what was declined with a stated reason
  (the mutable global registry and load-order resolver, `delegate_tool.py`'s
  swallowed-exception failure shape). No hermes file is quietly
  reinvented without comment.
- **Design principle 2 (reduce the number of bets)**: the two rejected,
  more-expensive alternatives (threading a spawn-count through
  `run_turn`/`take_turn`, and whole-run-state node inputs) are both
  correctly avoided in the diff, per the Rejected-alternatives check
  above. `call`/`each`/`wait` are refused rather than designed around,
  keeping the door open without committing to their shape yet — consistent
  with the guideline, though see the cycle finding above for a place
  where "no ceiling" was itself an implicit bet that wasn't named as one.
- **Design principle 3 (more plugins, not more core)**: `plugin_dispatch.py`
  is a new, narrowly-scoped module rather than logic added to
  `conversation.py`; `plugins.py`/`plugin_manifest.py` stay leaf modules.
  No plugin seam is invented before anything can load it — `discover_plugins`
  is the first and only loader, and it is exercised by both the unit
  suite and the standalone script.
- **Design principle 4 (least step-cost)**: the duplicate-tool handling
  reuses `build_surface`'s existing check rather than adding a new one —
  confirmed by the dedicated test. The `ChildSeqTracker` is the smaller of
  the two available moves on `dispatch_closure_state_bug.md`'s open gap,
  argued directly in spec.md Guideline 3 and not contradicted by the diff
  (`conversation.py` untouched).
- **Design principle 5 (minimise mutable state)**: `InstalledPlugin` and
  `PluginSet` are frozen and rebuilt from scratch each time, per spec.md
  Guideline 4. `ChildSeqTracker.next_seq` is the one mutable cell in the
  whole diff, and its justification (Guideline 3) and failure mode if a
  caller skips reconciliation (Concerns — surfaces loudly as
  `ConversationAlreadyExists`, not silent data loss) are both stated
  explicitly rather than left implicit. `take_turn_and_reconcile` closes
  the one place that obligation was previously enforced only by a
  docstring, exactly as the Concerns section describes.

## Decision

Approved by Adam, 2026-09-07, with both Important findings resolved in
this branch before merge: the unbounded `run_graph` cycle fixed at the
root (`validate()`'s new acyclicity check), and the `CLAUDE.md` scope gap
explicitly waived — its content was already disclosed in spec.md, and no
plan.md edit was requested.
