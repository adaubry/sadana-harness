# Plan: A conversation's run knows when it has used up its allotted actions or time (from intent.md 2026-09-03)

## Files that change

- `src/sadana/conversation.py` — same file C2 and C3 both extended; this
  work item appends to it, not a new file. New additions:
  `IterationBudget` (frozen dataclass: `max_total`, `used=0`),
  `consume_iteration(budget)`, `WallClockBudget` (frozen dataclass:
  `deadline`), `wall_clock_remaining(budget, now)`,
  `iteration_budget_from_config()`, `wall_clock_budget_from_config(now)`.
  New import: `from sadana import config`.
- `tests/unit/test_conversation.py` — same file C2/C3 both extended; new
  tests appended, one per `spec.md` acceptance criterion, using
  `monkeypatch.setenv`/`delenv` for the two config-reading tests (no real
  environment or wall clock touched, per testing-conventions).

## Design recap (from spec.md, restated so this plan stands alone)

```python
@dataclass(frozen=True)
class IterationBudget:
    max_total: int
    used: int = 0

def consume_iteration(budget: IterationBudget) -> IterationBudget | None:
    """One more iteration used, or None if already at max_total. Never raises."""

@dataclass(frozen=True)
class WallClockBudget:
    deadline: float  # time.monotonic()-scale

def wall_clock_remaining(budget: WallClockBudget, now: float) -> float:
    """Seconds left before deadline, given the caller's own now. Pure — no
    time.monotonic() call inside."""

def iteration_budget_from_config() -> IterationBudget:
    """Reads SADANA_CONVERSATION_MAX_ITERATIONS (default 60). Raises
    ValueError if the configured value is negative."""

def wall_clock_budget_from_config(now: float) -> WallClockBudget | None:
    """Reads SADANA_CONVERSATION_RUN_BUDGET_SECONDS (default 0). Returns
    None when 0 or unset; otherwise WallClockBudget(deadline=now+seconds)."""
```

`consume_iteration`: `budget.used >= budget.max_total` → `None`;
otherwise `replace(budget, used=budget.used + 1)` (a new value, input
unchanged since the dataclass is frozen). No lock, no `refund` — matches
`spec.md`'s Rejected Alternatives against hermes's mutable
class-plus-`threading.Lock` shape (the lock existed for hermes's
sync-plus-threads `AIAgent`, a design this project's async-first loop
already replaces) and against carrying `refund` forward for a tool
(`execute_code`) sadana doesn't have.

`iteration_budget_from_config`/`wall_clock_budget_from_config` call
`config.env_int(...)` inline, exactly matching
`model_access.py:258`'s established pattern — no new function added to
`config.py` itself.

## Order of work

1. **Add `IterationBudget`, `consume_iteration`, `WallClockBudget`,
   `wall_clock_remaining`, `iteration_budget_from_config`,
   `wall_clock_budget_from_config` to `src/sadana/conversation.py`**,
   after the existing CONV-02 content (`surface_hash`), with a
   `# ── CONV-03: budgets ──` section comment matching the style of the
   CONV-01/CONV-02 section markers already in the file. Add
   `from sadana import config` to the imports (new — `conversation.py`
   hasn't needed `config` until now) and `from dataclasses import replace`
   (or reuse the existing `dataclass` import and construct a new instance
   directly — whichever reads more plainly at the call site).
2. **Append tests to `tests/unit/test_conversation.py`**, one per
   `spec.md` acceptance criterion, under a new `# ── budgets ──` section.
   Run `make test` scoped to this file first, then the full narrow check.
3. **Run `make verify`** and paste its output as this stage's evidence.

## Risks

**What could this change break?** Nothing existing. All six new names are
pure additions — no existing function in `conversation.py`
(`pending_tool_call_ids`, `append`, `repair`, `build_surface`,
`filter_surface`, `surface_hash`) is touched, and this is the first thing
in `conversation.py` to import `config`, but `config.py` itself is
untouched (its own docstring's "never need a third thing" promise stays
intact). The two config-reading tests are the first in
`tests/unit/test_conversation.py` to use `monkeypatch` at all — worth
double-checking they actually clean up via `monkeypatch`'s own
teardown (automatic, scoped to the test) rather than leaking an env var
into a later test in the same file.

**Which step is riskiest?** The config-reading tests
(`iteration_budget_from_config`/`wall_clock_budget_from_config`) — getting
the `monkeypatch.setenv`/`delenv` calls wrong (e.g. forgetting to test
both "unset" and "explicitly set to a valid value" for each) would leave
the default-value path or the override path unverified while the test
suite still passes. Mitigation: the test list below states both cases for
each function explicitly, not folded into one test each.

**Is this drifting back toward anything `spec.md` already rejected?**
Checked against all five Rejected Alternatives entries: no mutable class,
no `threading.Lock` import, no `refund` method or function, no
`child.*`/`tool_results.*`/`timeouts.*` config keys introduced, no new
primitive added to `config.py`, no exception raised on budget exhaustion
(`consume_iteration` returns `None`, never raises).

## Proof

`tests/unit/test_conversation.py` covers, at minimum, one test per
`spec.md` acceptance-criteria checkbox:

- `consume_iteration` on a budget below `max_total` returns a new
  `IterationBudget` with `used` one higher; the original input's fields
  are unchanged after the call.
- `consume_iteration` on a budget at `used == max_total` returns `None`.
- `consume_iteration` on a budget with `max_total == 0` returns `None`
  immediately.
- `wall_clock_remaining` returns a positive number when `now` is before
  `deadline`, and a non-positive number when `now` is at or after it.
- `iteration_budget_from_config` with the env var unset defaults to 60.
- `iteration_budget_from_config` with the env var set to a valid
  non-negative value uses that value.
- `iteration_budget_from_config` with the env var set to a negative value
  raises `ValueError`.
- `wall_clock_budget_from_config` with the env var unset (default 0)
  returns `None`.
- `wall_clock_budget_from_config` with the env var explicitly set to `0`
  returns `None`.
- `wall_clock_budget_from_config` with the env var set to a positive value
  returns a `WallClockBudget` whose `deadline` equals `now + seconds`.
- Every config-reading test uses `monkeypatch.setenv`/`delenv` — no real
  environment variable read or left behind; no test calls
  `time.monotonic()` or `time.time()`.

`make verify` run at the end, pasted in full, ending `VERIFY OK`, is this
stage's evidence.
