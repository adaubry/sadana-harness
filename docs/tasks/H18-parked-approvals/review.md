# Review: parked approvals (from plan.md 2026-09-14)

Reviewed: HEAD (411e866)..working tree — 22 files (18 modified, 4 new), +1169/-131 on
modified files plus ~694 lines across the 4 new files (`src/sadana/door/nouns/approvals.py`,
`scripts/prove_call_approval_e2e.py`, `docs/console/nouns/approvals.md`,
`tests/contract/nouns/test_approvals.py`). Uncommitted working-tree diff — no base
commit exists for this work item yet, per the task's own scope note; `git diff HEAD`
used in place of `git diff <base>..HEAD`, `git status --short` for the file list.

Reviewed: fresh session — no prior context on this work item, delegated for
exactly that reason (cold review).
Second opinion: none — plan.md § Order of work step 9 records a self-check
(`/ponytail-review` + `/simplify`) during build; not repeated here by design.

## Evidence

**Update, after fixes**: every finding below is resolved (see the note on
each). Fresh `make verify`, same repo, after the fixes:

```
$ make verify
docs/tasks/H18-parked-approvals: all present artifacts valid
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
shellcheck..................................................................Passed
Detect secrets...............................................................Passed
docs/reference/ citations resolve to tracked files..........................Passed
LINT OK
Success: no issues found in 71 source files
TYPES OK
▶ hermetic run: TZ=UTC LANG=C.UTF-8 PYTHONHASHSEED=0, empty env
................................
  two conversations, concurrently: 262 ms for two 200 ms turns
.  one conversation, twice: 445 ms for two 200 ms turns
....................................... [  5%]
........................................................................ [ 11%]
........................................................................ [ 16%]
........................................................................ [ 22%]
........................................................................ [ 27%]
........................................................................ [ 33%]
........................................................................ [ 38%]
........................................................................ [ 44%]
........................................................................ [ 49%]
........................................................................ [ 55%]
........................................................................ [ 60%]
........................................................................ [ 66%]
........................................................................ [ 71%]
........................................................................ [ 77%]
........................................................................ [ 82%]
........................................................................ [ 88%]
........................................................................ [ 93%]
........................................................................ [ 99%]
..........                                                               [100%]
1306 passed in 71.46s (0:01:11)
TESTS OK
VERIFY OK
```

1306 rather than 1303: 2 new `tests/unit/test_scheduling.py` tests (finding
2, below) plus 1 new `tests/contract/nouns/test_approvals.py` regression
test (finding 1, below).

**Original evidence, at the time of the cold review** (kept for the audit
trail — this is what the four findings below were found against):
`make verify` did **not** end `VERIFY OK`. Full output:

```
$ make verify
docs/tasks/H18-parked-approvals: all present artifacts valid
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
ruff.....................................................................Failed
- hook id: ruff
- exit code: 1

tests/unit/test_plugin_dispatch.py:575:121: E501 Line too long (130 > 120)
    |
573 |     )
574 |
575 |     result = asyncio.run(plugin_dispatch.resume_paused_run(conn, "k1", decision="answer", state="answered", payload="the-answer"))
    |                                                                                                                         ^^^^^^^^^^ E501
576 |
577 |     assert result.failed_node is None
    |

tests/unit/test_plugin_dispatch.py:593:121: E501 Line too long (130 > 120)
tests/unit/test_plugin_dispatch.py:611:121: E501 Line too long (130 > 120)
tests/unit/test_plugin_dispatch.py:635:121: E501 Line too long (130 > 120)
tests/unit/test_plugin_dispatch.py:660:121: E501 Line too long (132 > 120)
tests/unit/test_plugin_dispatch.py:715:121: E501 Line too long (137 > 120)
tests/unit/test_plugin_dispatch.py:768:121: E501 Line too long (121 > 120)
tests/unit/test_plugin_dispatch.py:787:121: E501 Line too long (130 > 120)
tests/unit/test_plugin_dispatch.py:939:121: E501 Line too long (121 > 120)
tests/unit/test_plugin_dispatch.py:960:121: E501 Line too long (121 > 120)

Found 10 errors.

ruff-format..............................................................Failed
- hook id: ruff-format
- files were modified by this hook

1 file reformatted, 204 files left unchanged

shellcheck...............................................................Passed
Detect secrets...........................................................Passed
docs/reference/ citations resolve to tracked files.......................Passed
make: *** [Makefile:27: lint] Error 1
```

`make verify` fails-fast at `lint`, so `typecheck` and `test` never ran under
that invocation. Run directly (same repo, same HEAD, informational — not a
substitute for a green `make verify`):

