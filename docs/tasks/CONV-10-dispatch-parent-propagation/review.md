# Review: The proof script's own bookkeeping stops silently losing track of who it already spawned (from plan.md 2026-09-04)

Reviewed: `git diff HEAD` (nothing in this work item is committed yet) — 5 files, +465/-14
Reviewer context: same session as build. The actual three-pass review was delegated to a fresh subagent given no prior context beyond `git diff HEAD` and `intent.md`/`spec.md`/`plan.md` (deploy-skill §1); it independently re-traced the fix's mechanism against `src/sadana/conversation.py` (`_child_key`, `run_child`, `take_turn`'s TOOL_ROUND) and against hermes-agent's `delegate_tool.py` rather than trusting spec.md's account of either.
Second opinion: none — ran during build (self-check: `/ponytail-review` + four parallel `/simplify` passes, all clean), not repeated here by design.

## Evidence

```
$ make verify
docs/tasks/CONV-10-dispatch-parent-propagation: all present artifacts valid
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
Detect secrets............................................................Passed
LINT OK
Success: no issues found in 5 source files
TYPES OK
▶ hermetic run: TZ=UTC LANG=C.UTF-8 PYTHONHASHSEED=0, empty env
........................................................................ [ 38%]
........................................................................ [ 77%]
..........................................                               [100%]
186 passed in 1.75s
TESTS OK
VERIFY OK
```

This work item's own verification is local and unmocked-network, per its own constraints (no real spend needed for a pure state-threading fix — intent.md §Constraints): two ad-hoc Python scripts run in this session's scratchpad, not committed anywhere (matching CONV-09's own build-time smoke-check posture, spec.md §Non-goals).

- **Regression scenario** (re-running CONV-09 build's own mocked two-plugin scenario through the real, fixed `scripts/prove_conversation_e2e.py`): every original assertion still passes. The second child's printed key changed from `.../plugin_b_child/0` to `.../plugin_b_child/1` — expected, not a regression; see `docs/reference/dispatch_closure_state_bug.md`'s "Resolution" section for why.
- **Collision scenario** (two children spawned under the identical `node_name` across two turns): `spawned keys: ['collision-root/child/same_name/0', 'collision-root/child/same_name/1']` — different keys, no collision. `final conversation.next_child_seq = 2` — the reconciled value correctly reflects both spawns.
- CONV-09's own four artifact files confirmed byte-identical (`md5sum -c` against a baseline taken before this work item started) before and after the fix.

## Findings

**Compliance verification performed:** both `plan.md § Proof` items and all six `spec.md § Acceptance criteria` matched against the code and the (unrun-by-the-reviewer, but independently traced) verification claims; `spec.md § Rejected alternatives` checked for drift on all three items (none found); the five design principles checked explicitly, including re-verifying the hermes-agent `delegate_tool.py` prior-art claim directly against that file rather than trusting spec.md's summary of it; every file in the diff matched against `plan.md § Files that change`; and — the check that mattered most for this specific work item — confirmed `docs/tasks/CONV-09-deploy-evidence/`'s four files appear nowhere in `git diff --stat HEAD`, satisfying CLAUDE.md's rule against patching a closed work item.

### Important

None.

### Nits

None. Two items were raised as context, not findings: `plan.md`/`spec.md`'s unchecked `[ ]` boxes match this project's own established convention (CONV-09's closed spec.md has the same style post-merge); and `dispatch()` still *reads* `conversation` by closure to build each `parent` argument (only the *write* was removed) — spec.md's "removes `conversation` from the propagation path entirely" is accurate about what actually changed, but could be misread in isolation; the surrounding text in the same section is precise.

- [Bugs] None. The fix's mechanism was independently retraced end to end against `_child_key()`, `run_child()`, and `take_turn()`'s own stale-local construction (`conversation.py:1024-1070`) — confirmed to close the collision as designed. No staleness window exists between `run_child()` returning and the counter update (adjacent statements, no `await` between them), and `run_turn`'s TOOL_ROUND processes tool calls strictly sequentially, so no concurrent-dispatch race on the shared counter is possible either.
- [Security] None. No new I/O, no credentials, no network surface — pure in-process state-threading around an already-existing mechanism.

## Decision

Approved by Adam, 2026-09-04. No findings to resolve.
