# Spec: One real turn proves the whole pipeline is actually right, not just running

Intent: docs/tasks/EVAL-02-one-real-turn/intent.md

## Requirements

1. One task, run once, sends exactly one user prompt to a real model and
   grades what actually happened — no multi-turn sequence. (Intent
   §Proposed outcome, §Constraints.)
2. The behaviour under test is genuine plugin dispatch: whether the model,
   told about a mounted add-on procedure, actually calls it when the
   prompt asks for it — reusing the exact fixture (plugin-a, its skill,
   its child-spawning dispatch handler) CONV-09/CONV-10 already proved by
   hand. (Intent §Changed during planning.)
3. Grading is structural — did the model call the right tool, did the
   spawned child's report come back and get correctly acted on — never a
   second model's opinion. (Intent §Constraints.)
4. This is recorded as a `Task` value in `eval_harness`'s own terms
   (EVAL-01), not a script with inline `assert`s — the artifact this work
   item produces is meant to be run again, not read once and discarded.
   (Intent §Proposed outcome.)
5. Whatever `run_task()` cannot yet do that a real, tool-using task
   needs, gets added in this work item, not worked around. (Intent
   §Constraints.)
6. This does not become part of `make test`. (Intent §Constraints.)

## Design

The one real gap between EVAL-01's mechanism and this task: `run_task()`
hardcodes `dispatch=_no_tools_dispatch`, so no task it runs can offer the
model a real tool. Closing that gap, and building the plugin-dispatch
task itself on top of it, are both in scope, in that order.

### Reference corpus / prior art

