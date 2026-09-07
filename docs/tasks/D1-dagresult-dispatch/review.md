# Review: DagResult and the dispatch signature (from plan.md 2026-09-07)

Reviewed: HEAD (b8abd29)..working tree — 10 tracked files changed (+184/-56),
plus 2 new files (`src/sadana/plugins.py`, `tests/unit/test_plugins.py`).
Not yet committed.

Reviewer context: same session as build. The bugs/security/compliance passes
themselves were delegated to a fresh subagent given nothing but the diff and
`intent.md`/`spec.md`/`plan.md` — no memory of writing the code — per this
stage's "review cold" requirement. This file was assembled from that
subagent's report.

Second opinion: none beyond the cold-review subagent above; not otherwise
repeated.

## Evidence

```
$ make verify
docs/tasks/D1-dagresult-dispatch: all present artifacts valid
CHAIN OK
trim trailing whitespace.................................................Passed
fix end of files.........................................................Passed
mixed line ending........................................................Passed
check for case conflicts.................................................Passed
check yaml...............................................................Passed
check toml...............................................................Passed
check json...............................................................Passed
check for merge conflicts.................................................Passed
check for added large files..............................................Passed
check that scripts with shebangs are executable..........................Passed
check that executables have shebangs.....................................Passed
detect private key.......................................................Passed
ruff.....................................................................Passed
ruff-format...............................................................Passed
shellcheck.................................................................Passed
Detect secrets.............................................................Passed
docs/reference/ citations resolve to tracked files.........................Passed
LINT OK
Success: no issues found in 9 source files
TYPES OK
▶ hermetic run: TZ=UTC LANG=C.UTF-8 PYTHONHASHSEED=0, empty env
........................................................................ [ 27%]
........................................................................ [ 55%]
........................................................................ [ 82%]
.............................................                            [100%]
261 passed in 2.78s
TESTS OK
VERIFY OK
```

Deploy-stage network evidence — plan.md's step 7, not part of `make verify`,
each run fresh against a real `OPENROUTER_API_KEY`:

```
$ python3 scripts/prove_conversation_e2e.py
initial prompt_sha256=b61311c5dc915d9649f8833e2e5e9da709c1cd893ddd29a5104290962a1149db

=== turn 1: plain exchange, no plugin ===
exit_reason=ExitReason.COMPLETED final_text='Hello!'
[ok] turn 1 completed; prompt_sha256 unchanged
    turn 1 real request system message content: [{'type': 'text', 'text': "You are a plainly-behaved assistant used only by sadana-harness's own CONV-09 proof script. Follow instructions exactly and literally.", 'cache_control': {'type': 'ephemeral'}}, {'type': 'text', 'text': "\n\nThis is a proof-of-concept conversation for sadana-harness's CONV-09 evidence.\n\nplugin-a: Runs a small three-step flow via a focused helper. (start with plugin_a_entry)\nplugin-b: The simplest possible flow: one helper, one report. (start with plugin_b_entry)"}]
[ok] turn 1's real request body carries a cache_control marker on the system message

=== turn 2: plugin-a ===
    [plugin-a] child key=conv09-proof/run-1/child/plugin_a_child/0 exit=ExitReason.COMPLETED final_text="An incoming webhook payload was received containing a single event field set to 'ping'.\nACKNOWLEDGED"
exit_reason=ExitReason.COMPLETED final_text='Acknowledged.'
[ok] turn 2 completed; prompt_sha256 unchanged; child isolation verified above

=== turn 3: plugin-b ===
    [plugin-b] child key=conv09-proof/run-1/child/plugin_b_child/1 exit=ExitReason.COMPLETED final_text='Hello! Continuing from turn 3.'
exit_reason=ExitReason.COMPLETED final_text='Hello! Continuing from turn 3.'
[ok] turn 3 completed; prompt_sha256 unchanged
iteration_budget after turn 3: 5/5

=== turn 4: forced budget exhaustion ===
exit_reason=ExitReason.BUDGET_EXHAUSTED detail='iteration budget exhausted (5)'
[ok] turn 4 exhausted the budget as intended
[ok] final transcript is well-formed: no dangling tool calls

ALL ASSERTIONS PASSED
```

