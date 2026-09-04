# Spec: A check on the agent's actual behaviour can be written once and run again

Intent: docs/tasks/EVAL-01-task-runner-core/intent.md

## Requirements

1. One behavioural check is describable as a plain value — what to ask the
   agent, and how to judge the response — separate from any script that
   runs it. (Intent §Proposed outcome.)
2. Running that value drives the real `create_conversation()`/`take_turn()`
   machinery this project already has, against a real model. (Intent
   §Proposed outcome, §Affected users and systems.)
3. Judging a response is a plain, repeatable function over what the agent
   actually did — never a second model call rendering an opinion. (Intent
   §Constraints; blueprint §1.2 — none of the three hermes domains audited
   use one either.)
4. Running a check produces a plain, inspectable record of what happened
   and the score it got, in a form durable enough to look at again later.
   (Intent §Proposed outcome.)
5. This work item proves the mechanism with exactly one real, deliberately
   minimal, throwaway check — not a curated set worth keeping. (Intent
   §Constraints, §Changed during planning.)
6. Nothing about this becomes part of `make test` or any automatically-run
   check. (Intent §Constraints.)
7. Comparing two runs against each other, and anything resembling a
   pass/fail gate, is out of scope. (Intent §Constraints.)
8. Nothing in `src/sadana/conversation.py` changes. This work item is a
   new caller of already-closed machinery, the same posture CONV-09 had
   toward the turn loop.

## Design

The harness is a small library module, not a script — `run_task()` and
the types around it are meant to be imported by later work items (EVAL-02,
EVAL-03), so they get the same treatment as any other real-I/O module in
this codebase rather than living only inside a one-off script.

### Reference corpus (design guideline 1)

Audited in `docs/reference/eval_harness_ci_blueprint.md` §1.2 against
`evals/core_tool_deferral`, `evals/readtool`, `evals/browser_use`
(hermes-agent). What's adopted here and why:

- **A task is data — a prompt plus a grading function — never a single
  expected string.** All three hermes domains share this shape even
  though their grading logic differs wildly (structural tool-call checks
  in `core_tool_deferral`, regex-over-final-text in `readtool` and
  `browser_use`). Adopted as `Task`'s own shape below.
- **Grading is programmatic, never an LLM asked to judge.** True in all
  three domains audited, zero exceptions. Adopted as requirement 3.
- **The real agent drives every check — real spend, no scripted
  stand-in.** Matches this project's own `prove_conversation_e2e.py`
  precedent independently. Adopted as requirement 2.

Declined, each for a stated reason rather than a preference:

- **Fixture-building functions** (hermes: a callable that constructs a
  hostile-file workspace before a task runs, `readtool/fixtures.py:39-56`).
  This project has no filesystem/read-tool concern yet to build fixtures
  for; a `Task` here needs only a prompt. Add a fixture hook once a real
  task needs one, not speculatively.
- **Resume-safe, deduped, per-cell JSON across `(arm, task, model, rep)`**
  (hermes: `orchestrator.py:37-52`). That solves running a *battery* of
  many tasks across many repetitions without re-paying for cells already
  done. This work item runs exactly one task, once. Building dedup/resume
  logic now would be state with no scenario yet to justify it — guideline
  4's answer is to derive it later, when EVAL-03's actual battery exists,
  not store it against a hypothetical one now.
- **A `ctx` object aggregating tool-call counts, a callback log, and
  workspace contents** for graders to inspect (`core_tool_deferral/tasks.py`,
  throughout). This project's own `TurnResult` + the turn's `Message`
  history already carry what's available (final text, exit reason, tool
  calls made) — a grader reads those two values directly; no new
  aggregation type is invented to wrap them.
- **The two-checkout A/B arm plumbing** (`worker.py:210-230`'s pinned
  base/pr git-worktree pattern). Answers "did my change help," a question
  with no meaning yet — there is exactly one implementation, not two to
  compare. Comparison is EVAL-02's job, once there's something to compare
  against.

### Task and grading

```python
@dataclass(frozen=True)
class Task:
    task_id: str
    prompt: str                                    # the turn's user_input
    grade: Callable[[TurnResult, tuple[Message, ...]], float]
```

`grade` receives exactly what `take_turn()` already produces — the
`TurnResult` (final text, exit reason, model calls, usage) and the turn's
resulting message history — and returns a score in `[0.0, 1.0]`. No new
type wraps these; `conversation.py` already defines both, and a grader
reading them directly is the smallest surface that could work (design
guideline 3: an existing step, `take_turn()`'s own return values, absorbs
what a `ctx` object would otherwise exist only to restate).

### Running one task

