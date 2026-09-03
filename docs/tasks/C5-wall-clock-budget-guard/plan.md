# Plan: A negative time allotment is refused, not silently ignored (from intent.md 2026-09-03)

## Files that change

- `src/sadana/conversation.py` — `wall_clock_budget_from_config` gets its
  `if seconds <= 0: return None` split into `if seconds < 0: raise
  ValueError(...)` followed by `if seconds == 0: return None`. No new
  file, no new type.
- `tests/unit/test_conversation.py` — one new test
  (`test_wall_clock_budget_from_config_raises_for_negative`), alongside
  the three existing C4 tests for this function, which must keep passing
  unmodified.

## Order of work

1. Edit `wall_clock_budget_from_config` in `src/sadana/conversation.py`
   per `spec.md`'s Design section (the two-branch split, error message
   mirroring `iteration_budget_from_config`'s existing one).
2. Add the one new test to `tests/unit/test_conversation.py`, next to the
   three existing `wall_clock_budget_from_config` tests. Run `make test`
   scoped to this file.
3. Run `make verify` and paste its output as this stage's evidence.

## Risks

**What could this change break?** The three existing
`wall_clock_budget_from_config` tests (unset, explicit `0`, positive
value) — none of them pass a negative value, so the new `< 0` branch is
never reached by any of them and their existing assertions are untouched.
`iteration_budget_from_config` and every other function in the file are
untouched.

**Which step is riskiest?** None, really — this is a two-line change to
one already-tested function's error handling, with the new branch's
condition (`< 0`) disjoint from every condition the existing three tests
exercise (`== 0` unset-default, explicit `"0"`, a positive value).

**Is this drifting back toward anything `spec.md` already rejected?** No —
`spec.md`'s one Rejected Alternative (a shared validation helper between
the two `_from_config` functions) isn't introduced; this stays a
self-contained two-line change to one function.

## Proof

- `test_wall_clock_budget_from_config_raises_for_negative` — sets
  `SADANA_CONVERSATION_RUN_BUDGET_SECONDS=-1`, asserts
  `wall_clock_budget_from_config` raises `ValueError`.
- The three existing `wall_clock_budget_from_config` tests
  (`test_wall_clock_budget_from_config_defaults_to_none`,
  `_explicit_zero_is_none`, `_positive_value`) pass unmodified.
- `make verify` run at the end, pasted in full, ending `VERIFY OK`.
