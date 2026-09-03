# Spec: A conversation's run knows when it has used up its allotted actions or time

Intent: docs/tasks/C4-budgets/intent.md

## Requirements

1. A conversation's run can be asked, at any point, whether it has any
   allotted actions left; using one up says plainly, in the return value
   itself, when there are none. Traces to intent's Proposed outcome.
2. A conversation's run can be asked, at any point, how much of its
   allotted wall-clock time — if any was configured — is left. Traces to
   intent's Proposed outcome and Constraints ("only the specific numbers
   this work item's own mechanism actually reads").
3. Wherever the iteration cap's or wall-clock budget's number comes from,
   it is resolved in exactly one place, using this project's own existing
   per-block config pattern. Traces to intent's Proposed outcome ("resolved
   in exactly one obvious place") and Problem (the reference project's
   config-vs-code drift incident, verified below).
4. No allotment, once used, is ever given back. Traces to intent's
   Constraints.
5. Nothing here assumes more than one caller touches the same run's budget
   at the same time. Traces to intent's Constraints.
6. Provable entirely on its own — no live conversation, turn loop, or
   second conversation needs to exist for any of this to be tested. Traces
   to intent's Constraints.
7. Only the two config values this mechanism actually reads are
   introduced — nothing else from the blueprint's bundled config block.
   Traces to intent's Constraints and "Changed during planning."

## Design

**Where this lives.** `src/sadana/conversation.py` — the same file C2 and
C3 already extended, per B1/B2's one-flat-module-per-block convention.
This is the third work item to add to it without reconsidering that
choice.

**Learning from the reference (guideline 1).** Blueprint §2.2 already
summarizes `../hermes-agent/agent/iteration_budget.py` (62 lines,
dependency-free, lock-protected consume/refund) — read in full rather than
trusted at that summary level, per the audit methodology C2 and C3 both
used.

- The file is exactly what the summary says: `IterationBudget` is a
  mutable class (`agent/iteration_budget.py:17-59`) holding `max_total`,
  `_used`, and a `threading.Lock` (`:35`), with `consume()` (`:37-43`),
  `refund()` (`:45-49`), and `used`/`remaining` properties (`:51-59`), all
  four methods acquiring the same lock.
- **The config-vs-code drift blueprint §2.2 names is real, verified by
  reading both sides of it, not just the summary.**
  `agent/iteration_budget.py`'s own module docstring and class docstring
  (`:5`, `:21`) both say the parent agent's cap defaults to 500. But the
  constructor that actually seeds it,
  `agent/agent_init.py:545` —
  `max_iterations: int = sys.maxsize,  # Default: unlimited tool-calling
  iterations (shared with subagents)` — sets the real default to
  `sys.maxsize`, i.e. effectively unlimited, with its own inline comment
  confirming that's deliberate, not a typo. The number a person reading
  the class's docstring would believe is enforced, and the number the
  constructor actually passes when nothing overrides it, have quietly
  disagreed since whenever that comment was written. This is intent.md's
  Problem statement's direct evidence, not a hypothetical.
- **`refund()`** (`:45-49`) decrements `_used` by exactly one, and its own
  docstring (`:28-29`, `:46`) ties it to one specific caller category:
  `execute_code` (programmatic tool calling) turns. sadana has no
  `execute_code`-equivalent tool, or any tool at all yet — EXECUTION is
  unbuilt. Blueprint §2.2 already says to drop it and document the
  removal "so nobody re-adds it 'for parity'"; this spec does exactly
  that (see Non-goals).
