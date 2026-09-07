# Review: asking before a step reaches outside (from plan.md 2026-09-07)

Reviewed: 14ccd04..working-tree — 4 files, +109/-5
Reviewer context: fresh session, no builder context
Second opinion: none — ran during build (self-check /ponytail-review + /simplify), not repeated here by design.

## Evidence

```
docs/tasks/F1-call-node-approval: all present artifacts valid
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
Success: no issues found in 12 source files
TYPES OK
▶ hermetic run: TZ=UTC LANG=C.UTF-8 PYTHONHASHSEED=0, empty env
........................................................................ [ 21%]
........................................................................ [ 43%]
........................................................................ [ 64%]
........................................................................ [ 86%]
..............................................                           [100%]
334 passed in 2.73s
TESTS OK
VERIFY OK
```

## Findings

**Compliance verification performed:** every `plan.md § Proof` item and every `spec.md § Acceptance criteria` item matched against a specific test or line of code; `spec.md § Rejected alternatives` re-checked against the diff for drift (none found); the five design principles checked explicitly; and every file in `git diff --stat HEAD` matched against `plan.md § Files that change` — which is where the one finding below came from.

### Important

- `CLAUDE.md:47` is edited by this diff but plan.md's `## Files that change` section names only `src/sadana/plugins.py`, `src/sadana/plugin_manifest.py`, and `tests/unit/test_plugin_manifest.py` (plus two explicit "no change" files) — `CLAUDE.md` appears in neither list. This is the same class of gap this project's own history has already flagged twice: commit `b1cdd7c` (D1-dagresult-dispatch)'s message records "two Important compliance findings (an unplanned CLAUDE.md edit, ...)" and commit `14ccd04` (E1-call-node-execution)'s message records "plan.md corrected in place for a file-list..." — in both prior cases the fix was to correct plan.md's file list to match the CLAUDE.md edit, not to drop the rule. Left as-is, `plan.md` no longer accurately describes what this work item touched, and a future reader diffing plan.md against the commit to understand "what changed and why" will not learn that a new backbone rule was added here.

  **Fixed**, matching the established precedent: `plan.md`'s `## Files that change` now lists `CLAUDE.md` and names the rule it adds. No code changed; `make verify` re-run clean after the edit (`docs/tasks/F1-call-node-approval` continues to validate).

### Nits

None — the one issue found was significant enough to be Important, and I did not find anything else worth flagging as a nit after checking scope, three passes, and the compliance items below.

**What I checked that came back clean (no other findings):**
- **Bugs**: traced `run_graph`'s new `call` branch (`plugin_manifest.py:350-352`) — `approve` is awaited exactly once, before `_resolve_body` or any other work for the node, inside the existing per-node `try`, so a stray exception from `approve` falls through to the existing generic `except Exception` handler (`plugin_manifest.py:357-358`) exactly as spec's Interface section requires, with no special-casing added. `_default_approve` (`plugin_manifest.py:263-274`) fails closed for any non-"y"/"yes" answer including case variants and empty string, verified against `test_default_approve_rejects_anything_else`'s parametrization (`""`, `"n"`, `"no"`, `"maybe"`).
- **Security**: no secrets introduced; `_default_approve`'s `input()` prompt only echoes plugin name, node name, and the in-flight value to the local terminal it already controls — no new network, filesystem, or credential surface. `asyncio.to_thread(input, prompt)` keeps the blocking read off the event loop, matching the stated rationale.
- **Compliance — plan.md Proof**: all five Proof items are discharged by name — `test_default_approve_accepts_yes` and `test_default_approve_rejects_anything_else` (`tests/unit/test_plugin_manifest.py:399-411`), the updated `test_run_graph_refuses_call_nodes` (`tests/unit/test_plugin_manifest.py:512-520`), `test_run_graph_call_node_declined` (`tests/unit/test_plugin_manifest.py:523-530`), `test_run_graph_call_node_records_what_was_asked` (`tests/unit/test_plugin_manifest.py:533-546`), and `tests/unit/test_plugin_dispatch.py` passing with a byte-identical diff (confirmed via `git diff HEAD -- src/sadana/plugin_dispatch.py tests/unit/test_plugin_dispatch.py` producing no output) — plus the `VERIFY OK` above.
- **Compliance — spec.md Acceptance criteria**: `plugins.ApproveFn` sits directly below `AskFn` (`src/sadana/plugins.py:408,414`); `run_graph` gains `approve: plugins.ApproveFn = _default_approve` and calls it exactly once, only for `call`, before any other work on that node (`plugin_manifest.py:277,350-352`); decline sets `failed_node` with `detail == "declined"` (`test_run_graph_call_node_declined`); approval still ends the walk with `detail` containing "not runnable yet" (`test_run_graph_refuses_call_nodes`); `compute`/`ask`/`route`/`stop` never call `approve` — proven by an `approve` stub that raises if invoked, wired into `test_run_graph_walks_compute_ask_route_and_stop_together` (`tests/unit/test_plugin_manifest.py:601-604,617-627`); `_default_approve` is covered via `monkeypatch.setattr("builtins.input", ...)` in both new tests, never touching real stdin.
- **Compliance — spec.md Rejected alternatives**: no drift found. `ApproveFn` keeps plain positional args, not a dataclass (`plugins.py:414`). `approve` is defaulted, not a mandatory keyword like `ask` (`plugin_manifest.py:277`). The gate lives inside `run_graph`'s per-node loop, not at `plugin_dispatch.build_dispatch`'s entry point — confirmed by the empty diff on `plugin_dispatch.py`.
- **Design principle 1 (reference first)**: spec.md documents having read `../hermes-agent/tools/approval.py` and `agent/tool_guardrails.py` in full and gives a specific, itemized reason for declining both; nothing in the diff pulls in any of the declined machinery (no session keys, no wait-time ceilings, no denial-breaker thresholds).
- **Design principle 2 (reduce bets)**: the one new public surface, `ApproveFn`, is a single defaulted keyword parameter — cheap to widen or remove later, and spec.md's own Rejected alternatives section reasons through exactly this tradeoff.
- **Design principle 3 (more plugins, not more core)**: this is core-loop code, but it mirrors `AskFn`'s existing injected-callable shape rather than inventing a new mechanism, and spec.md explicitly designed it that way; no plugin-side seam was available to carry this instead.
- **Design principle 4 (least step-cost)**: the check is placed inside the single per-node-kind branch `run_graph` already runs, not as an added step at `dispatch()`'s entry (which spec.md's Rejected alternatives shows would ask too broadly, including on `call`-free paths); this is the cheaper of the two placements, not a heavier one.
- **Design principle 5 (minimise mutable state)**: `_default_approve` is a stateless per-call coroutine; no module-level dict, lock, or counter was added anywhere in the diff.

## Decision

Approved by Adam Aubry, 2026-09-07, with the Important finding fixed in this
branch before merge: `plan.md`'s `## Files that change` corrected to list
`CLAUDE.md`, matching the `b1cdd7c`/`14ccd04` precedent.