```
$ make typecheck
Success: no issues found in 71 source files
TYPES OK

$ bash scripts/run_tests.sh
▶ hermetic run: TZ=UTC LANG=C.UTF-8 PYTHONHASHSEED=0, empty env
...
1303 passed in 96.76s (0:01:36)
TESTS OK
```

`scripts/prove_call_approval_e2e.py` run by hand (spec.md's own Deploy-stage
evidence requirement, a real `sadana door serve` process reached over real
HTTP):

```
=== parking a real `call` node through the webhook path ===
handle_inbound -> ok=True text='noted.'
[ok] real plugin_pauses row: plugin='p' node='reach_out' kind='call'

=== the approvals row before anyone answered it ===
{"id": "appr_01a0a09b86147000871beac5e11b5b4f", "conversation_key": "webhook:call-approval-e2e-chat", "kind": "call", "state": "waiting", "answered_by": null, "answer": null, "version": 1}

=== starting a real `sadana door serve` process (in-thread) ===
[ok] the door is listening on http://127.0.0.1:18789/

=== approving it over real HTTP, from a different transport than the one that parked it ===
POST .../actions/approve -> status=200 body={'id': 'appr_01a0a09b86147000871beac5e11b5b4f', ..., 'state': 'approved', ..., 'answered_by': 'console:prover', ...}
assert status == 200 -> ok
assert body["state"] == "approved" -> ok

=== the approvals row after ===
{"id": "appr_01a0a09b86147000871beac5e11b5b4f", "conversation_key": "webhook:call-approval-e2e-chat", "kind": "call", "state": "approved", "answered_by": "console:prover", "answer": null, "version": 2}
[ok] the pause is cleared — the call node's body ran, off a real HTTP request

ALL ASSERTIONS PASSED
```

## Findings

Reformatted from the original cold-review pass: the `### Important`
sub-heading below `## Findings` made `scripts/artifact.py`'s own section
parser (which splits on any `#`-`######` line, not just matching depth)
read the whole section as empty, failing `make chain` on an artifact whose
content was never actually empty. Flattened to a plain "**Important**"
label — a `scripts/` fix is out of this work item's own scope (unverified,
per this project's own tracked experience with that directory) and this is
the smaller, zero-risk fix. All four findings below are the review's own,
unedited, and all four are now resolved (see the note after each).

**Important**

- [Bugs] `make verify` does not end `VERIFY OK`. `ruff` fails on 10 E501
  (line too long, up to 137 chars) violations, all in
  `tests/unit/test_plugin_dispatch.py`, all on lines that call the new
  `resume_paused_run(conn, "k1", decision=..., state=..., payload=...)`
  signature (lines 575, 593, 611, 635, 660, 715, 768, 787, 939, 960). This is
  the Proof section's own last line ("`make verify` ending `VERIFY OK`"),
  and it is not discharged as shipped. Typecheck and the full suite both pass
  when run directly (71 files clean under mypy, 1303 tests green), so the
  underlying logic is not in question — only the line length, which needs
  wrapping in that one file before this can merge.

  **Resolved.** `ruff check`/`ruff-format` now pass clean (verified by
  hand and via `make lint`/`make verify` above) — the 10 lines were already
  under 120 chars by the time of this write-up; whatever produced the
  original 10-line report reflected an earlier state of the file, not the
  current one.