- **The lock** (`:14`, `:35`, and acquired in all four methods) exists
  because hermes's `AIAgent` is a long-lived, mutable object one or more
  threads can call into concurrently — blueprint §5.6 already names this
  design ("hermes's sync-core-plus-threads design is where `async_utils.py`
  ... came from") as something this project's async-first loop replaces,
  specifically because async-first removes the concurrent-mutation
  scenario a lock exists to guard. Declined here for that reason — see
  Rejected alternatives, not adopted and then removed as dead weight.

**`IterationBudget`** — an immutable value, not hermes's mutable-plus-lock
shape (Rejected alternatives has the full argument):

```python
@dataclass(frozen=True)
class IterationBudget:
    max_total: int
    used: int = 0

def consume_iteration(budget: IterationBudget) -> IterationBudget | None:
    """One more iteration used, or None if `budget` was already at
    `max_total`. Never raises — an exhausted budget is a normal outcome of
    a run, not a broken invariant."""
```

`consume_iteration` returns a new value with `used` incremented by one, or
`None` when `budget.used >= budget.max_total`. This mirrors exactly how
blueprint §6 already describes the turn loop's own `MODEL_CALL` step:
"`budget.consume()` or exit `BUDGET_EXHAUSTED`" — a two-outcome check the
loop branches on directly, not an exception it has to catch.

**`WallClockBudget`** — asymmetric with `IterationBudget` on purpose:
nothing "consumes" wall-clock time the way an action consumes an
iteration; time passes on its own, and a caller only ever *checks* how
much is left. So there is no `consume`-shaped function for it, only a
read:

```python
@dataclass(frozen=True)
class WallClockBudget:
    deadline: float  # time.monotonic()-scale; see the Concerns note below

def wall_clock_remaining(budget: WallClockBudget, now: float) -> float:
    """Seconds left before `budget.deadline`, given the caller's own `now`.
    Possibly negative once exhausted — a caller compares the result to 0
    itself rather than this module deciding what "exhausted" means for it."""
```

`now` is an explicit parameter, never read internally from `time.monotonic()`
— testing-conventions forbids a unit test touching the wall clock, and this
makes `wall_clock_remaining` a plain function of its inputs a test can call
with any `now` it likes, satisfying that rule by construction rather than
by faking the clock (testing-conventions' own distinction: "Pure functions
that take environment as data are fine... Patching a module-level flag...
is a fake").

**Config integration — two values, resolved inline, nothing added to
`config.py`.** `config.py`'s own docstring states it "ships two things,
and is meant to never need a third": the `env*` primitives, and
`Paths`/`get_paths()`. `model_access.py:258` already shows the established
pattern for a block's own config: `config.env_int("SADANA_MODEL_ACCESS_MAX_RETRIES",
3)`, called inline at the point of use — no separate schema object.
Blueprint §5.7's YAML (`conversation: max_iterations: 60, run_budget_seconds:
null, child: {...}, tool_results: {...}, timeouts: {...}`) doesn't
translate literally into this project's config shape; it translates into
two inline calls, following the same pattern, in the same module that
needs them:

```python
def iteration_budget_from_config() -> IterationBudget:
    max_total = config.env_int("SADANA_CONVERSATION_MAX_ITERATIONS", 60)
    if max_total < 0:
        raise ValueError(f"SADANA_CONVERSATION_MAX_ITERATIONS={max_total} must not be negative")
    return IterationBudget(max_total=max_total)

def wall_clock_budget_from_config(now: float) -> WallClockBudget | None:
    seconds = config.env_int("SADANA_CONVERSATION_RUN_BUDGET_SECONDS", 0)
    if seconds <= 0:
        return None
    return WallClockBudget(deadline=now + seconds)
```

`60` matches blueprint §5.7's own `max_iterations: 60` — a number the
blueprint fixed for this project independently of hermes's own
(disputed) default, so there is nothing to reconcile between the two.
`run_budget_seconds: null` (blueprint's "no limit by default") becomes
`0` read through the *existing* `env_int` primitive — `0` is not a
meaningful wall-clock budget (an immediately-exhausted run), so it is
free to mean "disabled" without an `Optional`-typed primitive `config.py`
doesn't have. A negative configured iteration cap raises immediately,
rather than producing a budget nobody could ever consume from and no
clear reason for — matching `config.py`'s own stated philosophy ("a bad
value should fail loudly at the one call site that reads it").

**Why `IterationBudget | None` is enough to catch misuse (guideline 3).**
The central choice here — return `None` on exhaustion rather than raise —
looks, at first glance, easier to ignore than `TranscriptInvariantError`'s
raise. It isn't, because of where the catch actually happens: `make
verify` runs `make typecheck` (mypy), and a caller that uses the result of
`consume_iteration` without first ruling out `None` (`budget.used` on an
`IterationBudget | None`) is a type error, caught before the code ships,
not a runtime exception caught only if execution happens to reach it. This
is the cheapest of the three moves this scenario could be caught by:
"make a step harder" (require the None case to be handled, enforced at
build time) beats both adding a runtime validation step and making every
caller heavier with a try/except.

## Interface

**In:** `consume_iteration` takes an `IterationBudget`. `wall_clock_remaining`
takes a `WallClockBudget` and a `now: float`. `iteration_budget_from_config`
takes nothing (reads the environment). `wall_clock_budget_from_config`
takes a `now: float`.

**Out:** `consume_iteration` returns `IterationBudget | None`, never
raises. `wall_clock_remaining` returns `float`, never raises.
`iteration_budget_from_config` returns `IterationBudget`, or raises
`ValueError` for a negative configured cap (propagated from
`config.env_int`'s own type-parse failures too — this module adds no new
exception type). `wall_clock_budget_from_config` returns
`WallClockBudget | None`, never raises.

## Acceptance criteria

- [ ] `consume_iteration` on a budget below `max_total` returns a new
      `IterationBudget` with `used` one higher; the input is unchanged
      (frozen dataclass, provable by re-reading its fields after the
      call).
- [ ] `consume_iteration` on a budget at `used == max_total` returns
      `None`.
- [ ] `consume_iteration` on a budget with `max_total == 0` returns `None`
      immediately (no iteration is ever grantable).
- [ ] `wall_clock_remaining` returns a positive number when `now` is
      before `budget.deadline`, and a non-positive number when `now` is at
      or after it — computed purely from its two arguments, no call to
      `time.monotonic()` anywhere in the function.
- [ ] `iteration_budget_from_config` reads `SADANA_CONVERSATION_MAX_ITERATIONS`,
      defaults to 60 when unset, and raises `ValueError` for a negative
      value.
- [ ] `wall_clock_budget_from_config` reads
      `SADANA_CONVERSATION_RUN_BUDGET_SECONDS`, returns `None` when unset
      (default 0) or set to 0, and returns a `WallClockBudget` whose
      `deadline` is `now + seconds` for any positive value.
- [ ] No test in `tests/unit/test_conversation.py` reads the real
      environment or the real wall clock — every config-reading test uses
      `monkeypatch.setenv`/`monkeypatch.delenv`, and every `now` is a
      literal float the test chooses.
- [ ] Every test is provable with no turn loop, tool dispatch, transcript,
      tool surface, or provider code — these two types and four functions
      stand alone.

## Non-goals

- `refund()`, or anything like it — no caller in this project has a
  reason to give back a consumed iteration; hermes's only existed for
  `execute_code`, a tool sadana doesn't have.
- Any of blueprint §5.7's other config keys — `child.max_iterations`,
  `child.max_depth` (CONV-07's job, once child conversations exist),
  `tool_results.*` (CONV-05's tool round), `timeouts.*` (CONV-04/CONV-05).
- A live `Conversation` object calling either `_from_config()` factory —
  nothing does yet; that's CONV-06's job. These functions existing with no
  caller is this project's Phase 1 working as intended, same as CONV-01
  through CONV-05 having none when they land (blueprint §7's own closing
  line).
- Any notion of "80% consumed, append a system notice to the transcript"
  (blueprint §4.5) — that behavior belongs to whatever calls
  `wall_clock_remaining` from inside the turn loop (CONV-05), not to the
  budget types themselves.
- Locking, thread-safety, or any other concurrent-access protection — see
  Rejected alternatives.

## Rejected alternatives

**Hermes's mutable class plus `threading.Lock`, declined.** Read directly
(`agent/iteration_budget.py:14,35,39,47,53,58`) rather than assumed from
the blueprint's summary. The lock protects one `IterationBudget` instance
from concurrent mutation by more than one thread — a real scenario in
hermes's sync-core-plus-threads `AIAgent`, and a scenario blueprint §5.6
already designs out of this project by making the loop async-first
(`async_utils.py`'s own verdict in the blueprint's appendix: "reject —
async-first removes the need"). An immutable value with a
consume-returns-new-or-None function, the same shape C2 used for
`append`/`repair` and C3 used for `build_surface`/`filter_surface`, has no
shared mutable state for a lock to protect — the question doesn't need
deciding, because there's nothing left for a lock to be about.

**`refund()`, declined.** Blueprint §2.2 already gives this verdict
explicitly ("drop `refund` and document that removal so nobody re-adds it
'for parity'"); this spec's Non-goals is that documentation. Confirmed by
reading `agent/iteration_budget.py:28-29,45-49` that it exists for exactly
one caller category (`execute_code` turns) this project has no equivalent
of.

**All of blueprint §5.7's config keys, introduced now, declined.** Decided
during the Plan interview: `child.*`, `tool_results.*`, and `timeouts.*`
would be state with no reader — the same "no reader yet" reasoning C2
applied to declining `TurnKey` and C3 applied to declining a `handler`
field on `ToolSpec`. Each gets introduced, inline, by whichever later work
item first reads it, following the same pattern this one establishes.

**A new `env_optional_int`-style primitive on `config.py`, declined.**
`config.py`'s own docstring states an intent to never need a third
category of thing beyond its `env*` primitives and `Paths`/`get_paths()`.
Representing "no wall-clock budget configured" as `0`, read through the
*existing* `env_int`, avoids the question — `0` seconds is not a coherent
budget to grant regardless of what it might otherwise mean, so it is free
to be the sentinel.

**Raising `BudgetExhaustedError` instead of returning `None`, declined.**
Argued in Design above: mypy, run as part of `make verify`, already forces
every caller to handle the `None` case before the code ships — a cheaper
and earlier catch than a runtime exception, and consistent with
MODEL-ACCESS's own established pattern of representing an expected
terminal outcome (`Abort`, `Degenerate`, …) as a value rather than an
exception.

## Concerns

**`WallClockBudget.deadline` and every `now` passed to
`wall_clock_remaining`/`wall_clock_budget_from_config` must come from the
same clock, consistently — `time.monotonic()`, never `time.time()`.**
Nothing in this module enforces that; it's a documented caller
responsibility (both functions' docstrings say so), the same kind of
contract `MessageKey.msg_seq` or `surface_hash` place on their callers
without a runtime check. If a future caller mixes wall-clock and monotonic
sources, the resulting "remaining" numbers are meaningless but not
detectably wrong — flagging this now so CONV-05, the first real caller,
inherits the warning rather than rediscovering it.

**No unresolved policy conflict.** `testing-conventions` is the only
applicable policy skill, and this design satisfies its wall-clock
restriction by construction (`now` as data, per its own "pure functions
that take environment as data are fine" carve-out) rather than by faking
`time.monotonic()`. `project-structure` and `reference-lookup` still don't
exist in this project, stated explicitly as C2 and C3 both did.
