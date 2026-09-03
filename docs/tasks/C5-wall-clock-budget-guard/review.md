# Review: A negative time allotment is refused, not silently ignored (from plan.md 2026-09-03)

Reviewed: 48111aa..HEAD (working tree) — 5 files, +204/-2
Reviewer context: same session as build, done directly rather than
delegated to a fresh subagent — a stated limitation of this review, per
deploy-skill's own allowance, given the change's size (a two-branch split
in one already-tested function, plus one new test).
Second opinion: /ponytail-review and /simplify — both run.

## Evidence

```
docs/tasks/C5-wall-clock-budget-guard: all present artifacts valid
CHAIN OK
[... pre-commit hooks: all Passed ...]
LINT OK
Success: no issues found in 4 source files
TYPES OK
▶ hermetic run: TZ=UTC LANG=C.UTF-8 PYTHONHASHSEED=0, empty env
........................................................................ [ 81%]
................                                                         [100%]
88 passed in 0.34s
TESTS OK
VERIFY OK
```

(Full pre-commit hook list omitted here for brevity — every hook passed;
the complete output is identical in shape to every prior review in this
chain.)

## Findings

No Important findings. This is a two-branch split
(`seconds < 0` raise, `seconds == 0` return `None`) in one function
already fully specified and tested by C4, plus one new test — the
smallest possible diff that closes the gap C4's own review found.

### Checked and found clean

- **Bugs**: boundary values `-1`, `0`, `1` all route correctly — `-1`
  raises before reaching either return path, `0` still returns `None`
  (the `== 0` branch, reached only after the `< 0` branch didn't fire),
  `1` still falls through to `WallClockBudget(deadline=now+seconds)`
  unchanged. The error message mirrors
  `iteration_budget_from_config`'s existing one exactly in shape (env var
  name, the bad value, "must not be negative").
- **File-list check**: diff touches exactly `conversation.py`,
  `test_conversation.py`, and the three `C5` artifact files — matches
  `plan.md`.
- **`spec.md` § Acceptance criteria** (4): negative raises →
  `test_wall_clock_budget_from_config_raises_for_negative`; `0`/unset
  still `None` and positive still returns a budget → the three existing
  C4 tests, confirmed unmodified by the diff (only the function body
  changed, no test file lines were touched for those three); all existing
  C4 tests still pass → 88 total (87 + 1), all green. All 4 discharged.
- **`plan.md` § Proof**: same mapping, all 4 items discharged; `make
  verify` ends `VERIFY OK`.
- **`spec.md` § Rejected alternatives** (1 — a shared validation helper):
  not introduced; the fix is self-contained in the one function it
  changes.
- **`/ponytail-review`**: a two-branch guard added to an already-minimal
  function, one new test matching the existing three's style — nothing to
  cut. "Lean already. Ship."
- **`/simplify`**: no reuse/simplification/efficiency/altitude finding —
  the fix mirrors an existing sibling function's exact shape rather than
  introducing a new one, which is itself the simplest available form.
- **Security**: no new I/O, no new surface — same as C4.

## Decision

Approved by Adam, 2026-09-03, as-is. No Important findings; `make verify`
stayed green throughout.
