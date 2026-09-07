# Review: handing back a link as itself, not as prose (from plan.md 2026-09-07)

Reviewed: 66fcfce..working-tree — 2 files, +53/-2 (after the Important finding's fix, applied in this branch)
Reviewer context: fresh session, no builder context
Second opinion: none — ran during build (self-check /ponytail-review + /simplify), not repeated here by design.

## Evidence

```
docs/tasks/G2-artifacts: all present artifacts valid
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
........................................................................ [ 42%]
........................................................................ [ 64%]
........................................................................ [ 85%]
.................................................                        [100%]
337 passed in 2.65s
TESTS OK
VERIFY OK
```

## Findings

Cold review, no builder context. Scope check, then three passes (bugs,
security, compliance), plus the two points this deploy stage was explicitly
told to scrutinize.

**Scope.** `git diff --stat HEAD` touches exactly
`src/sadana/plugin_manifest.py` and `tests/unit/test_plugin_manifest.py`,
matching plan.md's `## Files that change` exactly — no untouched-but-named
file, no touched-but-unnamed file.

**Scrutiny point 1 (isinstance scoped to `call` only).** Confirmed:
`isinstance(value, plugins.Artifact)` appears exactly once in
`src/sadana/plugin_manifest.py`, at line 366, inside the `elif node.kind ==
"call":` branch (357-369). The `compute` (344-345), `route` (346-349), `ask`
(350-356), and `stop` (370-371) branches contain no such check — a `compute`
node returning an `Artifact` threads it as an opaque object, which
`test_run_graph_compute_node_returning_artifact_is_not_recorded`
(`tests/unit/test_plugin_manifest.py:554-564`) confirms by construction
(`result.artifacts == ()`). Matches spec.md's Design section and its
Rejected-alternatives entry declining "any node may emit."

**Scrutiny point 2 (detail resets every iteration; leak-across-nodes
coverage).** `detail: str | None = None` is declared at
`src/sadana/plugin_manifest.py:342`, inside the `while True:` loop, in the
same statement group and same per-iteration scope as `port: str | None =
None` at line 341 — the reset pattern itself is correct and matches `port`'s
proven precedent exactly. See the Important finding below for the coverage
gap this point also asked about.

### Important

1. **No test confirmed `detail` does not leak from an artifact-emitting `call` node into the very next node's own trace entry — the specific correctness property plan.md itself named as "the one real correctness property to double check" (plan.md, "Order of work" step 1 and step 6).** `test_run_graph_call_node_emits_a_link_artifact` builds exactly the sequence this property needs — a `call` node (`reach_out`) that sets `detail`, immediately followed by a `stop` node (`done`) in the same walk — but its assertions only checked `result.trace[0].detail is not None` (the `call` node's own entry), never `result.trace[1]` (the `done` node's entry). Plan.md's own proposed check, re-running `test_run_graph_walks_compute_ask_route_and_stop_together`, cannot discharge this either: that test has no `call` node at all, so nothing in it ever sets `detail` away from `None` — it cannot exercise leakage by construction.

   **Fixed during this review**: `assert result.trace[1].detail is None` added to `test_run_graph_call_node_emits_a_link_artifact` (`tests/unit/test_plugin_manifest.py:551`). Re-ran `make verify` after the change: still `VERIFY OK` (337 passed).

Everything else checked came back clean:

- **Bugs** — traced the full `call` branch (`plugin_manifest.py:357-369`) and the two closures it feeds (`result()`, 321-329; the shared `trace.append`, 377) by hand. `_coerce_text` (`plugin_manifest.py:225-236`) returns a `str` value unchanged, so an artifact-emitting `call` node that is also the walk's terminal node produces a clean `DagResult.text` equal to the bare `ref`, not a JSON- or repr-wrapped string — no dumping-ground regression. No other logic error found.
- **Security** — no new import, no new external reach, no secret introduced; the `isinstance` check only inspects an already-in-process return value.
- **Proof (plan.md).** `test_run_graph_call_node_emits_a_link_artifact` (`tests/unit/test_plugin_manifest.py:531-550`) discharges the artifact-recorded-and-`.ref`-threaded proof. `test_run_graph_compute_node_returning_artifact_is_not_recorded` (554-564) discharges the `compute`-is-inert proof. `test_run_graph_call_node_runs_its_body_and_threads_the_result`, strengthened with `assert result.artifacts == ()` (`tests/unit/test_plugin_manifest.py:527`), discharges the plain-value-still-inert proof. `tests/unit/test_plugin_dispatch.py` and `tests/unit/test_plugins.py` are untouched by the diff and pass (337 passed total, Evidence above). `make verify` ends `VERIFY OK` (Evidence, above).
- **Acceptance criteria (spec.md).** Criterion 1 (artifact recorded) and criterion for `.ref` threading (requirement 3) — `tests/unit/test_plugin_manifest.py:547-548`. Criterion for plain-value byte-identical behavior (requirement 4) — `tests/unit/test_plugin_manifest.py:527`. Criterion for `compute`/`route`/`ask`/`stop` never populating `artifacts` — `tests/unit/test_plugin_manifest.py:554-564` (via `compute`) plus the direct code read above (no `isinstance` check exists in the other three branches, so none of them could populate `artifacts` even absent a fixture for each). Criterion for `NodeTrace.detail` non-`None` on the emitting node — `tests/unit/test_plugin_manifest.py:550` (the mirror half of this same criterion, `detail=None` unchanged for a plain-value `call` node, has no test asserting it directly, folded into the Important finding above since it is the same underlying gap). `test_plugin_dispatch.py` unchanged and green. `make verify` ends `VERIFY OK`.
- **Rejected alternatives (spec.md).** No second parameter or callback added to a body's signature — `run_body` (`plugin_manifest.py:362-363`) still closes over only `node`/`value`, same shape as before this diff. No `isinstance` check added to `compute`/`route`/`ask`/`stop` — confirmed by direct read, above. The raw `Artifact` object is never threaded forward — `value = value.ref` (line 369) extracts the string before the next node sees it. No file-writing or output-directory concept introduced anywhere in the diff.
- **Five design principles** — no violation found. (1) spec.md's own Design section cites `../hermes-agent/agent/image_gen_provider.py`'s flat-return-value pattern as prior art for declining a second emit channel. (2) The change is additive and cheap to reverse (a single `isinstance` check in one branch); no expensive-to-reverse decision outside spec.md's own sanction. (3) The change completes an already-declared core field (`DagResult.artifacts`, existing since D1) rather than inventing new core surface or a premature plugin seam. (4) The `isinstance` check sits only in the `call` branch — no step was added to `compute`/`route`/`ask`/`stop`'s common path. (5) `artifacts: list[plugins.Artifact]` (`plugin_manifest.py:317`) is function-local, dies with the walk, and has the identical lifetime and shape as the pre-existing `trace` list it sits beside — no new class, no state that outlives one `run_graph` call.

### Nits

None found worth listing separately — the one deviation surfaced above is a coverage gap, not a style preference.

## Decision

Approved by Adam Aubry, 2026-09-07, with the Important finding fixed in
this branch before merge: `test_run_graph_call_node_emits_a_link_artifact`
strengthened with `assert result.trace[1].detail is None`, proving the
`detail`-reset property plan.md itself named as the one to double-check.