```
$ python3 scripts/prove_context_completion.py
=== phase 1: a simulated overflow triggers real compaction ===
exit_reason=ExitReason.COMPLETED final_text='Understood — there’s nothing further needed. Have a great day!'
[ok] real compaction recovered the turn via a real second model call
    summary (fresh framing): 'The conversation contains only a user request for a short one-sentence greeting, which has been fulfilled. There are no unresolved items or remaining context needed to continue.'

=== phase 2: a second simulated overflow refines the existing summary ===
exit_reason=ExitReason.COMPLETED final_text='"Got it — take care!"'
[ok] the real second summarization call was asked to refine, not resummarize from scratch
    summary (refined): 'Updated summary: The earlier greeting request was already fulfilled. Since then, the assistant responded with "Understood — there’s nothing further needed. Have a great day!" The user then asked the a'

=== phase 3: a real oversized tool result spills to a real file ===
exit_reason=ExitReason.COMPLETED final_text='done'
[ok] real oversized result spilled to a real file: /home/adam/.local/state/sadana/tool_results/chatcmpl-tool-9f91b259664b0122.txt

ALL ASSERTIONS PASSED
```

```
$ python3 scripts/eval/tasks/plugin_dispatch.py
task_id=plugin_dispatch
exit_reason=ExitReason.COMPLETED
final_text='The plugin-a flow has completed. The incoming webhook payload was a ping event—a connectivity check—and it was acknowledged successfully.'
score=1.0
[ok] plugin dispatch verified end to end
[ok] result saved to /home/adam/.local/state/sadana/eval/results/plugin_dispatch__0.json

ALL ASSERTIONS PASSED
```

## Findings

**Compliance verification performed:** `plan.md § Files that change` cross-checked
against the actual diff (one unlisted file found — see Important); every
`plan.md § Proof` item traced to what discharges it; every `spec.md §
Acceptance criteria` item matched to the file/test that satisfies it;
`spec.md § Rejected alternatives` checked against the diff for drift (none
found); the five design principles checked explicitly (summarized below).
Delegated to a fresh subagent with no prior context on this codebase, given
only the diff and the three artifacts, per this stage's "review cold"
instruction.

### Important

- [Compliance] `CLAUDE.md` is modified (two new bullets appended to "Please
  do", `CLAUDE.md:43-44`) but is not listed in `plan.md` § Files that change,
  and `spec.md` never names `CLAUDE.md` as a file this item plans to edit —
  it only cites existing rules there. `CLAUDE.md` is project-wide policy, not
  this work item's own artifact; landing two new binding rules through an
  unplanned diff means they never went through the same Files-that-change
  scrutiny plan.md's other listed changes did.
  **Outcome: fixed.** `plan.md` § Files that change now lists `CLAUDE.md`
  under Documentation, naming both bullets and why they're project-wide
  policy rather than incidental. The artifact now matches what the diff
  actually contains, closing the gap this finding named — done while D1 is
  still open, not as an edit to a closed item's artifact (CLAUDE.md's own
  rule against that applies only after Decision records approval).
- [Compliance] The second new `CLAUDE.md` bullet — "A classified failure a
  caller must branch on is returned as one of a named, closed set of outcome
  types — never raised as an exception" — misdescribed what this item
  actually built. That sentence named a discriminated-union/sum-type shape
  (`model_access.classify()`'s six named `Outcome` values, the analog
  `spec.md` § Rejected alternatives cites at `spec.md:229-232`). `DagResult`
  is not that: it is one frozen dataclass used for both success and failure,
  distinguished only by whether `failed_node` is `None`
  (`src/sadana/plugins.py:58`) — one type, not a named closed set of types.
  `spec.md`'s own Rejected-alternatives text even flags this precisely while
  rejecting the JSON-string alternative, faulting it because "success and
  failure remain the same static type" (`spec.md:226`) — the chosen
  `DagResult` design has that identical property.
  **Outcome: fixed.** `CLAUDE.md:44` now reads: "A classified outcome whose
  branches carry meaningfully different data — model_access.classify()'s
  Outcome today, more as they land — is a named, closed set of outcome
  types, never a raised exception. A result whose branches differ only by
  one field, like DagResult, doesn't need this shape." This keeps the rule
  accurate for what already exists (`classify()`), states it as forward
  guidance for what doesn't exist yet (a future check like D2's manifest
  validator), and explicitly excludes `DagResult` instead of implying it
  follows a pattern it was deliberately designed not to.

### Nits

None raised beyond the two Important findings above — the reviewing
subagent found the rest of the diff clean enough that padding to five nits
would have diluted the two real findings, per this stage's own rule against
letting nits outnumber Important findings.

### Passes checked and found clean

