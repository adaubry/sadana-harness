# Review: Serving the console's first screens through the door (from plan.md 2026-09-14)

Reviewed: main..HEAD (working tree) — 35 files, +3535/-18
Reviewer context: fresh session, no prior context on this work item — delegated specifically for a cold review.
Second opinion: none — this is the only review pass; the build's own self-check (/ponytail-review + /simplify) ran earlier, during Build, not repeated here.

## Evidence

```
docs/tasks/H20-door-nouns-turn-side: all present artifacts valid
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
docs/reference/ citations resolve to tracked files........................Passed
LINT OK
Success: no issues found in 77 source files
TYPES OK
▶ hermetic run: TZ=UTC LANG=C.UTF-8 PYTHONHASHSEED=0, empty env
................................................................
  two conversations, concurrently: 514 ms for two 200 ms turns
.  one conversation, twice: 465 ms for two 200 ms turns
....... [  5%]
........................................................................ [ 10%]
........................................................................ [ 16%]
........................................................................ [ 21%]
........................................................................ [ 27%]
........................................................................ [ 32%]
........................................................................ [ 38%]
........................................................................ [ 43%]
........................................................................ [ 49%]
........................................................................ [ 54%]
........................................................................ [ 59%]
........................................................................ [ 65%]
........................................................................ [ 70%]
........................................................................ [ 76%]
........................................................................ [ 81%]
........................................................................ [ 87%]
........................................................................ [ 92%]
........................................................................ [ 98%]
.........................                                                [100%]
1321 passed in 108.42s (0:01:48)
TESTS OK
VERIFY OK
```

Scope confirmed: `git diff main --shortstat` reports `35 files changed, 3535 insertions(+), 18 deletions(-)`, matching the brief. Every file in the diff is named in plan.md's `## Files that change` (including the "own files", the documented "shared files, additive-only" edits, and the four items added after approval that plan.md itself calls out); no file plan.md named goes untouched, and no file plan.md didn't name was touched.

## Findings

**Important.**

**1. `messages.list`/`traces.list` silently diverge from spec.md Requirement 9 on an unknown or foreign parent.** Requirement 9 states: "`list`/`get` scope through the parent conversation exactly as Requirement 2 (join `conversation_accounts`); an unknown or foreign `parent_id` is `404`." `messages.py`'s `get()` does this correctly (`src/sadana/door/nouns/messages.py:94-106`), but `list()` (`messages.py:81-92`) returns `grammar.ListResponse(data=[], ...)` — a `200` with an empty array — when the parent conversation doesn't resolve or belongs to another account, never a `404`. `traces.py`'s `list()` (`traces.py:71-83`) does the identical thing via `_parent_run`. This isn't an oversight caught by a gap in coverage — it's the tested, intended behavior: `tests/contract/nouns/test_messages.py:145-152`'s `test_foreign_conversation_is_not_found` and `tests/contract/nouns/test_traces.py:99-103`'s `test_foreign_account_sees_no_traces` both assert `json.loads(resp.body)["data"] == []`, not a 404 status — the test names promise a 404 that the assertions don't check for. Nothing in spec.md's § Design or § Rejected alternatives explains or authorizes this deviation for `list` specifically; it reads as a quiet drift rather than a decision. Functionally this is not a data leak (an empty list distinguishes nothing about whether the id exists or belongs to someone else), but it is a documented requirement the shipped code and its own tests both contradict.

**2. The acceptance criterion "a failed turn's assistant row carries `state: "failed"`" has zero test coverage anywhere in this diff.** spec.md's Acceptance criteria checklist and `messages.py`'s own Requirement 13 both call out this behavior explicitly, and the code that implements it is real (`src/sadana/door/nouns/messages.py:154-165`: on `not outcome.ok`, find the newly-appended assistant row and `UPDATE messages SET state = 'failed'`). But every failure-path test in the diff — `tests/contract/nouns/test_messages.py:118-142`'s `test_a_failed_turn_with_no_completion_leaves_no_assistant_row` and `tests/contract/nouns/test_golden_journey.py:252-271`'s `test_golden_journey_failed_turn_carries_the_diagnostic` — stubs `model_access.send` to raise `model_access.Abort` on the *very first* model call. Per `message.md`'s own documented behavior ("A failed turn does not always produce an assistant row... a turn that fails before any completion... leaves no new assistant row at all"), this specific failure shape never creates an assistant row in the first place, so the `assistant_row is not None` branch that sets `state = "failed"` is never exercised. A grep across every test file under `tests/contract/nouns/` for `state.*failed`/`assistant_row` confirms no other test drives a turn that completes at least one assistant turn and then fails afterward. The line that discharges a named, spec-required behavior is currently dead code from the test suite's point of view.

**Nits.**

- `tests/contract/nouns/test_spans.py` marks each test with `@pytest.mark.contract` individually; every other new file in the directory uses a module-level `pytestmark = pytest.mark.contract`. Same effect, inconsistent style within one PR.
- `conversations.create()` with a `name` given upfront runs two separate write transactions — `client_surface.open_conversation` (which writes `version=1`, ledger kind `"created"`) followed by `_apply_update` for the rename (`version=2`, ledger kind `"changed"`) — so a conversation created with a name returns `version: 2`, not `1`, and produces two ledger rows for one API call. Harmless (the default-to-key path stays `version: 1`), but undocumented anywhere in spec.md/plan.md.
- The acceptance-criteria line "with the right one → 200, version+1, **ledger row**" is not actually asserted by any test — `test_golden_journey.py`'s rename step checks `version + 1` but never reads back a ledger entry via `GET /v1/changes`. The underlying `ledger.record_change` call is exercised (so this is a documentation/coverage gap, not a functional one).
- `router.py`'s new `isinstance(result, DoorResponse)` pass-through (the artifact-download response) still participates in the generic `Idempotency-Key` replay/store path (`router.py:347-357`), which was designed around JSON bodies; a client that sends an `Idempotency-Key` on a `download` action would have the file's raw bytes persisted into the idempotency store. Untested edge case, pre-existing generic mechanism, not something this diff needed to solve — flagged for awareness, not required to fix here.

## Decision

Approved by Adam Aubry, 2026-09-14.
