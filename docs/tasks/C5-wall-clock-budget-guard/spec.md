# Spec: A negative time allotment is refused, not silently ignored

Intent: docs/tasks/C5-wall-clock-budget-guard/intent.md

## Requirements

1. `wall_clock_budget_from_config` raises for a negative configured value,
   the same way `iteration_budget_from_config` already does for its own
   sibling setting. Traces to intent's Proposed outcome.
2. Zero and unset keep their existing meaning ("no time limit") — only
   negative changes. Traces to intent's Constraints.

## Design

**Learning from the reference (guideline 1).** `docs/reference/conversation_block_blueprint.md`
§5.7 fixes `run_budget_seconds: null` as the default but says nothing
about a negative value one way or the other — there is nothing in the
blueprint to reconcile against here, only this project's own already-cited
`config.py` philosophy ("a bad value should fail loudly at the one call
site that reads it," quoted directly in C4's own `spec.md`). No further
hermes reading is needed: this fixes an inconsistency between two
functions this project wrote itself, not something hermes's corpus has an
opinion on.

**The fix, in `src/sadana/conversation.py`.** `wall_clock_budget_from_config`
currently reads:

```python
seconds = config.env_int("SADANA_CONVERSATION_RUN_BUDGET_SECONDS", 0)
if seconds <= 0:
    return None
```

Splits into two branches instead of one:

```python
seconds = config.env_int("SADANA_CONVERSATION_RUN_BUDGET_SECONDS", 0)
if seconds < 0:
    raise ValueError(f"SADANA_CONVERSATION_RUN_BUDGET_SECONDS={seconds} must not be negative")
if seconds == 0:
    return None
```

The message mirrors `iteration_budget_from_config`'s existing one
(`conversation.py:280`) exactly in shape — naming the variable and the bad
value — so the two functions read as one policy applied twice, not two
policies that happen to agree.

**Guideline 3 (least step-cost).** The scenario — a negative configured
value reaching a caller as if it meant something sane — is already caught
for the sibling setting by making that one function's read step harder to
misuse (raise instead of silently coerce). This applies the identical
move to the second function, rather than inventing a new mechanism (e.g. a
single shared validation helper both functions call) for what is, after
this change, two three-line guards that happen to look alike. A shared
helper is exactly the kind of thing to introduce once a *third* caller
needs the same shape — not now, for two.

**Guideline 2 (reduce bets) / guideline 4 (minimise mutable state).**
Neither applies beyond what C4 already decided — this changes no type,
adds no state, and touches no plugin seam. Noted rather than skipped.

## Interface

**In:** unchanged — `wall_clock_budget_from_config(now: float)`.

**Out:** unchanged for `seconds > 0` and `seconds == 0`/unset. New:
raises `ValueError` for `seconds < 0`, matching
`iteration_budget_from_config`'s existing error shape.

## Acceptance criteria

- [ ] `wall_clock_budget_from_config` raises `ValueError` for a negative
      `SADANA_CONVERSATION_RUN_BUDGET_SECONDS`.
- [ ] `wall_clock_budget_from_config` still returns `None` for `0` and for
      unset — unchanged from C4.
- [ ] `wall_clock_budget_from_config` still returns a `WallClockBudget`
      for a positive value — unchanged from C4.
- [ ] The existing C4 tests for this function all still pass unmodified.

## Non-goals

- A shared validation helper between the two `_from_config` functions —
  declined per guideline 3 above; revisit only if a third caller needs the
  same shape.
- Anything about `iteration_budget_from_config` itself — already correct,
  untouched.

## Rejected alternatives

**A shared `_positive_or_none`/`_non_negative` helper, declined.** Two
call sites with the same three-line shape is not yet a duplication worth
naming — see guideline 3 in Design. Revisit if a third `_from_config`
function (a future work item's) needs the same check.

## Concerns

None. This is a one-function, two-line change to a function C4 already
specified and tested; the only new behavior (raising on negative) matches
an existing sibling exactly, and every existing test for the unchanged
paths (`0`, unset, positive) still applies without modification.
`testing-conventions` is the only applicable policy skill and this change
introduces no new I/O, clock use, or fixture need beyond what C4's own
tests already established.
