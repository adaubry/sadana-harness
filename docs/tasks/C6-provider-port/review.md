# Review: Asking a model for a real response never requires trusting an unkept promise (from plan.md 2026-09-03)

Reviewed: 65f02ee..HEAD (working tree) — 5 files, +1038/-1
Reviewer context: same session as build — cold-read requirement satisfied by
delegating the actual review passes (steps 1-5 below) to a fresh, explicitly
adversarial subagent, given this is the largest and highest-risk work item
in the project so far. It read the ported hermes source line-by-line
alongside the port, not just spec.md's summary of it.
Second opinion: /ponytail-review and /simplify — both run, after the cold
review's findings were already written down.

## Evidence

Pre-fix (as first reviewed, 107 tests):

```
docs/tasks/C6-provider-port: all present artifacts valid
CHAIN OK
[... all pre-commit hooks Passed ...]
LINT OK
Success: no issues found in 4 source files
TYPES OK
▶ hermetic run: TZ=UTC LANG=C.UTF-8 PYTHONHASHSEED=0, empty env
........................................................................ [ 67%]
...................................                                      [100%]
107 passed in 0.48s
TESTS OK
VERIFY OK
```

Post-fix (after the Important finding below was fixed, 112 tests):

```
docs/tasks/C6-provider-port: all present artifacts valid
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
shellcheck.................................................................Passed
Detect secrets..............................................................Passed
LINT OK
Success: no issues found in 4 source files
TYPES OK
▶ hermetic run: TZ=UTC LANG=C.UTF-8 PYTHONHASHSEED=0, empty env
........................................................................ [ 64%]
........................................                                 [100%]
112 passed in 0.54s
TESTS OK
VERIFY OK
```

## Findings

One Important finding, a real reproducible bug not caught by any of the 19
shipped tests: `repair_tool_call_arguments` can return a non-`dict` for
valid-but-non-object JSON, breaking its own declared contract. Everything
else — the full hermes-cascade line-by-line comparison, all 9 acceptance
criteria, all 10 Proof bullets, all 4 Rejected Alternatives, ReDoS/security,
and three-consecutive-retry threading — checked clean under adversarial
testing, not just re-reading.

### Important

