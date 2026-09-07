# Spec: The smallest complete plugin runs, visibly

Intent: docs/tasks/D4-smallest-plugin-runs/intent.md

## Requirements

1. A real, live run of the smallest possible plugin — one node, one `ask`
   step, entered by naming its entry tool — happens against a real model.
   (intent: Proposed outcome, para 1)
2. After that run, a person can read what the plugin was: its name, its
   entry tool, and the one step it declared. (intent: Proposed outcome,
   "what the plugin was")
3. After that run, a person can read a real record of what happened when
   the step ran: that it happened, and whether it succeeded — not
   inferred from the final answer text, but from the run's own account of
   itself. (intent: Proposed outcome, "a real record of what happened")
4. The check is a file that stays in the repository and can be run again
   later, not a one-time transcript. (intent: Proposed outcome, "can be
   run again later"; Problem, "never kept in a form anyone can run again")
5. The plugin exercised is the existing one-node fixture, unmodified — no
   new or duplicate fixture plugin is created for this. (intent:
   Constraints, "must already exist and must not be rewritten or
   duplicated")
6. The run spends real money against a real model; no mocked or simulated
   model stands in for it. (intent: Constraints, "must spend real money")
7. Pass/fail is decided by inspecting what the run's own structured result
   says happened — never by asking a second model to judge it. (intent:
   Constraints, "judged by inspecting what actually happened")
8. The check is not added to `make test` / the pytest suite; it is run by
   hand and its output is pasted into `review.md`'s `## Evidence`.
   (intent: Constraints, "does not become part of the checks that run
   automatically")

## Design

**What already exists, and the one thing it doesn't do.**
`scripts/prove_plugin_dispatch_e2e.py` (written for D3-graph-dispatch,
merged `6e3c3b3`) already does requirements 1 and 5-8: it drives
`tests/fixtures/plugins/plugin-c` — the one-node, `ask`-only fixture — through
two real turns against `deepseek/deepseek-v4-flash-0731` on OpenRouter, using
the real mechanism (`plugin_manifest.discover_plugins()` →
`plugin_dispatch.build_plugin_set()` → `plugin_dispatch.build_dispatch()` →
`plugin_manifest.run_graph()` → a real `run_child()` call), asserts
`ExitReason.COMPLETED` and correct `next_child_seq` advancement, and is kept
in the repo, re-runnable, evidence pasted into `D3-graph-dispatch/review.md`.

What it doesn't do is requirements 2 and 3. `conversation.py:887-889`:

```python
dag_result = await dispatch(tc["name"], tc["arguments"])
result_text = dag_result.text
```

`dag_result` — a `plugins.DagResult` (`src/sadana/plugins.py:139-150`),
carrying `plugin`, `entry`, `trace: tuple[NodeTrace, ...]`, and
`failed_node` — is a local variable in `run_turn`'s tool-round loop. Only
`.text` survives past this point; `TurnResult` (`conversation.py:606-618`,
what `take_turn`/`take_turn_and_reconcile` hand back) has no field for it.
Nothing the existing script prints today ever touched `dag_result` — it
inspects `TurnResult` and `Conversation.next_child_seq` only. The plugin's
own account of itself (which node ran, whether it succeeded) exists for
exactly the duration of one Python statement and is then gone.

**The change: extend the existing script, don't add a new one.**
`plugin_dispatch.build_dispatch()` returns a `DispatchFn` — a plain
`Callable[[str, dict], Awaitable[plugins.DagResult]]` — before it is ever
handed to `take_turn_and_reconcile()`. A caller can wrap it:

```python
captured: list[plugins.DagResult] = []

async def capturing_dispatch(name: str, arguments: dict) -> plugins.DagResult:
    result = await dispatch(name, arguments)
    captured.append(result)
    return result
```

`capturing_dispatch` replaces the raw `dispatch` in each of the script's two
`take_turn_and_reconcile()` calls. This is the whole mechanism: no signature
in `src/` changes, no new fixture, no new file. `run_turn` still only ever
sees `.text`; the capture happens one layer up, entirely inside the script's
own `main()`.

After each turn, the script prints `captured[-1]`'s fields in place of
nothing:

```python
def _print_dag_result(label: str, result: plugins.DagResult) -> None:
    print(f"{label}: plugin={result.plugin!r} entry={result.entry!r} "
          f"failed_node={result.failed_node!r}")
    for t in result.trace:
        print(f"  node={t.node!r} kind={t.kind!r} visit={t.visit} "
              f"ok={t.ok} port={t.port!r} detail={t.detail!r}")
```

— satisfying requirement 2 (`result.plugin`, `result.entry`, and each
`NodeTrace.node`/`.kind` name the plugin and the step it declared) and
requirement 3 (`.ok`, `.failed_node`, and `.detail` are the run's own
account of what happened, read from the structured result, not the final
answer text).

**New assertions, structural, matching the existing script's style**
(requirement 7): after each turn, that turn's own capture list is
non-empty (see Interface, below, for why this check exists), then
`failed_node is None`, and `trace` has exactly one entry, for plugin-c's
single `interpret` node, with `kind == "ask"` and `ok is True`. This is the
same shape the script's existing `next_child_seq`/`pending_tool_call_ids`
assertions already use — inspect the structured value, assert on it, print
it.

## Interface

**Corrected after this work item's own deploy-stage cold review** (see
`review.md` § Findings): the capture list is scoped per turn, not shared
across both — a shared list let a turn that never actually dispatched
(`run_turn` reaches `ExitReason.COMPLETED` with no `dispatch` call at all
when the model answers without a tool call, and separately swallows a
raising `dispatch` into a `tool_error:` string) silently read the
*previous* turn's already-captured `DagResult` instead of failing loudly.
The design below is what actually shipped, not the original draft.

- New: `_print_dag_result(label: str, result: plugins.DagResult) -> None`,
  local to `scripts/prove_plugin_dispatch_e2e.py`. No return value; writes
  to stdout.
- New: `_assert_single_ask_node(label: str, captured:
  list[plugins.DagResult]) -> plugins.DagResult`, local to the script.
  Asserts `captured` is non-empty (the turn actually dispatched) before
  asserting on its last entry's `failed_node`/`trace`/`kind`/`ok`; returns
  that entry for the caller to print.
- New: `_make_capturing_dispatch(dispatch: plugin_dispatch.DispatchFn) ->
  tuple[plugin_dispatch.DispatchFn, list[plugins.DagResult]]`, a factory
  local to the script. Returns a wrapping closure matching
  `plugin_dispatch.DispatchFn` exactly (`Callable[[str, dict],
  Awaitable[plugins.DagResult]]`) — a transparent pass-through with one
  side effect (append to its own, freshly-created list) — paired with that
  same fresh list. Each of the script's two turns calls this once, after
  that turn's own `build_dispatch()` call, and gets back a dispatch wrapper
  and a capture list that belong to that turn alone.
- Changed: the script gains one import, `from sadana import plugins`
  (already an indirect dependency via `plugin_dispatch.DagResult`, now
  named directly for the capture list's type and the print helper's
  parameter).
- Nothing in `src/` changes. `DispatchFn`, `build_dispatch`,
  `take_turn_and_reconcile`, `DagResult`, and `NodeTrace` are all used
  exactly as D1-D3 already defined them.

## Acceptance criteria

- [ ] `scripts/prove_plugin_dispatch_e2e.py` wraps `dispatch` with a
      capturing closure before each of its two `take_turn_and_reconcile()`
      calls.
- [ ] After each turn, the script prints the captured `DagResult`'s
      `plugin`, `entry`, `failed_node`, and every `NodeTrace` entry's
      `node`/`kind`/`visit`/`ok`/`port`/`detail`.
- [ ] The script asserts, per turn: `failed_node is None`; `len(trace) ==
      1`; that entry's `kind == "ask"` and `ok is True`.
- [ ] `tests/fixtures/plugins/plugin-c` is untouched.
- [ ] No file under `src/` changes.
- [ ] The script is not invoked from `make test`, `scripts/run_tests.sh`,
      or any pytest file.
- [ ] `make verify` ends `VERIFY OK`.
- [ ] The script is run once for real, with a real `OPENROUTER_API_KEY`,
      ending `ALL ASSERTIONS PASSED`, output pasted into `review.md`'s
      `## Evidence` showing both turns' printed plugin identity and trace.

## Non-goals

- Persisting the trace to disk, or on every real turn regardless of who's
  asking — that is hermes's `agent/moa_trace.py` shape (own opt-in config
  key, JSONL file per session, `try`/`except`-swallowed so tracing can
  never break a live turn) and it is declined below, not adopted.
- A second fixture plugin, a route/compute/stop node, or any node kind
  beyond `ask` — the checkpoint is the one-node case; D3's spec already
  covers the walker's handling of the other three backbone kinds.
- Giving `DagResult`/`NodeTrace` a durable home in `TurnResult` or
  `Conversation` — that would be a real interface change to already-closed
  code for a manual proof script's benefit, out of proportion to what this
  item needs (see Rejected alternatives).

## Rejected alternatives

**A new eval Task under `scripts/eval/tasks/`, matching EVAL-02's own
shape.** Considered directly, since EVAL-02 is this project's own
precedent for "promote a one-off mechanism proof into something kept."
Declined: intent's own interview explicitly filed this under the PLUGINS
track, not eval-harness, precisely because it isn't the same kind of claim
— EVAL-02 checks *whether the model chooses to call a tool*, graded on
message history; this checks *whether a plugin's own declared graph, once
entered, hands back an honest account of itself*, which needs the
`DagResult` an eval `Task`'s `grade(TurnResult, messages)` signature never
receives either. Riding `eval_harness.Task` would buy `save_result`/
`results_dir_from_config` machinery this checkpoint doesn't need (intent's
own constraint keeps this out of anything resembling a graded, kept-forever
battery) at the cost of a second bet: a second real eval Task's-worth of
discovery/registration surface, for a project whose eval track (memory:
EVAL-03/CI-01) hasn't decided if or how tasks other than EVAL-02's get
discovered as a set yet. Guideline 2: adding a bet an undecided future
battery-mechanism would have to accommodate is worse than adding a proven
kind of file (`scripts/prove_*.py`) this project already has three of.

