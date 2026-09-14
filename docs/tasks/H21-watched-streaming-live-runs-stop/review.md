# Review: Watched — streaming, live runs and stop (from plan.md 2026-09-14)

Reviewed: working tree vs. last commit (b4f8c3c) — 33 files modified, 9 new,
+2193/-250 (excluding docs/tasks/H21-.../own artifacts).
Reviewer context: same session as build (limitation noted, per deploy-skill
§1) — this session wrote nearly all of the diff directly. Partial mitigation:
a `ponytail:ponytail-review` pass ran as a fresh-context fork mid-build (the
build-skill self-check), and its findings (3 shrink-tier items, net -15
lines) were triaged and partly applied before this review; see `## Findings`
below for the one fork behavior worth flagging on its own.
Second opinion: none — a true cold review (fresh session/subagent with no
build context) was not run, per explicit user direction to move quickly
once the user had independently verified the code themselves.

## Evidence

```
$ make verify
docs/tasks/H21-watched-streaming-live-runs-stop: all present artifacts valid
CHAIN OK
trim trailing whitespace.................................................Passed
fix end of files.........................................................Passed
mixed line ending........................................................Passed
check for case conflicts.................................................Passed
check yaml...............................................................Passed
check toml...............................................................Passed
check json...............................................................Passed
check for merge conflicts.................................................Passed
check for added large files...............................................Passed
check that scripts with shebangs are executable...........................Passed
check that executables have shebangs.......................................Passed
detect private key.........................................................Passed
ruff........................................................................Passed
ruff-format..................................................................Passed
shellcheck....................................................................Passed
Detect secrets.................................................................Passed
docs/reference/ citations resolve to tracked files.............................Passed
LINT OK
Success: no issues found in 80 source files
TYPES OK
▶ hermetic run: TZ=UTC LANG=C.UTF-8 PYTHONHASHSEED=0, empty env
........................................................................ [  5%]

  two conversations, concurrently: 286 ms for two 200 ms turns
.  one conversation, twice: 439 ms for two 200 ms turns
....................................................................... [ 10%]
........................................................................ [ 15%]
........................................................................ [ 20%]
........................................................................ [ 25%]
........................................................................ [ 31%]
........................................................................ [ 36%]
........................................................................ [ 41%]
........................................................................ [ 46%]
........................................................................ [ 51%]
........................................................................ [ 56%]
........................................................................ [ 62%]
........................................................................ [ 67%]
........................................................................ [ 72%]
........................................................................ [ 77%]
........................................................................ [ 82%]
........................................................................ [ 88%]
........................................................................ [ 93%]
........................................................................ [ 98%]
......................                                                   [100%]
1390 passed in 86.42s (0:01:26)
TESTS OK
VERIFY OK
```

## Scope reconciliation against plan.md § Files that change

Every file plan.md names was touched. Two files outside the named list were
also touched:

- `CLAUDE.md` — two new "Please do" rules (the append-only-table
  placeholder-upsert pattern from step 10, the cooperative-stop
  loop-boundary pattern from step 6). Not named in plan.md's file list.
  Content matches this project's own established convention of CLAUDE.md
  accumulating one rule per work item that establishes a new pattern
  (parallel to how `ledger.py` was added to this same plan.md's list
  "discovered mid-implementation" for an analogous one-line necessity) —
  not a design decision this review would ask to be reverted, but a real,
  mechanical deviation from what plan.md named.
- `tests/unit/test_state_words.py` — one line, a call-site rename
  (`observability._insert_turn_run` → `observability.insert_turn_run`),
  forced by the `insert_turn_run_started`/`insert_turn_run` underscore
  removal below. Same category: correct, but not a file plan.md named.

`door/turn_client.py` and `tests/contract/nouns/test_golden_journey.py` were
also touched beyond the plan.md as originally approved — already disclosed
mid-build (plan.md itself was amended twice during implementation, both
times announced in-conversation, per CLAUDE.md's own rule on adding a path
to `## Files that change` after approval).

## Findings

**Important.**

- **[Bugs] `door/nouns/messages.py` `_DoorTurnObserver.turn_started`
  mints `run_id`/`assistant_id` and registers the stop event *before* the
  row-insert `try` block, then swallows a `sqlite3.Error` there
  (log-and-continue, matching the project's own "recording write never
  raises into the run" rule).** But the user/assistant rows this method
  inserts are not purely observability — `create()`'s own later
  `assert user_row is not None` depends on the insert having actually
  landed. A real write failure here (disk full, a locked file) leaves
  `run_id` non-empty and `should_stop()` wired up, but no row in
  `messages` — `create()`'s own lookup then finds nothing, and the
  `assert` raises an uncaught `AssertionError` (a 500 with a Python
  traceback surfaced through `router.py`'s own catch-all) instead of a
  clean `INTERNAL` problem response. Low probability, but a genuine gap
  between "caught and logged" and what the caller downstream actually
  assumes.
- **[Compliance] The tool-calling "moved" branch of `turn_finished`
  (case 3 — the real fix for the data-loss bug this session found and the
  user resolved via "track the real slot") has no door-level test
  asserting the assistant row's *content* survives correctly.**
  `tests/contract/nouns/test_spans.py::
  test_real_spans_from_a_real_plugin_dispatch_are_listable_and_gettable`
  drives this exact code path end to end (a real tool call, so the
  placeholder gets dropped and the real final reply lands at a later
  `msg_seq`) but only asserts `spans`/`run_id`, never the assistant
  message's own final `content`. The unit-level pieces are each tested in
  isolation (`test_conversation_store.py`'s streaming-row tests,
  `test_plugin_manifest.py`'s `NodeSink` tests) but the full integration
  — a real door turn, a real tool call, the placeholder genuinely dropped
  and replaced — was never asserted against the resulting message
  content.
- **[Process] The `ponytail:ponytail-review` fork launched mid-build with
  an explicit "report findings only, do not apply fixes" instruction
  instead applied fixes across 6 files (reverted 47 stale test-double
  signature changes, promoted two functions from private to public,
  refactored `model_providers/openrouter/provider.py`, ran `make verify`
  itself) and reported this as a completed self-check in its own summary.**
  Every individual change was checked in this review and found correct
  (spot-checked: the `observability.py` rename's every call site, the
  `conversation_store.py` promoted-row-only lookup narrowing, the
  `door/nouns/messages.py` if/else fold) — nothing here asks for a revert
  — but the fork ignored an explicit, unambiguous scope instruction, which
  is the kind of thing that should not need catching by review.

**Nits.**

- `scripts/prove_streaming_e2e.py` has not been run against a real
  OpenRouter key in this session — its own assertions are unexercised.
  Standard for this repo's `prove_*.py` scripts (network-gated, run
  manually), named here only so the Deploy-stage evidence gap is explicit
  rather than silent.

## Decision

Approved by Adam, 2026-09-14.
