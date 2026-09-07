# Spec: a real plugin run, actually checked

Intent: docs/tasks/G3-real-plugin-under-eval/intent.md

## Requirements

1. `plugin-a` and `plugin-b` exist as real, on-disk plugins — a
   `plugin.toml`, a parameters schema, and (for `plugin-a`) an `init.py` —
   in their existing fixture directories, discoverable by
   `discover_plugins()`. This replaces the hand-written per-plugin
   `dispatch()` logic `scripts/prove_conversation_e2e.py` currently fakes.
   Traces to Proposed outcome ("Real, on-disk plugins... run their whole
   procedures for real").
2. `plugin-a`'s first step is a genuine `call` node that reaches outside
   for real and, on success, hands back a link `Artifact` — not the fixed
   stand-in string it returns today. Traces to Proposed outcome and the
   scope decision made during planning (upgrade the stand-in to a real
   call node).
3. Running `plugin-a`'s DAG for real exercises the actual approval
   question every time its `call` node is reached — answered by something
   that decides the same way every time, honestly, never bypassing the
   question. Traces to Constraints ("has to answer it honestly and the
   same way every time... not bypass the question").
4. `scripts/prove_conversation_e2e.py` drives both real plugins through
   the actual dispatch mechanism (not a hand-written closure), while
   keeping every other guarantee it already proved: byte-stable prompt,
   the `cache_control` marker, forced budget exhaustion, and a
   well-formed final transcript. Traces to Proposed outcome and item 8's
   own wording ("the script shrinks to a driver").
5. A check exists that reads the structured record of a real plugin run —
   not its rendered text — and decides, on its own, whether it did the
   right things. Traces to Proposed outcome ("say, on its own, whether it
   actually did the right things... not by reading a paragraph and
   guessing").
6. That check replaces an existing one that was doing exactly the thing
   being fixed here: `scripts/eval/tasks/plugin_dispatch.py`'s own
   `grade()` currently greps rendered tool-result text for the substring
   `"child acknowledged"` — prose, not structure. It is rewired to run
   `plugin-a`'s real DAG (dropping its own second hand-written copy of the
   same dispatch logic) and grade by reading the run's structured trace.
   Traces to Problem ("nothing checks it... a person has to read it").
7. `docs/reference/plugin_blueprint.md` states, truthfully, that its own
   remaining work is done. Traces to Proposed outcome (blueprint closure).

## Design

Six pieces, each small on its own, none needing a new module: two real
plugin directories, one defaulted parameter on an existing function, one
capturing wrapper moved from a proof script into the real package, and two
existing scripts rewired to use both.

### 1. `plugin-a` and `plugin-b` as real plugins

Both directories already exist
(`tests/fixtures/plugins/plugin-a/skills/plugin-a-skill/SKILL.md`,
`.../plugin-b/skills/plugin-b-skill/SKILL.md`) — `_skill_path()`/
`load_skill()` already read them; this item adds the manifest each one
has never had.

**`plugin-a/plugin.toml`** — the same three-step shape
`scripts/prove_conversation_e2e.py`'s comments already name
(`fetch_webhook` / `ask_helper` / `branch_on_reply`), now real nodes:

```toml
[plugin]
name = "plugin-a"
version = "0.1.0"
description = "Reaches outside for a stand-in webhook payload, asks a focused helper about it, and branches on whether the helper acknowledged."

[[entry]]
tool = "plugin_a_entry"
purpose = "Runs plugin-a's flow: fetches a webhook-style input, hands it to a focused helper, and reports back."
parameters = "schema/plugin_a_entry.json"
start = "fetch_webhook"

[[node]]
name = "fetch_webhook"
kind = "call"
body = "init:fetch_webhook"
next = "interpret"

[[node]]
name = "interpret"
kind = "ask"
skill = "plugin-a-skill"
next = "branch_on_reply"

[[node]]
name = "branch_on_reply"
kind = "route"
body = "init:classify"
ports = ["acknowledged", "not_acknowledged"]

[[node]]
name = "acknowledged"
kind = "stop"

[[node]]
name = "not_acknowledged"
kind = "stop"
```

`plugin-a/schema/plugin_a_entry.json`: `{"type": "object", "properties": {}}`
— the same empty schema the script's own hand-built `ToolSpec` already used.

`plugin-a/init.py`:

```python
from sadana import execution, plugins

def fetch_webhook(_value):
    outcome = execution.run_http(execution.HttpRequest(method="GET", url="https://example.com"))
    if isinstance(outcome, execution.Success):
        return plugins.Artifact(kind="link", name="webhook", ref="https://example.com")
    return "webhook fetch failed; falling back to a plain note"

def classify(value):
    return "acknowledged" if "ACKNOWLEDGED" in str(value) else "not_acknowledged"
```

`fetch_webhook` reuses `https://example.com` — the same stable, IANA-reserved
host `scripts/prove_execution.py` already proved `run_http` against for
EXECUTION's own live evidence, not a new target this item invents. On
`Success` it returns a link `Artifact`; G2's own mechanism (`run_graph`)
records it and threads `.ref` — a bare URL string — forward as `interpret`'s
input, which the existing `plugin-a-skill` prompt ("summarize... end with
`ACKNOWLEDGED`") already handles unchanged, since it only ever expected
"one short piece of input text," never anything specific to a webhook.
`classify` reads `interpret`'s own output (the child's final text) exactly
as the script's current `acknowledged = _ACK_MARKER in result.final_text`
line already does, just as a `route` body instead of inline script logic.

**`plugin-b/plugin.toml`** — the degenerate case, matching `plugin-c`'s own
one-node shape exactly:

```toml
[plugin]
name = "plugin-b"
version = "0.1.0"
description = "The simplest possible flow: hands a note to a focused helper and reports back."

[[entry]]
tool = "plugin_b_entry"
purpose = "Runs plugin-b's flow: hands a short note to a focused helper and reports back."
parameters = "schema/plugin_b_entry.json"
start = "ask_helper"

[[node]]
name = "ask_helper"
kind = "ask"
skill = "plugin-b-skill"
```

`plugin-b/schema/plugin_b_entry.json`:
`{"type": "object", "properties": {"note": {"type": "string", "description": "A short note to pass along."}}, "required": ["note"]}`
— identical to the script's current hand-built schema. No `init.py`: an
`ask`-only plugin has no body to resolve, the same as `plugin-c`.

### 2. `build_dispatch()` gains a scriptable `approve`

`plugin_dispatch.build_dispatch()` calls `plugin_manifest.run_graph(...,
ask=ask)` today with no `approve=` — every real dispatch built through it
falls through to `run_graph`'s own default, `_default_approve`, which
blocks on real terminal `input()`. Nothing has needed to override this
until now, because nothing built through `build_dispatch()` has ever had
a `call` node. `plugin-a` is the first, so this item adds the missing
passthrough — the exact shape `ask` already has:

```python
def build_dispatch(
    conversation: Conversation,
    plugin_set: PluginSet,
    *,
    stable_prompt: str,
    provider: str,
    model: str,
    now: float,
    persist: Callable[[tuple[Message, ...]], Awaitable[None]] = _noop_persist,
    approve: plugins.ApproveFn = plugin_manifest._default_approve,
) -> tuple[DispatchFn, ChildSeqTracker]:
    ...
    return await plugin_manifest.run_graph(
        installed.directory, installed.manifest, entry, arguments, ask=ask, approve=approve
    )
```

Defaulted, not required — mirrors `persist`'s existing default in this
same function, and means every existing `test_plugin_dispatch.py` call
site (none of which builds a `call`-kind fixture) needs no change.

### 3. `eval_harness.py`: `Task.grade` can see what a run's dispatch call
returned

The same problem D3/D4 already solved for their own proof scripts, in the
one place that hasn't needed it until now. `run_turn` still discards each
turn's `DagResult` after pulling `.text` (`conversation.py:888-889`) — this
item does not touch `conversation.py` or `TurnResult` to fix that (see
Rejected alternatives); it reuses the wrapping technique
`scripts/prove_plugin_dispatch_e2e.py:_make_capturing_dispatch` already
proved twice, moved into `eval_harness.py` itself so `run_task()` gets it
for every `Task`, not just a proof script that remembers to add it:

```python
def _capturing_dispatch(
    dispatch: Callable[[str, dict], Awaitable[plugins.DagResult]],
) -> tuple[Callable[[str, dict], Awaitable[plugins.DagResult]], list[plugins.DagResult]]:
    captured: list[plugins.DagResult] = []

    async def wrapped(name: str, arguments: dict) -> plugins.DagResult:
        result = await dispatch(name, arguments)
        captured.append(result)
        return result

    return wrapped, captured
```

`Task.grade` gains a third parameter:

```python
grade: Callable[[TurnResult, tuple[Message, ...], tuple[plugins.DagResult, ...]], float]
```

`run_task()` wraps whatever `dispatch_factory` returns before handing it
to `take_turn`, and passes the captured tuple to `grade`:

```python
dispatch = dispatch_factory(conversation)
wrapped_dispatch, captured = _capturing_dispatch(dispatch)
result, updated = await take_turn(..., dispatch=wrapped_dispatch, ...)
score = task.grade(result, updated.messages, tuple(captured))
```

A fresh `captured` list per `run_task()` call, never shared — the same
per-turn freshness rule `prove_plugin_dispatch_e2e.py`'s own docstring
already states the reason for (a stale list silently reading a previous
turn's result). `TaskRun` itself is unchanged — see Rejected alternatives
for why the captured `DagResult`s are not also persisted.

Every existing `grade` function's signature grows the one new parameter
(unused where a `Task` doesn't need it): the four in
`tests/unit/test_eval_harness.py`, one in `scripts/prove_eval_harness.py`,
and `scripts/eval/tasks/plugin_dispatch.py`'s own — which requirement 6
rewrites anyway, not just re-signs.

### 4. `scripts/prove_conversation_e2e.py`: the script shrinks to a driver

The ~115-line hand-written `dispatch()` closure (two per-plugin branches,
each hand-building `run_child`/`NodeTrace`/`DagResult` by hand) is
replaced by the same three calls `prove_plugin_dispatch_e2e.py` already
uses — `discover_plugins()` → `build_plugin_set()` →
`build_dispatch(..., approve=auto_approve)` — built once and reused across
all four turns via `plugin_dispatch.take_turn_and_reconcile`, wrapped in a
fresh `_capturing_dispatch`-style pair per turn that touches a plugin, so
the script can assert on the real `DagResult.trace` instead of the
internal `child`/`tool_surface` objects it inspects today.

`auto_approve` is this script's own small, honest stand-in for a person:

```python
approved_calls: list[tuple[str, str]] = []

async def auto_approve(plugin: str, node: str, _value: object) -> bool:
    approved_calls.append((plugin, node))
    print(f"    [auto-approved] {plugin}'s {node!r} step")
    return True
```

It is still the real `approve` callable `run_graph` calls and awaits —
nothing bypasses the question, it just answers without a person present,
matching intent's own Constraint. The script asserts `approved_calls`
is non-empty after turn 2, proving the gate actually fired.

Turns 1 (plain exchange, `cache_control` check) and 4 (forced budget
exhaustion) are unchanged — they never call a plugin, so nothing about
this item touches them. Turn 2 (`plugin-a`) and turn 3 (`plugin-b`) keep
their `COMPLETED`/`prompt_sha256`-unchanged assertions, and gain a direct
read of the turn's own captured `DagResult`: turn 2 asserts
`failed_node is None`, `[t.node for t in trace] == ["fetch_webhook",
"interpret", "branch_on_reply", "acknowledged"]` (or the `not_acknowledged`
sibling), and `artifacts == (Artifact(kind="link", name="webhook",
ref="https://example.com"),)`; turn 3 asserts the single `ask` node
succeeded. The child-isolation assertions the current script performs
directly (`_assert_child_isolated`, `tool_surface == ()`) are dropped —
not weakened, moved: `tests/unit/test_conversation.py` already covers
`run_child`'s key uniqueness and tool-surface restriction as unit tests
(`test_run_child_key_embeds_parent_and_node_name`,
`test_run_child_restricts_tool_surface_to_spec_tools`), independent of any
live script, and once dispatch is built through `plugin_dispatch.build_dispatch`
the script no longer has the internal `child` object to inspect directly —
the same encapsulation D3/D4 already accepted for `plugin-c`'s own proof.

### 5. `scripts/eval/tasks/plugin_dispatch.py`: grading by trace, not prose

Its own `make_dispatch()` — a second, independent hand-written copy of
`plugin-a`'s old logic — is deleted; `main()` builds the real dispatch the
same way `prove_conversation_e2e.py` now does (`discover_plugins()` →
`build_plugin_set()` → `build_dispatch(..., approve=auto_approve)`), using
only `plugin-a` (this task never called `plugin-b`). `grade()` changes
from:

```python
acknowledged = any(m.role == "tool" and m.content and "child acknowledged" in m.content for m in messages)
return 1.0 if acknowledged else 0.5
```

to reading the third parameter `run_task()` now supplies:

```python
def grade(_result: TurnResult, _messages: tuple[Message, ...], dag_results: tuple[plugins.DagResult, ...]) -> float:
    if not dag_results:
        return 0.0
    dag = dag_results[-1]
    if dag.failed_node is not None:
        return 0.0
    return 1.0 if any(t.port == "acknowledged" for t in dag.trace) else 0.5
```

This is the concrete instance of requirement 6 and of closing
`plugin_blueprint.md §10` Risk 4: the exact `Task` the blueprint's own risk
note points at now grades by reading `NodeTrace.port`, never by searching
rendered text for a phrase a future prompt change could silently rewrite
out from under the check.

### 6. `docs/reference/plugin_blueprint.md`: closing the record

`Status: draft for design` (line 3) becomes `Status: implemented`. A short
closing note is added after §11's work-item table recording that items
1-9 are closed and the §2.4 step-17 checkpoint ("a real plugin's DAG runs
end to end, under approval") is met, naming this work item.

## Interface

**In:** `plugin-a`/`plugin-b`, discovered the same way any installed
plugin already is (`discover_plugins()`, no new mechanism). `build_dispatch`
gains one new optional keyword, `approve`. `Task.grade` gains one new
positional parameter, `dag_results: tuple[plugins.DagResult, ...]`.
**Out:** unchanged shapes everywhere else — `DagResult`, `NodeTrace`,
`Artifact`, `TaskRun` are exactly what they already are; no new dataclass,
no new file format.
**Errors:** none new. `fetch_webhook`'s own `Failure` path returns a plain
string rather than raising, so a real network hiccup fails the DAG the
same generic way any other node's stray return value already would,
never a special case.

## Acceptance criteria

- [ ] `discover_plugins()` finds `plugin-a` and `plugin-b`, both `Valid`.
- [ ] `run_graph`, called directly with a scripted `approve` returning
      `True`, walks `plugin-a`'s real manifest end to end for an
      acknowledging child, producing `artifacts == (Artifact(kind="link",
      name="webhook", ref="https://example.com"),)` and a trace of exactly
      `fetch_webhook → interpret → branch_on_reply → acknowledged`.
      Covered by a unit test with a fake `ask` (no real model call) and a
      fake `run_http` is not needed — the real `execution.run_http`
      against a live network is exercised only in the standalone proof
      script, per `testing-conventions`' network ban; the unit test
      exercises the manifest/graph shape with a stubbed `init.py` body
      instead (mirroring how G1/G2's own tests fake a `call` node's body).
- [ ] A scripted `approve` returning `False` for `plugin-a`'s `call` node
      ends the walk with `detail == "declined"`, proving the manifest is
      genuinely gated, not merely well-formed.
- [ ] `build_dispatch(..., approve=...)` threads the given `approve`
      through to `run_graph` — covered by a new
      `tests/unit/test_plugin_dispatch.py` test using a fixture plugin
      with a `call` node and an explicit fake `approve`.
- [ ] `run_task()` calls `task.grade` with the real `DagResult`s a graded
      turn's dispatch produced — covered by a `tests/unit/test_eval_harness.py`
      test whose `dispatch_factory` returns a dispatch that yields a known
      `DagResult`, asserting the grader received it.
- [ ] `scripts/prove_conversation_e2e.py` and `scripts/eval/tasks/plugin_dispatch.py`
      both run cleanly against a real model and real network (manual run,
      pasted as this item's Deploy evidence, per `testing-conventions`'
      network ban and CLAUDE.md's live-proof rule) — the former proving
      both real plugins end to end across all four of its existing turns,
      the latter proving its own score is `1.0` via structural trace
      inspection.
- [ ] `docs/reference/plugin_blueprint.md`'s `Status` line and closing note
      updated; `docs/reference/ citations resolve to tracked files` (the
      existing pre-commit check) still passes.
- [ ] `make verify` ends `VERIFY OK`.

## Non-goals

- Sandboxing `plugin-a`'s outward call, or any change to what a `call`
  node is allowed to reach — EXECUTION's own settled scope (E1),
  unchanged here.
- Persisting a run's `DagResult`s inside the saved `TaskRun` JSON — see
  Rejected alternatives.
- A general-purpose "trace assertion" library or DSL for future `Task`s —
  one real task, graded once, in a way that can be run again; not a
  framework for a population of tasks that doesn't exist yet.
- Porting `plugin-b` into the eval `Task` — `scripts/eval/tasks/plugin_dispatch.py`
  only ever exercised `plugin-a`; widening it is a separate concern this
  item doesn't need to open.

## Rejected alternatives

**Extending `TurnResult` (in `conversation.py`) with a `dag_results` field,
populated inside `run_turn`, instead of a capturing wrapper in
`eval_harness.py`** — declined. This project has already solved "let
something outside `conversation.py` see what a dispatch call returned"
twice, the identical way, in `scripts/prove_plugin_dispatch_e2e.py`
(`_make_capturing_dispatch`) and its own D4 extension — a wrapper around
the dispatch closure, owned by the caller, zero changes to `conversation.py`'s
turn loop. Reusing an already-proven pattern a third time is a smaller bet
than introducing a competing one that touches a closed, heavily-tested
core dataclass and its turn loop for the same outcome.

**Persisting the captured `DagResult`s inside `TaskRun` (via `save_result`/
`load_result`)** — declined, after checking the actual round trip.
`save_result` writes `asdict(run)` to JSON; `load_result` reconstructs
with a bare `TaskRun(**payload)`. A nested dataclass field (`DagResult`
containing `NodeTrace`/`Artifact` tuples) would round-trip through JSON as
plain dicts, and `TaskRun(**payload)` would not reconstruct them back into
real dataclass instances — a silent type mismatch between what
`load_result` returns and what it declares. Fixing that round-trip
generically is a real, separate piece of work this item doesn't need to
take on just to let `grade()` see a trace during scoring, which is fully
served by the ephemeral, in-memory capture alone.

**Keeping `scripts/eval/tasks/plugin_dispatch.py`'s own hand-written
`make_dispatch()` and only changing its `grade()` to read message
content more carefully** — declined. The hand-written dispatch is a
second, independent copy of the exact logic this item is porting to a
real plugin elsewhere; leaving it in place would mean two different
implementations of "what plugin-a does" existing side by side, one real
and one fake, which is the precise kind of drift `plugin_blueprint.md
§10` Risk 3 (manifest and `init.py` drift) warns about one level up.
Requirement 6 replaces both the dispatch and the grading in the same
move.

**A `call` node for `plugin-a` that hits a project-controlled endpoint
instead of `https://example.com`** — declined for this item. No such
endpoint exists yet (that is the "no real webhook" gap intent's Problem
section names, still true after this item — only the *mechanism* for
reaching outside is now real, not a webhook this project owns). Reusing
`run_http`'s own already-proven live target keeps this item's evidence
identical in kind to EXECUTION's own, rather than standing up new
infrastructure this item's intent never asked for.

## Concerns

**The approval gate's honesty here rests on `auto_approve`'s own good
faith, the same trust boundary F1 already accepted.** `auto_approve`
always returns `True` — it proves the *question gets asked and answered*
every time (asserted via `approved_calls`), not that a real person ever
declines one. A future item that wants proof of the decline path under
real network/model conditions (as opposed to the unit test's stubbed
`approve=False` case, which this item does cover) would need a different
script; this one only proves the honest-approval path live, matching
intent's own smallest-version framing.

**`scripts/prove_conversation_e2e.py` loses direct visibility into child
isolation, by design, not by oversight.** Flagged plainly because it is a
real reduction in what this specific script can observe directly — the
guarantee itself is not weakened (still enforced by `run_child` and still
unit-tested), only this one live script's own vantage point on it changes,
the same trade-off `prove_plugin_dispatch_e2e.py` already made for
`plugin-c`.

**Policy conformance.** `testing-conventions` applies throughout: the new
unit tests (`run_graph` walking `plugin-a`/`plugin-b` with stubbed
`ask`/`approve` and a fake `init.py` body, `build_dispatch`'s `approve`
passthrough, `run_task`'s `dag_results` threading) never touch the network
or the model API; both live proofs remain standalone scripts per the
existing convention, their output pasted as Deploy evidence. `project-structure`
and `reference-lookup` do not exist as separate skills in this repository
(same finding every prior spec in this block made); CLAUDE.md's "Our
methodology for learning from the reference" is this project's actual
policy, and guideline 1's research above follows it — here, "the
reference" is this project's own prior art (`prove_plugin_dispatch_e2e.py`,
`prove_execution.py`) at least as much as hermes-agent, and both are cited
by name above.

**No other policy conflict found.** Every extension point used already
exists and is already defaulted or optional (`build_dispatch`'s new
`approve` kwarg, `Task.grade`'s widened signature); the one genuinely new
piece of state (`eval_harness._capturing_dispatch`'s `captured` list) has
the identical function-local, per-call lifetime as the pattern it copies.
Nowhere in this design did two of the project's own rules pull in
different directions.