```python
@dataclass(frozen=True)
class TaskRun:
    task_id: str
    score: float
    exit_reason: ExitReason
    final_text: str | None
    provider: str
    model: str
    timestamp: float
    detail: str | None = None


async def run_task(
    task: Task,
    template: ConversationTemplate,
    *,
    provider: str,
    model: str,
    iteration_budget: IterationBudget,
    now: float,
    key: ConversationKey | None = None,
) -> TaskRun:
    """Builds a fresh Conversation from `template` (create_conversation()),
    takes exactly one turn with `task.prompt` (take_turn()), grades the
    result with `task.grade`, returns a TaskRun. `key` defaults to a
    value derived from `task.task_id` and `now` when not given, so two
    independent callers never collide on the same conversation key by
    accident (CLAUDE.md's own "names, not pointers, with a uniqueness
    story" rule, applied even though nothing persists this key anywhere
    yet)."""
```

A task with no tools of its own uses a template whose `tool_specs` is
empty; `_no_tools_dispatch()` (a module-level function returning
`tool_error: no tools available` — never actually invoked when the model
is offered nothing to call) satisfies `take_turn()`'s required `dispatch`
parameter, which has no default. `compress` is `_compress_noop`, matching
every other caller's own precedent (`conversation.py`'s own module never
implements compression yet). `wall_clock_budget` stays unset (`None`) —
nothing about EVAL-01 needs a time limit.

### Recording a run

```python
def save_result(run: TaskRun, results_dir: Path) -> Path:
    """Writes one JSON file per run to results_dir/<task_id>__<int(timestamp)>.json,
    creating results_dir if it doesn't exist. Never overwrites — a second
    run of the same task at a different `timestamp` gets its own file, by
    construction (the filename embeds it). Returns the path written."""
```

One file per run, not an appended log or a database — this work item
records a single run at a time and nothing yet reads more than one file
back (requirement 7 rules comparison out). `results_dir` is a caller
argument, not a hardcoded path — the caller (a proof script now, a
battery runner in EVAL-03 later) decides where results land, and
`results_dir_from_config()` is the caller-agnostic way to get one:
`SADANA_EVAL_RESULTS_DIR`, default `config.get_paths().state_dir / "eval"
/ "results"` — the same `store_path_from_config()` pattern CONV-08's
`conversation_store.py` already established (CLAUDE.md: "use config for
behaviours"), not a path improvised relative to whichever script happens
to call it. Corrected during this work item's own self-check, which
caught the first draft hardcoding a script-relative path instead — see
`## Concerns`.

### Files

```
src/sadana/eval_harness.py           Task, TaskRun, run_task(), save_result(),
                                      results_dir_from_config()
tests/unit/test_eval_harness.py      mocked-network unit tests — same posture as
                                      CONV-08's conversation_store.py, a real src/
                                      module gets a real committed test file
scripts/prove_eval_harness.py        the one real, throwaway smoke task
```

No `scripts/eval/results/.gitignore` — that was the first draft's own
hardcoded-path mistake made visible as a file layout; once results moved
under the OS state directory (via `results_dir_from_config()`), there is
nothing left inside the repo tree to gitignore. `conversation_store.py`'s
own sqlite file lives the same way: under `state_dir` by default, never
inside the repo.

`eval_harness.py` is a `src/` module, not a script — it gets the same
committed-unit-test treatment `conversation_store.py` did (mocked
`model_access.send`, no real network, `tests/unit/test_<module>.py`).
`scripts/prove_eval_harness.py` is the separate, real-network,
not-run-by-`make test` proof, matching `prove_conversation_e2e.py`'s own
precedent exactly. The two are not the same check wearing two names: one
proves the module's own logic against a controllable fake; the other
proves it against the real world it was built for.

Not `src/sadana/eval.py` — shadows the `eval` builtin's name closely
enough to read as a mistake at a glance; `eval_harness.py` says the same
thing without the ambiguity. Not under `scripts/` itself (only the proof
script is) — `Task`/`run_task`/`save_result` are meant to be imported by
EVAL-02/EVAL-03, the same reason `conversation_store.py` (CONV-08) is a
real module and not a script.

## Interface

```python
class Task: ...          # frozen dataclass, see above
class TaskRun: ...        # frozen dataclass, see above
def _no_tools_dispatch(name: str, arguments: dict) -> Awaitable[str]: ...
async def _compress_noop(messages: tuple[Message, ...], system_prompt: str) -> str | None: ...
def run_task_key(task_id: str, now: float) -> ConversationKey: ...
async def run_task(task, template, *, provider, model, iteration_budget, now, key=None) -> TaskRun: ...
def results_dir_from_config() -> Path: ...
def save_result(run: TaskRun, results_dir: Path) -> Path: ...
def load_result(path: Path) -> TaskRun: ...
```

`run_task_key()` is `run_task()`'s default-`key` construction (§Design,
§Concerns), exposed as its own function rather than inlined so a caller
can predict a task's key before running it. `load_result()` is
`save_result()`'s exact inverse, added during implementation once its own
round-trip test needed a way to read a written file back — the smallest
possible reader for a format this module already owns, not a general
result-querying capability (requirement 7 still rules that out).