No new hermes research needed — this task reuses machinery two closed
work items already audited hermes against (CONV-07's child-spawning
design, CONV-09's fixture-plugin shape) rather than opening a new
question. What *is* worth re-citing: hermes's `evals/core_tool_deferral`
grades structurally (tool-call presence, side-effect checks, partial
credit) rather than string-matching prose (EVAL-01's spec.md §Design
already adopted this; this task is the first one to actually need it, not
just declare it).

### `run_task()` gains a `dispatch_factory` parameter

```python
async def run_task(
    task: Task,
    template: ConversationTemplate,
    *,
    provider: str,
    model: str,
    iteration_budget: IterationBudget,
    now: float,
    key: ConversationKey | None = None,
    dispatch_factory: Callable[[Conversation], Callable[[str, dict], Awaitable[str]]] = _no_tools_dispatch_factory,
) -> TaskRun:
```

A factory, not a bare dispatch callable — corrected during this work
item's own self-check from an initial draft that took a plain `dispatch:
Callable[[str, dict], Awaitable[str]]`. The problem with that first
draft: `run_task()` builds its own `Conversation` internally and never
exposes it, but a dispatch handler that needs to call `run_child(parent=
...)` (this task's own case) needs a real parent to call it with. The
first draft's answer was to have the caller build an *independent*
second `Conversation` via its own `create_conversation()` call, using
the same `template`/`key`/`iteration_budget`, and trust it would be
equal — true today, but a silent divergence risk the moment `run_task()`'s
own internal call ever changes (a different `system_message`, a future
`wall_clock_budget`) with nothing to catch the drift. The fix: `dispatch_factory`
receives the *actual* `Conversation` `run_task()` built, right after
building it, and returns the real dispatch handler — one call, one
value, no guessing. One new keyword argument, defaulting to a factory
that always returns the existing no-op — every existing caller (EVAL-01's
own tests, `prove_eval_harness.py`) is unaffected; this is still growth
as addition (design guideline 2), not a modification of anything closed.
`dispatch_factory` stays a `run_task()`-level argument, not a field on
`Task`, mirroring how `template` already is: `Task` stays exactly
prompt-plus-grader (EVAL-01's spec.md declined adding a tool surface to
`Task` itself "until a real task demonstrates that split is wrong" — this
task doesn't demonstrate that; it demonstrates that *both* `template` and
`dispatch_factory` belong at the call site, together, since they
describe the same thing, "what this run's environment can do," from two
different angles the existing `take_turn()` signature already keeps
separate).

### The task itself

`scripts/eval/tasks/plugin_dispatch.py` (new — the first file under
`scripts/eval/`, a real, keep-forever task rather than a
`scripts/prove_*.py`-named mechanism proof):

- A `ConversationTemplate` mounting one plugin (`plugin-a` — not both
  CONV-09 fixtures; this task checks "does it use the one it's given,"
  not "does it choose between two," a different and larger claim
  requirement 1 already rules out by staying single-turn).
- A `make_dispatch(parent: Conversation)` — the `dispatch_factory`
  `run_task()` calls with its real `Conversation` — returning a
  `dispatch()` that reuses CONV-09's own plugin-a shape (synthesize a
  fixed webhook-style input, `run_child(parent, ...)` against
  `tests/fixtures/plugins/plugin-a/skills/plugin-a-skill`, branch on
  whether the child's reply contains the acknowledgement marker) — the
  same fixture files, not copies of them.
- A `Task` whose `prompt` directly asks the model to call the plugin
  (matching CONV-09/spec.md's own established reasoning: this proof is
  about the wiring, not about whether a model spontaneously chooses the
  right tool unprompted — a different, harder claim this project has
  already decided not to make here).
- A `grade()` function reading the turn's message history structurally:
  1.0 if an assistant message requested `plugin_a_entry` **and** the
  paired tool-result message contains the child-acknowledged marker;
  0.5 if the tool was called but the child's acknowledgement didn't come
  through (the mechanism partly worked); 0.0 if the tool was never
  called at all. Partial credit, not a bare pass/fail, matching hermes's
  own `core_tool_deferral` grading shape.
- A `__main__` guard: checks `OPENROUTER_API_KEY`, sets
  `SADANA_PLUGINS_DIR`, calls `run_task()` for real, prints the
  `TaskRun`, saves it via `save_result()`/`results_dir_from_config()`
  (EVAL-01's own, unchanged).

"One real turn, end to end" describes the observable unit — one user
prompt in, one graded final response out — not a single model API call.
Internally this still costs three real calls (the parent asking for the
tool, the spawned child's own completion, the parent's final response
after the tool result), the same shape CONV-09's own turn 2 already
proved. Stated plainly here so it isn't a surprise reading the real run's
Evidence.

## Interface

```python
# src/sadana/eval_harness.py — one additive change:
def _no_tools_dispatch_factory(conversation: Conversation) -> Callable[[str, dict], Awaitable[str]]: ...
async def run_task(
    task, template, *, provider, model, iteration_budget, now,
    key=None, dispatch_factory=_no_tools_dispatch_factory,
) -> TaskRun: ...
```

```python
# scripts/eval/tasks/plugin_dispatch.py — new, directly runnable:
TASK: Task
def build_template() -> ConversationTemplate: ...
def make_dispatch(parent: Conversation) -> Callable[[str, dict], Awaitable[str]]: ...
# __main__: real run via run_task(..., dispatch_factory=make_dispatch), prints TaskRun, saves it
```

No other file's interface changes.

## Acceptance criteria

- [ ] `run_task()`'s new `dispatch_factory` parameter defaults to a
      factory always returning `_no_tools_dispatch`; every existing call
      site (EVAL-01's tests, `prove_eval_harness.py`) is unaffected and
      still passes unchanged.
- [ ] A new `tests/unit/test_eval_harness.py` case covers `run_task()`
      with a real `dispatch_factory` override (mocked network) — a tool
      gets called, its result reaches the grader via the message
      history, and the factory is shown to receive the real
      `Conversation` `run_task()` built (not an independent guess).
- [ ] `scripts/eval/tasks/plugin_dispatch.py`, run manually with a real
      `OPENROUTER_API_KEY`, produces a `TaskRun` with `exit_reason ==
      ExitReason.COMPLETED` and `score == 1.0` — pasted as this work
      item's Evidence.
- [ ] A local, no-network mocked run of `plugin_dispatch.py` proves the
      wiring before the real run is asked for, matching every prior real
      round trip this project has produced.
- [ ] `make verify` ends `VERIFY OK`; `plugin_dispatch.py` is not invoked
      by `make test`.

## Non-goals

- A generic battery runner iterating over many task modules. One task
  exists; building a runner for a collection that doesn't exist yet is
  exactly the premature machinery EVAL-01's spec.md already declined —
  add it once a second task module exists and needs one.
- Multi-turn tasks. Still out of scope, per intent.md §Constraints;
  `run_task()` remains single-turn-only.
- Mounting both CONV-09 fixture plugins in one task. This task checks use
  of one mounted plugin; a "choose between two" claim is a different,
  larger one this work item doesn't make.
- Moving `dispatch_factory` onto `Task` itself. Considered and declined
  in §Design — `template` and `dispatch_factory` travel together as
  call-site arguments, the same way `take_turn()` already keeps them.

## Rejected alternatives

- **A simpler, direct-answer task with no tool involved** (intent.md's
  other option, not chosen). Declined by the maintainer's own choice,
  not a design-stage call — recorded in intent.md §Changed during
  planning, not re-litigated here.
- **Mounting both fixture plugins and checking that the model picks the
  right one.** Rejected: that's a materially different and larger claim
  (model judgement between options, not just correct use of a tool it's
  told to use) — the same distinction CONV-09's own spec.md already drew
  between "wiring works" and "the model reliably chooses correctly
  unprompted," and this task stays on the wiring side of that line, same
  as CONV-09 did.
- **A fresh dispatch implementation instead of reusing CONV-09's fixture
  plugin as-is.** Rejected — the fixture and its skill file are already
  built, committed, and proven against a real model; reimplementing the
  same shape would be duplication with no new information, not a design
  improvement.
- **Adding `dispatch_factory` to `Task` as a field.** Rejected in §Design
  — it would duplicate what `template` already needs to carry alongside
  it (a task with tools needs both a template that mounts them and a
  dispatch factory that handles them; splitting one onto `Task` and
  leaving the other at the call site would be a worse split than keeping
  both at the call site together).
- **A plain `dispatch: Callable[[str, dict], Awaitable[str]]` parameter**
  — this work item's own first draft. Reversed once the reuse-focused
  self-check pass pointed out the real consequence: since `run_task()`
  never exposes the `Conversation` it builds, a plain dispatch parameter
  forces the caller to build an independent, "hopefully equal" second one
  to hand to `run_child()` — a real, silent divergence risk, not just
  redundant computation (the computation itself is free; `create_conversation()`
  is pure and local). The factory shape removes the guess entirely by
  construction. See §Design for the full account.

## Concerns

- **Grading partial credit (0.5) for "tool called but child didn't
  acknowledge" is a judgement call about what "partly right" means for
  this specific task**, not a general rule this project has settled.
  Named here so a future task's grader doesn't assume this project has a
  standard partial-credit scale — it doesn't yet; each task's grader
  decides its own.
- **This task's real run costs three real model calls for what reads as
  "one turn"** (§Design) — worth remembering when EVAL-03/a future
  battery runner estimates real spend across many tasks; a plugin-dispatch-shaped
  task is not the same cost as a plain single-call task.
- No policy skill named `project-structure` or `reference-lookup` exists
  in this project (same gap every prior spec.md this session has
  recorded). `testing-conventions` applies as already established in
  EVAL-01: `scripts/eval/tasks/plugin_dispatch.py` stays outside
  `make test` for the same real-network reason `prove_eval_harness.py`
  and `prove_conversation_e2e.py` do; the `run_task()` change to
  `eval_harness.py` itself gets covered by the existing committed test
  file, not a new one.