- [Bugs] `repair_tool_call_arguments` (`conversation.py:379,407,415`)
  returns whatever `json.loads` produces at any of its three success
  points with no `isinstance(result, dict)` guard, violating its own
  `-> dict` signature. Confirmed directly: `repair_tool_call_arguments('[1,2,3]')`
  returns the list `[1, 2, 3]`; `'5'` returns `5`; `'"a string"'` returns
  `'a string'`; `'null'` returns `None`; `'true'` returns `True`. This is
  not present in hermes, whose equivalent function returns a repaired
  *string*, never a parsed value — the type obligation is new to this
  port's own documented adaptation ("adapted to return a parsed dict
  instead of a repaired string," `conversation.py:369`) and no guard was
  carried over to enforce it. Consequence: a provider sending a
  syntactically-valid-but-non-object `arguments` string (a bare array,
  number, string, null, or bool — plausible from a degraded model) puts a
  non-dict value into `Completion.tool_calls`, breaking the exact promise
  intent.md makes ("arguments already in a usable shape — never raw text
  a caller has to parse or guess at") for any caller that does
  `arguments.get(...)` or `**arguments`. The fix belongs inside
  `repair_tool_call_arguments` itself, at each success point — confirmed
  by this session's own `/simplify` altitude pass: the `dict` contract is
  this function's own to keep, not something a caller downstream should
  have to re-check.

### Nits

- [Bugs, minor robustness note, not a defect against current code]
  `complete()` (`conversation.py:511-527`) has no explicit
  `isinstance(outcome, model_access.Response)` branch — it falls through
  to building a `Completion` after three `isinstance` checks eliminate
  the other five `Outcome` variants. Correct for every variant that
  exists today (confirmed: `mypy` finds no narrowing issue, and direct
  testing of all four terminal branches plus 3-consecutive-`Retry`
  passes), but a 7th, unexpected outcome type would crash with a
  confusing `AttributeError` rather than a clear "unhandled outcome"
  message. Not fixed here — flagged for awareness.

**Outcome:** the Important finding above (non-dict return from
`repair_tool_call_arguments`) was fixed in this branch. Each of the three
success points now checks `isinstance(parsed, dict)` before returning —
a pass that parses valid-but-non-object JSON (a bare array, number,
string, null, or bool) no longer counts as success; the cascade falls
through to the next pass and ultimately to `{}`. Added
`test_repair_tool_call_arguments_rejects_valid_non_object_json`
(parametrized over `[1,2,3]`, `5`, `"a string"`, `null`, `true`) —
112 tests now pass, all green. The Nit (no explicit `Response` branch in
`complete()`) was left unfixed, as noted above.

### Checked and found clean

- **Line-by-line hermes comparison** (not just spec.md's summary):
  `_escape_invalid_chars_in_json_strings` is character-for-character
  identical to hermes's version. `_repair_tool_call_arguments`'s five-pass
  cascade structure, the trailing-comma regex, the bracket-balance
  counting, and the 50-iteration bound are all present in the same order
  with the same bound preserved verbatim. The lint-driven merge of
  hermes's two separate `if/elif` branches (`fixed.endswith("}")...` /
  `fixed.endswith("]")...`) into one `if (A) or (B):` was verified
  behaviorally identical via Python operator precedence and confirmed
  both original branches perform the same action (`fixed = fixed[:-1]`).
  `deterministic_call_id` is byte-identical in formula and confirmed
  deterministic across two separate process invocations (no time/random
  leak). `coalesce_tool_call_id`'s dict-only adaptation matches hermes's
  dict branch exactly, adversarially tested (whitespace-only id, empty
  `call_id` falling through to `id`, a non-string id skipped via
  `isinstance` rather than crashing). `uniquify_tool_call_ids`'s
  simplification (operating on already-resolved dicts instead of raw
  provider objects, since `_resolve_tool_calls` already ran
  `coalesce_tool_call_id`/`deterministic_call_id` first) is a deliberate,
  sound adaptation, not a dropped feature — adversarially tested with 3+
  duplicates and a pre-existing `"x_d2"` id colliding with a synthesized
  suffix, no collision in either case.
- **Security**: the trailing-comma regex has no nested/overlapping
  quantifiers (linear time, no ReDoS shape); the bracket-trim loop is
  bounded to 50 iterations of O(n) work each (O(50n), not exponential),
  confirmed against a 200-character all-brackets adversarial input.
- **`complete()`'s retry loop**: threaded correctly across 3 consecutive
  `Retry` outcomes in a row (attempts observed `[0, 1, 2, 3]`, not just
  the shipped single-retry case), genuinely uses
  `await asyncio.to_thread(model_access.send, request)` (not a blocking
  sync call), and the synthetic system-role message cannot leak into
  `Completion` — structurally guaranteed, since `Completion` has no
  `messages`/`system` field at all.
- **`_resolve_tool_calls`'s per-batch index**: confirmed it counts only
  among calls needing synthesis (not batch position) via a 3-call batch
  with a real id in the middle — synthesized ids got index 0 and 1, not
  0 and 2, matching spec.md's documented design exactly.
- **File-list check**: diff touches exactly `conversation.py`,
  `test_conversation.py`, and the three C6 artifact files — matches
  `plan.md`, no scope creep into `model_access.py` or any provider.
- **All 9 `spec.md` acceptance criteria and all 10 `plan.md` Proof
  bullets**: discharged by name-matched tests, each verified by reading
  the test body.
- **All 4 Rejected Alternatives**: grepped and read directly — no
  `logging`/`logger`, no `tool_call_id_variants`/`tool_result_id_variants`,
  no `ProviderPort` class/Protocol, no second retry-cap config read, no
  `ToolCall` dataclass (`Completion.tool_calls: tuple[dict, ...]` matches
  `Message.tool_calls`'s existing C2 shape exactly, same file).
- `/ponytail-review`: "Lean already. Ship." — the ported cascade is
  deliberately kept faithful to hermes per the Plan interview's own
  explicit choice; nothing to cut without undoing that.
- `/simplify`: no reuse/efficiency finding; the one altitude observation
  (the `dict` guard belongs inside `repair_tool_call_arguments`, not a
  downstream caller) is folded into the Important finding above rather
  than listed separately.

## Decision

Approved by Adam, 2026-09-03, after the Important finding (non-dict
return from `repair_tool_call_arguments`) was fixed in this branch — see
Outcome above. `make verify` green post-fix (112 tests).