`run_task()` raises whatever `create_conversation()`/`take_turn()`
already raise (`PromptDriftError`, etc.) — no new exception type. A
`TurnResult` whose `exit_reason` isn't `COMPLETED` still gets graded and
recorded; `task.grade` decides what a non-completion is worth (typically
0.0, but that's the grader's call, not the runner's).

## Acceptance criteria

- [ ] `run_task()` called with a minimal template (one task, no tools)
      against a real model produces a `TaskRun` with `exit_reason ==
      ExitReason.COMPLETED` and a `score` the task's own grader assigned.
- [ ] `save_result()` writes a JSON file whose contents round-trip every
      `TaskRun` field.
- [ ] `scripts/prove_eval_harness.py`, run manually with a real
      `OPENROUTER_API_KEY`, drives one throwaway smoke task for real and
      prints its `TaskRun`, pasted as this work item's Evidence — same bar
      as `prove_model_access.py`/`prove_conversation_e2e.py`.
- [ ] `tests/unit/test_eval_harness.py` (mocked `model_access.send`, no
      real network) covers `run_task()`'s happy path, a non-`COMPLETED`
      exit still producing a graded `TaskRun`, and `save_result()`'s
      round trip — committed, run by `make test`, same posture as
      `tests/unit/test_conversation_store.py`.
- [ ] `make verify` ends `VERIFY OK`; `src/sadana/eval_harness.py` is
      covered by `mypy` (it's under `src/`, `pyproject.toml`'s `files =
      ["src"]` already includes it — no config change needed).
- [ ] `scripts/prove_eval_harness.py` is not invoked by `make test`.

## Non-goals

- A curated set of real behavioural checks worth keeping. That's EVAL-03.
- Comparing two runs, regression detection, or any pass/fail gate. EVAL-02.
- Resume/dedup across a battery of many tasks and repetitions — no battery
  exists yet to make this worth building (see §Design).
- Multi-turn tasks. `run_task()` takes exactly one turn; a task needing a
  back-and-forth is a real, larger design question for whichever later
  item first needs one, not decided speculatively here.
- A task carrying its own tool surface. `template` is the caller's
  responsibility; `Task` itself stays prompt-plus-grader only, until a
  real task demonstrates that split is wrong.

## Rejected alternatives

- **Embedding `ConversationTemplate` construction inside `Task` itself**,
  so a task is fully self-contained. Rejected: every task in this work
  item's own scope (one, throwaway, no tools) would build the identical
  empty template, and a later task that does need tools will very likely
  want to share a template with sibling tasks rather than each rebuilding
  one — deferring this to the caller costs nothing now and forecloses
  nothing later.
- **A `ctx` aggregation type for graders**, mirroring hermes. Rejected in
  §Design — `TurnResult` plus the message history already carry
  everything this project has to inspect; inventing a second type to hold
  the same two values is the state guideline 4 warns against.
- **Resume-safe/deduped result storage.** Rejected in §Design — no
  battery exists yet; premature per guideline 2 (a bet with no scenario
  backing it) and guideline 4 (state with nothing to derive it from yet).

## Concerns

- **First implementation draft hardcoded `RESULTS_DIR = Path(__file__).resolve().parent / "eval" / "results"`
  in `scripts/prove_eval_harness.py`, ignoring `config.py`'s already-established
  pattern for exactly this.** Caught by this work item's own self-check
  (a delegated reuse-focused review, not spotted during design), not by
  spec.md's own reference-corpus audit — hermes's own eval domains don't
  use anything resembling this project's config module, so there was no
  prior-art trigger to catch it there either. Fixed by adding
  `results_dir_from_config()` (§Design), removing the hardcoded path
  entirely. Recorded here because it's the same class of gap CONV-08's
  and CONV-09's own reviews caught — a self-check fix landing after
  spec.md's first draft was already written — except this time caught and
  corrected within the same build pass, before a cold review had to find
  it.
- **`run_task()`'s `key` default (`task.task_id` + `now`) is a naming
  convention, not a uniqueness guarantee.** Nothing in this work item
  persists conversations anywhere (CONV-08's store is not used here — a
  deliberate, separate choice, matching CONV-09/CONV-10's own posture of
  not pulling in unrelated blocks), so a key collision has no observable
  consequence today. Named here so a future reader adding real
  persistence to eval runs knows this key was never designed against that
  requirement.
- **Grading returns a bare `float`, not a richer value carrying its own
  reasoning.** Sufficient for one throwaway task; if a real EVAL-03 task's
  grader needs to explain *why* it scored what it did (for a human reading
  a comparative report later), that's a real interface question for
  EVAL-02/EVAL-03 to raise, not preempted here with an unused field.
- No policy skill named `project-structure` or `reference-lookup` exists
  in this project (same gap every prior spec.md this session has
  recorded). `testing-conventions` was checked and applies in full:
  `eval_harness.py` is a `src/` module, so it gets a real, committed
  `tests/unit/test_eval_harness.py` (mocked network) — the same treatment
  every other `src/` module in this codebase gets, not an exception.
  `scripts/prove_eval_harness.py` stays outside `make test`'s suite for
  the separate, already-established reason `prove_conversation_e2e.py`
  does (real network, non-deterministic) — the two rules apply to two
  different files for two different reasons, not one rule applied twice.