**A standalone new script, `scripts/prove_plugin_trace_e2e.py` or
similar.** Guideline 3: this would be *adding a step* — a second live
dispatch call against the same fixture, over the same wire, proving
overlapping ground D3's script already covers (discovery, catalog
building, `ExitReason.COMPLETED`, tracker reconciliation) purely to also
print a trace. `scripts/prove_plugin_dispatch_e2e.py` already performs the
exact two calls this checkpoint needs; wrapping its own `dispatch` before
passing it to `take_turn_and_reconcile()` is a heavier step, not a new
one, and costs one real model round-trip instead of two (four real calls,
since each turn already costs a parent-model call plus a spawned child
call). Direct precedent for editing a prior work item's own kept script
from a later one, without touching that work item's four closed artifacts:
CONV-10 edited `scripts/prove_conversation_e2e.py` (CONV-09's own script)
and D1-dagresult-dispatch edited it again — in both cases CONV-09's
`intent.md`/`spec.md`/`plan.md`/`review.md` themselves stayed untouched,
which is what CLAUDE.md's "never patch a closed work item" actually
guards (the artifact chain, not the source it produced).

**Persisting every real dispatch's trace to disk, opt-in via config —
hermes's `agent/moa_trace.py` shape** (`../hermes-agent/agent/moa_trace.py`,
DELEGATION block, tier 3). Read in full before deciding. It is a real,
production-hardened pattern: a config-gated (`moa.save_traces`) JSONL
append per session, `try`/`except`-swallowed so a tracing failure can never
break a live turn, deliberately kept out of the message-history table so it
never corrupts replay. What's specifically wrong with adopting it here: it
is *runtime production infrastructure* — a permanent code path in the
dispatch loop, gated by a new config key, with no second consumer of the
persisted files it would produce (no `--labels`-style comparative reader
exists for plugin traces the way `report.py` exists for hermes's evals, or
the way this project's own eval track was told to build one only after a
real drift incident). Building it now is exactly the "speculative
infrastructure with no consumer" guideline 2's caveat warns against, for a
checkpoint whose intent asks for one person to be able to read one run's
output by hand. What *is* adopted from it: the idea that a trace is a
side-channel, read separately from the model-facing text, never folded
into message history — already true of `DagResult.trace` today; this
design doesn't need to invent that separation, only stop discarding it.