- [Bugs] Acting on an approval whose `kind` doesn't match the action crashes
  with `500 INTERNAL` instead of the `409 CONFLICT` spec.md's own acceptance
  criteria requires ("a kind mismatch... is 409 CONFLICT — the router's own
  state gate, proven against a real call-kind row"). Reproduced directly: a
  `wait`-kind `waiting` approval hit with `POST .../actions/approve` raises
  `AssertionError: a wait-kind pause needs decision answer, got 'approve'`
  inside `plugin_dispatch.resume_paused_run` (`plugin_dispatch.py:366`),
  called from `door/nouns/approvals.act`'s bare `asyncio.run(...)`
  (`src/sadana/door/nouns/approvals.py:149-159`, no exception handling around
  it). `router.py`'s state gate (`router.py:264-275`) only compares
  `current_state` against `action.from_states` — it has no notion of `kind`,
  so a `wait`-kind row in state `waiting` passes the gate for `approve`
  exactly as a `call`-kind row does, and the mismatch is only caught inside
  `resume_paused_run`'s `assert`, which `router.py`'s top-level catch-all
  (`router.py:181-184`) turns into a generic, unhelpful `500`.
  `docs/console/nouns/approvals.md:17-22` describes `approve`/`decline` as
  "`call` only" and `answer` as "`wait` only" as if this were already
  enforced — it isn't. Neither `tests/unit/test_plugin_dispatch.py` nor
  `tests/contract/nouns/test_approvals.py` exercises a kind-mismatched
  action, so this shipped untested and broken; the acceptance criterion
  naming it is undischarged.

  **Resolved.** `door/nouns/approvals.py` gained `_REQUIRED_KIND`
  (`{"approve": "call", "decline": "call", "answer": "wait"}`) and `act`
  now checks `row.kind` against it — a clean `409 CONFLICT` — before ever
  calling `resume_paused_run`. Reproduced-then-fixed directly (same repro
  script, now returns 409); proven by
  `test_approve_on_a_wait_kind_row_is_409_not_a_crash` in
  `tests/contract/nouns/test_approvals.py`. spec.md's Interface, Design §7,
  and Acceptance criteria sections all updated to describe the real
  mechanism instead of "the router's own state gate," which was never
  actually going to catch this.

- [Compliance] `plan.md` § Files that change names `tests/unit/test_scheduling.py`
  with two specific new tests ("`tick()` calls `expire_due` before the
  trigger loop; a failing `expire_due` row doesn't stop trigger firing or
  other rows' expiry" — repeated in `plan.md` § Proof). The diff never
  touches this file: `git status --short -- tests/unit/test_scheduling.py`
  is empty, and the file has zero references to `expire_due` or `approvals`.
  `scheduling.tick()` (`scheduling.py:75`) does call
  `door_approvals.expire_due(runtime.connections, now)` unconditionally
  before the trigger loop, with no `try`/`except` of its own around that
  call — `expire_due` catches per-row failures internally, but nothing
  proves `tick()`'s own posture toward it (an unexpected failure inside
  `conversation_store.due_approvals` itself, for instance, would propagate
  out of `tick()` uncaught and skip the trigger loop entirely). A file
  `plan.md`'s current version names as in scope, with named tests, that the
  diff does not touch.

  **Resolved.** Both named tests now exist in `tests/unit/test_scheduling.py`:
  `test_tick_calls_expire_due_once` and
  `test_tick_one_failing_approval_expiry_does_not_stop_trigger_firing`
  (the latter also answers this finding's own follow-on question — an
  `expire_due` row whose `resume_paused_run` call raises is caught and
  skipped without stopping the trigger loop or being marked `expired`).
  `tick()` itself still has no `try`/`except` of its own directly around
  the `expire_due` call — that call has no failure mode of its own left
  uncaught, since `expire_due` already catches per-row; a failure in
  `due_approvals` itself (a schema/connection problem) is the same class of
  failure `due_triggers` right below it is equally unguarded against, and
  extending that posture is a separate, wider decision this work item does
  not make unilaterally.

- [Compliance] `spec.md` § Interface documents `resume_paused_run`'s full
  signature as `(conn, conversation, *, decision, payload=None,
  answered_by=None, approve=...)` — no `state` parameter anywhere — and
  § Design 3's own narrative describes `state` as derived ("state set to
  ... (matching `decision`)"), not caller-supplied. The diff instead adds a
  required, no-default keyword `state: Literal["approved", "declined",
  "answered", "expired"]` (`plugin_dispatch.py:614-625`) that every caller —
  `client_surface.py:439`, `door/nouns/approvals.py:154`,
  `scheduling`'s `expire_due` (`door/nouns/approvals.py:192`) — must now
  pass explicitly. The change itself looks like the right fix for a real
  inconsistency `spec.md`'s own narrative left unresolved (expiry resumes
  with `decision="decline"` but the row must read `"expired"`, a different
  fact than a person declining — `spec.md`'s own § Design 4 acceptance
  criterion requires exactly this), and `conversation_store.resolve_pause`'s
  docstring explains the reasoning well. But it is an undocumented deviation
  from `spec.md`'s own Interface contract, and `plan.md`'s file-list entry
  for `plugin_dispatch.py` ("signature grows decision/answered_by,
  payload_text→payload rename") does not mention it either — unlike every
  other deviation in this build, which got an explicit "not in the original
  plan" note in `plan.md`'s file list (`test_state_words.py`,
  `test_subcommands_chat.py`, `test_console_grammar.py`,
  `test_door_capabilities.py`, `test_door_nouns_harness.py`). The artifact
  chain no longer describes this function's real shape.

  **Resolved.** spec.md's Interface section now documents `state` and the
  full account of why it exists (the `/simplify` altitude finding that
  replaced an implicit `decision`→`state` derivation plus a `state_override`
  escape hatch with one explicit required parameter every caller states);
  `plan.md` gained a "Deploy-stage cold review findings, closed" section
  naming this and the other two findings explicitly, matching the pattern
  the other five plan deviations already used.

## Decision

Approved by adam, 2026-09-14, with all four Important findings fixed in
this branch before merge (see the resolution note under each finding
above).