- **Bugs.** Traced all three `dispatch` call sites
  (`src/sadana/conversation.py:748-1334`), every `DagResult` producer across
  the eleven touched files, and the exception path (`run_turn`'s
  `except Exception as e: result_text = f"tool_error: {e}"`,
  `src/sadana/conversation.py:891-892`, unchanged, correctly bypasses
  `dag_result` on a raise). No stale `raw_result`/`str(raw_result)` code
  remains (only a test docstring mentions the old name, as prose). No
  leftover `Awaitable[str]` dispatch-shaped declaration anywhere in `src/`,
  `scripts/`, or `tests/` (grep-confirmed). Dataclass field ordering is
  valid. `plugin_b_entry`'s `text=f"plugin-b: {result.final_text}"`
  (`scripts/prove_conversation_e2e.py:229`) stays non-empty even if
  `result.final_text` is itself empty, because of the literal prefix, so the
  "always non-empty text" invariant holds. No logic error, broken edge case,
  or regression found.
- **Security.** No secrets, credentials, or new I/O surface. `DagResult`
  echoes caller-supplied tool names back in the unknown-tool fallback
  branches (`scripts/prove_conversation_e2e.py:237`,
  `src/sadana/eval_harness.py:64-75`), identical to the reflection the old
  `f"tool_error: unknown tool {name!r}"` string already did — no new
  exposure. This item is pure-data (frozen dataclasses, no disk/network/clock
  access, matching `src/sadana/plugins.py:10-13`'s own docstring claim),
  so it doesn't widen what the process can reach.
- **Compliance — acceptance criteria.** All satisfied and traceable:
  `src/sadana/plugins.py` exports `Artifact`/`NodeTrace`/`DagResult`, all
  `frozen=True` (`src/sadana/plugins.py:21-58`), each proven by
  `tests/unit/test_plugins.py:23-27,44-47,88-91` asserting
  `FrozenInstanceError` on mutation. `run_turn`/`take_turn`/`run_child` all
  declare `Awaitable[plugins.DagResult]`
  (`src/sadana/conversation.py:751,1114,1334`); no remaining
  `Awaitable[str]` dispatch declaration anywhere in `src/` (grep-confirmed).
  `run_turn`'s loop reads `dag_result.text`, no `str()` call
  (`src/sadana/conversation.py:889-890`), proven by the new
  `tests/unit/test_conversation.py:996-1013` assertion that
  `"DagResult(" not in tool_messages[0].content`. `prove_conversation_e2e.py`'s
  `dispatch` closure returns a `DagResult` with non-empty `text` on all
  three branches. `docs/reference/dispatch_closure_state_bug.md` carries the
  new `## Resolution (D1-dagresult-dispatch)` section
  (lines 171-195), matching the existing resolution section's style. No file
  under `docs/tasks/CONV-10-dispatch-parent-propagation/` touched. No `Any`
  or `# type: ignore` introduced anywhere in the touched files.
- **Compliance — rejected alternatives.** No drift found. No
  JSON-string-in-`text` encoding (the `json.dumps` calls present are
  pre-existing and unrelated to `DagResult`). No dual signature or
  transitional shim — all eleven files carrying a dispatch-shaped callable
  moved together in one diff, matching plan.md's own eleven-file claim
  exactly (including its correctly-excluded `test_model_access.py` false
  positive). No `metadata: dict[str, Any]` escape hatch on any of the three
  dataclasses.
- **Compliance — five design principles.** (1) Prior art (hermes's
  `tools/registry.py dispatch()` / `hermes_cli/plugins.py dispatch_tool()`)
  is read and explicitly declined in `spec.md` § Rejected alternatives. (2)
  No expensive-to-reverse decision beyond what spec sanctioned, other than
  the `CLAUDE.md` policy addition flagged above as Important. (3) No
  behavior added that belongs in a plugin, no seam invented ahead of a
  loader — `DagResult` stays pure data, consistent with the Non-goals
  section. (4) The one step made heavier (the dispatch call/read) is exactly
  what `spec.md`'s guideline-3 reasoning defends, not a hidden new step
  elsewhere. (5) No mutable state — all three dataclasses are
  `frozen=True`; nothing introduced a counter, cache, or lock.

### Raised, not findings

`spec.md` § Rejected alternatives critiques the JSON-string-in-`text`
option for leaving success and failure "the same static type," but the
chosen `DagResult` design has that same property (one frozen dataclass,
`failed_node: str | None` as the only success/failure signal). That tension
was decided at the design stage, not introduced by this diff, so it isn't a
defect in the diff — but it's the same underlying tension behind the second
Important compliance finding above, and worth a maintainer's eyes if a
future item leans on "closed set of outcome types" as though `DagResult`
already were one.

## Decision

Approved by Adam, 2026-09-07, with both Important findings fixed in this
branch before approval: `CLAUDE.md`'s second new bullet reworded so it no
longer misdescribes `DagResult`, and `plan.md` § Files that change updated
to list `CLAUDE.md`.