## Concerns

**Guideline 2, applied where PLUGINS actually sits.** D1-D3 already built
the seam (`DagResult`, the graph walker); a fourth plugin, or an `ask`-node
proof against a different fixture, would be pure addition. This item isn't
that — it's making the *walker's own existing output* observable from
outside one Python call, which is a change to how a caller uses an
already-closed function, not a new plugin. That's why guideline 3's
step-cost question (heavier step, not a new one) carried more weight in
this design than guideline 2's addition-over-modification preference — the
two point in slightly different directions here, and step-cost won because
the "modification" is entirely inside a script's own `main()`, never
touching the closed function's own signature or behavior.

**Confidence on why this needs no unit test.** `_print_dag_result` and
`capturing_dispatch` are pure/near-pure (formatting, and a transparent
pass-through with one list append) and could technically be unit-tested
with a hand-built fake `DagResult` and no network. Declined anyway,
matching this project's own established boundary: every `scripts/prove_*.py`
script's internal helpers have gone unpythontested so far (not just this
one — `ChildSeqTracker` gets covered because it lives in `src/`; nothing
defined inside a `prove_*.py` file has ever gotten a `tests/unit/` file of
its own), and the check this item's own acceptance criteria already demand
— real assertions inside the live run, verified by a human reading the
pasted evidence — is this project's whole existing methodology for
`prove_*.py` scripts, not a gap being introduced here.

**Nothing here binds a future block.** No new rule for CLAUDE.md is being
proposed. The technique (wrap a `DispatchFn` in a capturing closure before
handing it to a caller that would otherwise discard its return value) is
specific to auditing one already-closed function from outside, not a
constraint on how future dispatch, node kinds, or `src/` code should be
written — the moment a real second consumer of `DagResult.trace` exists
(a UI, a comparative report, anything beyond one person reading stdout),
that consumer should very likely get a real field on `TurnResult` instead
of a wrapped closure, and that's a new work item's design decision, not
this one's.
