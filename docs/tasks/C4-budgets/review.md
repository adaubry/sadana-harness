# Review: A conversation's run knows when it has used up its allotted actions or time (from plan.md 2026-09-03)

Reviewed: 7e4f71f..HEAD (working tree) — 5 files, +660/-1
Reviewer context: same session as build — cold-read requirement satisfied by
delegating the actual review passes (steps 1-5 below) to a fresh subagent
with no prior context beyond intent.md/spec.md/plan.md and the diff; this
session performed the file-list scoping, the second-opinion commands, and
the write-up.
Second opinion: /ponytail-review and /simplify — both run (performed
directly in-session), after the cold review's findings were already
written down.

## Evidence

```
docs/tasks/C4-budgets: all present artifacts valid
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
check that scripts with shebangs are executable............................Passed
check that executables have shebangs........................................Passed
detect private key..........................................................Passed
ruff.........................................................................Passed
ruff-format...................................................................Passed
shellcheck.....................................................................Passed
Detect secrets.................................................................Passed
LINT OK
Success: no issues found in 4 source files
TYPES OK
▶ hermetic run: TZ=UTC LANG=C.UTF-8 PYTHONHASHSEED=0, empty env
........................................................................ [ 82%]
...............                                                          [100%]
87 passed in 0.31s
TESTS OK
VERIFY OK
```

## Findings

One Important finding, a real gap in the spec itself rather than a
mismatch between spec and code — every acceptance criterion, every Proof
bullet, and all five Rejected Alternatives held up clean, including a
direct read confirming `IterationBudget | None` is genuinely the
annotation `make typecheck` enforces, not just an aspirational claim.

### Important

- [Compliance/Bugs — a spec gap, not a code-vs-spec mismatch]
  `wall_clock_budget_from_config` (`conversation.py:298-301`) silently
  treats a *negative* `SADANA_CONVERSATION_RUN_BUDGET_SECONDS` as
  "disabled" (`if seconds <= 0: return None`), identical to unset or `0`.
  `iteration_budget_from_config` (`conversation.py:279-281`) handles the
  same shape of bad input oppositely — it raises `ValueError` naming the
  bad value. `spec.md`'s own Design section quotes `config.py`'s stated
  philosophy directly ("a bad value should fail loudly at the one call
  site that reads it," spec.md:159-160) but `spec.md` itself only ever
  discusses `0` and unset for the wall-clock case — a negative value was
  never put to the fail-loudly test during Design, so this isn't a
  deviation from spec.md, it's a case spec.md didn't cover. Confirmed by
  both the cold review (Bugs pass) and independently by this session's own
  altitude pass (`/simplify`'s fourth angle): the two `_from_config`
  functions apply the same policy inconsistently, which is exactly the
  "special case where a shared mechanism should apply uniformly" shape an
  altitude review looks for.

### Checked and found clean

- **Boundary-value pass** (adversarial, via direct reasoning and reading,
  not just names): `used == max_total - 1`, `used == max_total`,
  `max_total == 0`, and (via direct construction, not reachable through
  any real caller) `used > max_total` and negative `used` — all degrade
  correctly to either an incremented budget or `None`, no crash, no
  under/overflow. `wall_clock_remaining` at exact equality
  (`now == deadline`) returns `0.0`, correctly non-positive.
  `iteration_budget_from_config`'s negative guard fires *after*
  `env_int`'s own successful integer parse (`int("-1")` parses fine, then
  this module's own `< 0` check raises) — a genuinely non-numeric string
  still fails inside `env_int` itself, propagating unchanged, matching
  spec.md's Interface section exactly.
- **`consume_iteration` non-mutation**: uses `dataclasses.replace`
  (`conversation.py:257`), not a manual field-by-field reconstruction —
  forwards every field including any added later, and cannot mutate the
  frozen input in place. `test_consume_iteration_below_max_returns_incremented_budget`
  asserts the original object's value is unchanged after the call.
- **Security pass**: pure in-memory dataclass arithmetic plus
  `os.environ` reads through `config.py`'s existing, unmodified
  primitives — no new I/O, no new subprocess/network/filesystem surface,
  no secret material touched.
- **File-list check**: diff touches exactly `conversation.py`,
  `test_conversation.py`, and the three `docs/tasks/C4-budgets/` artifact
  files — matches `plan.md` exactly. `src/sadana/config.py` is completely
  absent from the diff, confirmed untouched.
- **`spec.md` § Acceptance criteria** (all 8) and **`plan.md` § Proof**
  (all 11): every one discharged by a specifically-matched test, verified
  by reading each test's body — `test_consume_iteration_below_max_returns_incremented_budget`,
  `test_consume_iteration_at_max_returns_none`,
  `test_consume_iteration_zero_max_returns_none_immediately`,
  `test_wall_clock_remaining_positive_before_deadline`,
  `test_wall_clock_remaining_non_positive_at_or_after_deadline`,
  `test_iteration_budget_from_config_defaults_to_60`,
  `test_iteration_budget_from_config_uses_env_override`,
  `test_iteration_budget_from_config_raises_for_negative`,
  `test_wall_clock_budget_from_config_defaults_to_none`,
  `test_wall_clock_budget_from_config_explicit_zero_is_none`,
  `test_wall_clock_budget_from_config_positive_value`.
- **`spec.md` § Rejected alternatives re-check** (all 5): grepped
  directly for `threading`/`Lock` (only a comment explaining the
  decline, no actual use), `refund` (prose/comment only, no callable),
  and `child`/`tool_results`/`timeouts.` (zero matches) — none crept back
  in. `consume_iteration`'s return annotation is literally
  `IterationBudget | None` with no `raise` in its body.
- **Design principles**: reference-corpus learning (code matches what
  spec.md said it would build against the verified hermes drift),
  reducing bets (no lock/thread-safety machinery), catch-at-least-step-cost
  (the `IterationBudget | None` annotation genuinely is what `make
  typecheck` — which passed clean — would enforce against an unchecked
  caller), minimizing mutable state (both types frozen, nothing cached).
  All clean.
- `/ponytail-review`: "Lean already. Ship." — no complexity findings.

## Decision

Approved by Adam, 2026-09-03, with the wall-clock negative-value gap
deferred to a followup work item (C5-wall-clock-budget-guard) rather than
fixed in this branch — no other Important findings; `make verify` stayed
green throughout.
