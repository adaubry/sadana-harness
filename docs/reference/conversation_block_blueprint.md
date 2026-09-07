# sadana — CONVERSATIONS block: intent material and architecture spec

Reference commit audited: `NousResearch/hermes-agent@63279301` (2026-09-03).
Every hermes claim below carries a `path:line` you can open in `../hermes-agent`.

This document is not an `intent.md` or a `spec.md`. It is the material you need to write the intent with your agent (section 1 and section 8), and the architecture decisions that should survive into `spec.md` (sections 3 to 7). Section 2 is the audit of the reference that justifies those decisions.

---

## 0. Starting position (what the plan assumes)

Already in sadana: a **config block** (no `.env` bridge yet) and a **provider client** (the raw model call). Not yet in sadana: a tool registry, a transcript store, a plugin runtime.

Therefore CONVERSATIONS is the block that _introduces_ the tool surface and the transcript, because a turn loop cannot exist without either. The plan keeps both as thin interfaces inside this block so a later TOOLS block or PERSISTENCE block can absorb them without a rewrite. That is the "graft host" idea from your glossary applied to this block.

Two paradigm facts drive everything:

1. **The agent never authors procedure.** A plugin (repo = plugin) carries `skills/`, `deps/` and a hardcoded DAG (`init.py`). The conversation must let _one_ agent use _many_ plugins in the _same_ conversation, and must offer the DAG a way to run "a subagent with a specific skill" as one node.
2. **The system prompt is byte-stable for the life of a conversation**, compression is the single named exception, and any other prompt-state mutation is deferred. This must be _checkable_, not a comment.

---

## 1. Intent seed — what the CONVERSATIONS block must promise

### 1.1 Outcome (one sentence)

A re-entrant, budgeted turn loop that turns `(conversation_key, user_input)` into `(final_text, exit_reason, transcript_delta)` while dispatching tool calls to a frozen per-conversation tool surface, and that can be invoked _by a plugin DAG node_ as a child conversation with a chosen skill and a restricted tool subset.

### 1.2 The concrete milestone (what "done" looks like for the backbone)

A standalone script (outside `make test`, per your rule on first external round trips) that proves, against a real provider:

- one conversation, three turns, **two plugins** mounted at once: `plugin-a` (a 3-node DAG: webhook-ish input node → subagent-with-skill node → branch node) and `plugin-b` (the degenerate case: one skill, one node that launches a subagent with it);
- turn 2 uses `plugin-a`, turn 3 uses `plugin-b`, both in the same transcript;
- the child conversation spawned by the DAG node has its own budget, its own transcript rows, and cannot see the parent's tools beyond the subset it was given;
- `sha256(system_prompt)` is identical on turns 1, 2 and 3 (the byte-stability contract) and the assertion is _in the script_;
- the iteration budget is exhausted on purpose on a fourth turn and the loop exits with `ExitReason.BUDGET_EXHAUSTED` and a well-formed transcript (every `tool_call` has exactly one paired result).

That output pasted as Deploy-stage Evidence is the milestone.

### 1.3 Affected systems

Config block (new keys under `conversation:`), provider client (one new requirement: it must return `tool_calls` in a normalized shape and raise a typed overflow error), and two new internal interfaces this block owns until their own blocks exist: `ToolSurface` and `Transcript`.

### 1.4 What is explicitly _out_ of this block

Compression, memory, gateway/platform adapters, MCP client, the DAG engine itself, the plugin marketplace, checkpoints, streaming display, liveness watchdog, provider fallback chains. Each is named below with the seam it plugs into so the loop does not have to be reopened.

---

## 2. Audit of the reference — what hermes's conversation block really is

### 2.1 Shape and cost

The block you listed is ~53 000 lines. The turn loop itself is one function, `run_conversation` (`agent/conversation_loop.py:2026`), whose body runs to line ~9310 and whose outer `while` is at `agent/conversation_loop.py:2289`:

```
while (api_call_count < agent.max_iterations and agent.iteration_budget.remaining > 0) or agent._budget_grace_call:
```

Everything hermes learned in 27 000 commits about providers (OAuth refresh per vendor, 429 backoff, thinking-signature stripping, llama.cpp grammar fallback, image shrinking, Copilot stale-credential 400s…) is threaded into that loop. `agent/turn_retry_state.py:36-94` is the honest inventory: 20+ one-shot guards, _all_ provider recovery, none of it conversation logic. That file is the strongest evidence for the first design decision below: **provider recovery does not belong in the loop**.

The loop has been decomposed along three seams that _are_ worth copying as seams:

| Seam       | hermes file                                                                                                                  | What it holds                                           |
| ---------- | ---------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------- |
| Prologue   | `agent/turn_context.py:544` (`TurnContext`), `:572` (`build_turn_context`)                                                   | once-per-turn setup, returns the locals the loop reads  |
| Tool round | `agent/tool_executor.py:1099` (concurrent), `:1957` (sequential), `:2876` (segmented); selection in `run_agent.py:9089-9119` | dispatch of one assistant message's `tool_calls`        |
| Epilogue   | `agent/turn_finalizer.py:138` (`finalize_turn`), result dict at `:718-751`                                                   | budget-exhaustion summary, persistence, result assembly |

Between prologue and epilogue, the loop body per iteration is: build API messages → model call (with the retry sub-loop at `:3338`) → if `tool_calls`: validate names, cap/dedupe (`:7955-7958`), append assistant row, **persist before executing** (`:8113-8140`), execute (`:8168`), check guardrail halt (`:8178`), decide compression (`:8218+`) → else: final text → epilogue.

### 2.2 Mechanisms worth copying (audited)

**IterationBudget** (`agent/iteration_budget.py`). 62 lines, dependency-free, lock-protected consume/refund. Copy. Audit note: `refund()` exists only for `execute_code` turns (`conversation_loop.py:8205-8210`). sadana has no such tool; drop `refund` and document that removal so nobody re-adds it "for parity". Second note: hermes seeds the parent cap at `sys.maxsize` (`agent/agent_init.py:547`) while the docstring says 500; the config-vs-code drift is exactly what your "know which config loader you are inside" rule is about. In sadana the cap is one config key, read once at conversation creation.

**Persist-before-execute** (`conversation_loop.py:8113-8140`). The assistant tool-call row is flushed to the store _before_ any handler runs, and if the flush fails the turn `break`s with `session_persistence_failed` rather than running side-effecting tools from in-memory-only state. This is a correctness invariant for a harness whose plugins call webhooks and APIs. Copy the invariant, not the code.

**One result per tool call, always** (`conversation_loop.py:7960-7970` and `:8088-8104`). Providers reject an assistant message whose `tool_calls` lack matching `tool` rows. hermes keeps every call in the assistant row and error-results the invalid ones. Copy as a transcript invariant enforced at append time, not as loop code.

**Result normalization at the registry boundary** (`tools/registry.py:1135-1163`). Handlers may return a `str` or one multimodal envelope; everything else becomes a bounded `tool_error`. Bounding error bodies (`:39-71`, 2 048 chars) stops error text stacking across retries. Copy both.

**Prompt tiers and restore-or-build** (`agent/system_prompt.py:1-24` docstring; `conversation_loop.py:993` `_restore_or_build_system_prompt`). The prompt is built once per session, stored in the session row, restored verbatim on later turns, rebuilt only by compression. This _is_ your byte-stability rule — hermes just enforces it by convention and a warning log. sadana enforces it by hash (section 4.3).

**Tool result budget constants** (`tools/budget_config.py:15-33`): per-result cap 100 K chars, per-turn 200 K, MCP-prefixed tools 50 K, `read_file` pinned to ∞ to prevent persist→read→persist loops. The three-layer idea (per-result, per-turn, pinned exceptions) is sound and cheap. Copy the constants into config, not the spill-to-disk machinery.

**Deadline resolution order** (`agent/deadline.py:14-24` docstring): `timeouts:` config section > legacy env var > default, plus platform-safe clamping (`:189`). This matches your config-for-behaviour rule. Copy `resolve_timeout` and `clamp_timeout` semantics; ignore `run_bounded_async` until a blocked-event-loop incident actually happens to sadana.

**Subagent as a fresh agent with a focused prompt** (`tools/delegate_tool.py:1230` `_build_child_system_prompt`, `:1745` `_build_child_agent`, `:3008` `child.run_conversation(...)`). The child is a new `AIAgent` with its own `IterationBudget`, an explicit toolset list, an inherited or overridden model, and a role derived from depth (`:1783-1790`) rather than declared by the caller. That derivation ("capability comes from depth alone") is the right instinct. Copy the shape: child = new conversation, budget from config, tools by explicit subset, depth from parent + 1.

**Exit reason as a first-class field** (`_turn_exit_reason`, `turn_finalizer.py:718-724`). hermes carries it as a free-form string (`"max_iterations_reached(3/3)"`, `"guardrail_halt"`, `"session_persistence_failed"`). Copy the idea, make it an `Enum` with optional detail.

### 2.3 Mechanisms to leave behind (and the hidden coupling that would bite)

**The god object.** `init_agent` takes ~90 parameters (`agent/agent_init.py:536-620`), of which ~25 are UI callbacks and ~20 are provider routing. `run_conversation` mutates ~40 `agent._*` attributes. The coupling is invisible from any one file: `turn_context.py` writes `agent._cached_system_prompt`, `turn_finalizer.py` reads `agent._response_was_previewed`, `tool_executor.py` reads `agent._tool_guardrail_halt_decision`. Transplant any one file and it silently reads attributes nothing in sadana sets. This is the #1 transplant hazard.

**Name-based tool policy.** Parallel safety (`agent/tool_dispatch_helpers.py:44-60` `_PARALLEL_SAFE_TOOLS`), side-effect classification (`agent/tool_result_classification.py:16-22` `NO_EFFECT_TOOL_NAMES`), housekeeping detection (`conversation_loop.py:7995-7998` `_HOUSEKEEPING_TOOLS`), destructive-command regexes (`tool_dispatch_helpers.py:70-86`), file path overlap admission (`:296-392`). All of it keys on hermes's built-in tool names. In sadana every meaningful tool comes from a plugin whose name you do not control. The policy must be _declared by the tool_ (section 5.2), never inferred from a name.

**Plugin hooks that reach into the loop.** hermes exposes ~30 hooks (`hermes_cli/plugins.py:163-230`: `pre_llm_call`, `transform_llm_output`, `pre_verify`, `transform_api_error_classification`…). In hermes a plugin _extends the agent_. In sadana a plugin _is invoked by the agent_; it is an SOP, not an aspect. Giving plugins loop hooks would let two plugins in one conversation fight over the same turn, which is precisely the multi-plugin failure you want to design out. No plugin hooks in this block. An `Observer` protocol for tracing and tests only.

**Skills as agent-owned state.** `skill_manage`, `skills_list`, `skill_view`, the skills index in the _volatile_ prompt tier (`agent/system_prompt.py:20-22`), the post-turn "skill review" fork, `tools/skill_provenance.set_current_write_origin`. All of it exists because hermes's agent writes skills. sadana's does not. Skills are read-only plugin assets loaded _as tool results_ (section 5.4), never as prompt state.

**Tool Search / progressive disclosure** (`tools/tool_search.py:1-45`). Solves catalog bloat for thousands of MCP tools by hiding them behind three bridge tools. Note its own stated lesson: the catalog is rebuilt from the live definitions every call to avoid drift. Not needed at backbone; the seam to keep is that the tool surface is a _value_ computed once per conversation, so a later disclosure layer can wrap it.

**Provider recovery in the loop.** Everything in `TurnRetryState` plus the 429/backoff/credential-pool code at `conversation_loop.py:3338-4100`. Belongs in your provider block. The conversation loop should see exactly three provider outcomes: `Response`, `ContextOverflow`, `ProviderFailure` (section 5.1).

**Liveness watchdog** (`agent/turn_liveness.py`), **checkpoints** (`tools/checkpoint_manager.py`), **registration leases** (`registration_lifecycle.py`), **async/sync bridging** (`agent/async_utils.py`). Each is a scar from a specific incident in a sync-with-threads architecture. Not needed if the loop is async-first (section 5.6) and plugins cannot hot-swap registrations mid-conversation (section 4.4).

**Files in your CSV that are not conversation at all** and should not be read for this block: `evals/**` (benchmarks), `hermes_cli/kanban*.py`, `tools/kanban_tools.py`, `plugins/kanban/**` (multi-agent board), `tools/mcp_oauth*.py`, `tools/mcp_dashboard_oauth.py`, `tools/mcp_schema_cache.py`, `tools/mcp_stdio_watchdog.py`, `hermes_cli/mcp_*.py`, `mcp_serve.py` (MCP client/server plumbing — a later block), `plugins/google_meet/**` (one plugin), `tools/blueprints.py` (skill+cron bridge), `tools/tip_tool.py`, `tools/slash_confirm.py`, `hermes_cli/slash_exec.py`, `mini_swe_runner.py`, `toolset_distributions.py` (data-gen sampling). `tools/todo_tool.py` and `agent/oneshot.py` are small and worth a glance: `oneshot.py:1-22` defines "a single stateless model call outside any conversation" — that is the primitive a DAG _non-agent_ node that still needs an LLM (e.g. "classify this payload") should use, and it should be the provider block's job, not this one's.

---

## 3. Architecture — the block at a glance

```
                    ┌──────────────────────────────────────────────┐
  Plugin DAG node ─▶│  ConversationRunner                          │
  (later block)     │   run_turn(key, input) ─▶ TurnResult          │
                    │   run_child(ChildSpec) ─▶ ChildResult         │
                    └───────┬──────────────────────┬───────────────┘
                            │                      │
             ┌──────────────▼──────┐     ┌─────────▼──────────┐
             │ TurnLoop (pure-ish) │     │ Conversation (agg.) │
             │  state machine      │     │  key, prompt, epoch │
             │  budgets, exit      │     │  surface, transcript│
             └───┬──────────┬──────┘     └─────────┬──────────┘
                 │          │                      │
     ┌───────────▼──┐  ┌────▼────────┐    ┌────────▼────────┐
     │ ProviderPort │  │ ToolSurface │    │ Transcript      │
     │ (exists)     │  │ defs+dispatch│   │ append-only log │
     └──────────────┘  └─────────────┘    └─────────────────┘
```

Proposed layout (adapt names to your tree):

```
src/sadana/conversation/
    keys.py          natural keys: ConversationKey, TurnKey, MessageKey
    transcript.py    Transcript protocol + in-memory + sqlite impl
    surface.py       ToolSpec, ToolSurface (frozen), build_surface()
    budget.py        IterationBudget, WallClockBudget
    loop.py          TurnLoop state machine, ExitReason
    conversation.py  Conversation aggregate, prompt epoch contract
    runner.py        ConversationRunner: run_turn / run_child
    observer.py      Observer protocol (no-op default)
    errors.py        typed errors this block raises
```

Six modules that matter, one file each, no file over ~400 lines. If one grows past that, it is absorbing a neighbouring block's job.

---

## 4. State contracts (spec.md material)

### 4.1 Identity — names, not pointers

- `ConversationKey`: a caller-supplied unique natural key (`str`, e.g. `"support/ticket-4821"` or a ULID the gateway mints). Unique constraint in the store. A child conversation's key is `f"{parent_key}/child/{node_name}/{n}"` where `node_name` comes from the DAG and `n` is the per-parent sequence — readable in a DB browser, and the lineage is in the name, not in a foreign key you have to join.
- `TurnKey = (ConversationKey, turn_seq: int)`, `MessageKey = (ConversationKey, msg_seq: int)`. Sequences are assigned by the transcript on append, gap-free, so "every tool_call has one result" is checkable by scanning a range.
- `tool_call_id`: whatever the provider issued, stored verbatim, and the pairing invariant is `(assistant_msg.tool_calls[i].id == tool_msg.tool_call_id)`. hermes needed `coalesce_tool_call_id` (`agent/message_sanitization.py`) because some providers omit ids; require the provider port to synthesize one if missing so this block never sees `None`.

### 4.2 Transcript — append-only, invariant-checked

`Transcript` protocol: `append(msg) -> MessageKey`, `read(key) -> list[Message]`, `last_seq(key) -> int`. Two invariants enforced _in `append`_, raising `TranscriptInvariantError`:

1. Role alternation: after an `assistant` row with `tool_calls`, the next rows must be `tool` rows until every id is paired; no `user` row may be appended while ids are unpaired.
2. Pairing: a `tool` row's `tool_call_id` must match an unpaired id from the immediately preceding assistant row.

Persistence order (copied invariant): the assistant tool-call row is appended _and durably stored_ before any handler runs. If `append` raises, the loop exits `ExitReason.PERSISTENCE_FAILED` and runs nothing. Say what this survives: a crash between the assistant append and the tool appends leaves a transcript with unpaired ids; the recovery rule is that `run_turn` on such a conversation first appends `tool` rows with `{"error": "interrupted before execution"}` for every unpaired id (hermes: `agent/message_sanitization.py:296` `close_interrupted_tool_sequence`), then proceeds. That repair is the _only_ mutation `run_turn` may make before the prologue.

### 4.3 System prompt — byte-stable, checkable, deferred invalidation

The conversation row stores `system_prompt: str`, `prompt_sha256: str`, `prompt_epoch: int` (starts at 0).

Contract, enforced in `Conversation.begin_turn()`:

- Every turn asserts `sha256(rendered_prompt) == prompt_sha256` for the current epoch. A mismatch is a bug, raised as `PromptDriftError` — never silently rebuilt, never logged-and-continued (hermes logs a warning and falls back to a fresh build at `conversation_loop.py:1034-1041`; that is the behaviour you told me to make checkable instead).
- `prompt_epoch` may increment for exactly one reason in this block: `Compression` (a later block) calls `conversation.rotate_prompt(reason="compression", new_prompt=...)`. The reason is an `Enum` with one member so adding a second is a visible diff.
- Any other action that _would_ change prompt state (a plugin mounted, a config key edited, a model switched) calls `conversation.defer_invalidation(what)`. The invalidation is recorded on the row and applied when the _next conversation_ is created from the same template, or at the next epoch rotation — never mid-conversation. This is your "deferred invalidation as the default" rule as a method with a test.

Prompt composition follows hermes's tiers but drops the volatile tier entirely: `stable` (identity + tool-use guidance) + `context` (caller `system_message` + plugin _catalog_: one line per mounted plugin — name, one-sentence purpose, entry tool name). Nothing time-dependent (hermes puts a timestamp line in the volatile tier, `system_prompt.py:20-22`; that alone defeats byte-stability). Time, if the model needs it, is a tool.

### 4.4 Tool surface — a frozen value per conversation

`ToolSurface` is an immutable value computed once at `Conversation.create()` from the resolved set of `ToolSpec`s (core tools + every mounted plugin's exported tools). It exposes `definitions() -> list[dict]` (provider format) and `dispatch(name, args, ctx) -> ToolResult`.

- Descriptions are rendered at _build time_ with the resolved set in hand: `ToolSpec.describe(surface: ResolvedNames) -> str`. A tool that needs to mention another tool asks the resolver for its name; no description string is ever a literal cross-reference. (Your rule; hermes gets this partly with `dynamic_schema_overrides`, `tools/registry.py:226-234`, but at `get_definitions()` time, i.e. per call, which can drift within a conversation.)
- Mounting a plugin after creation is a deferred invalidation (4.3), not a mutation. This is what removes the need for `registration_lifecycle.py`'s generational leases: nothing is replaced in a live surface.
- The surface carries the definitions bytes hash too; the prompt-stability assertion covers `sha256(system_prompt + definitions_json)`, because for cached providers the tool schemas are part of the prefix.

### 4.5 Budgets

- `IterationBudget(max_total)` — copy of hermes minus `refund`. One per conversation, _not_ per turn (hermes: per agent, injected at `agent_init.py:606`), because a plugin DAG may drive several turns and the SOP author reasons about a whole run.
- `WallClockBudget(seconds)` — optional; when 80 % is consumed, one system notice is appended to the _user_ side of the transcript (hermes `RUN_BUDGET_WRAPUP_NOTICE`, `conversation_loop.py:125-131`), never to the system prompt.
- Child conversations get their own `IterationBudget` from `conversation.child.max_iterations` (config) and inherit the _remaining_ wall clock, so a child can never outlive its parent's run.
- Depth: `conversation.child.max_depth` (config, default 2). Derived from parent depth + 1 as in hermes; the DAG cannot request a depth.

### 4.6 Turn result

```python
@dataclass(frozen=True)
class TurnResult:
    conversation_key: ConversationKey
    turn_key: TurnKey
    final_text: str | None
    exit_reason: ExitReason           # Enum below
    detail: str | None                # free text for the human, never parsed
    model_calls: int
    usage: Usage                      # from provider port, summed
    appended: range                   # msg_seq range this turn produced
```

`ExitReason = {COMPLETED, BUDGET_EXHAUSTED, WALL_CLOCK_EXHAUSTED, PERSISTENCE_FAILED, PROVIDER_FAILED, CONTEXT_OVERFLOW_UNHANDLED, INTERRUPTED, INVALID_TOOL_CALLS}`. Eight members; a ninth needs a spec change. hermes's result dict (`turn_finalizer.py:718-751`) has 27 keys mixing result, usage, cost, provider identity and UI flags; the DAG needs the eight above and `usage`.

---

## 5. Interfaces (spec.md material)

### 5.1 Provider port (what this block requires from the existing client)

```python
class ProviderPort(Protocol):
    async def complete(self, *, system: str, messages: list[Message],
                       tools: list[dict], budget: RequestBudget) -> Completion: ...
```

`Completion` = `text | None`, `tool_calls: list[ToolCall]` (each with non-empty `id`, `name`, `arguments: dict` already parsed), `finish_reason`, `usage`. The port raises exactly two typed errors the loop understands: `ContextOverflow` (loop hands it to the compression seam or exits `CONTEXT_OVERFLOW_UNHANDLED`) and `ProviderFailure` (loop exits `PROVIDER_FAILED`; retries, backoff, credential refresh, fallbacks all happened _inside_ the port). Argument-JSON repair (hermes `_repair_tool_call_arguments`) is the port's job too: the loop never sees a string that should have been a dict.

If the existing client is sync, wrap it once in the port with `asyncio.to_thread`; do not make the loop sync to fit it (5.6).

### 5.2 ToolSpec — policy is declared, never inferred

```python
@dataclass(frozen=True)
class ToolSpec:
    name: str                          # unique in the surface; plugin tools are "<plugin>.<tool>"
    parameters: dict                   # JSON schema
    handler: Callable[[dict, ToolContext], Awaitable[str | Multimodal]]
    describe: Callable[[ResolvedNames], str]
    concurrency: Literal["safe", "exclusive"] = "exclusive"
    effects: Literal["none", "external"] = "external"
    max_result_chars: int | None = None   # None -> config default; plugins may lower, not raise
```

- `concurrency="safe"` tools in one batch run concurrently; anything else serializes the batch in emission order. That is the whole parallel policy at backbone — no path-overlap engine, no destructive-command regex.
- `effects="none"` is what an interrupted-batch cleanup may discard; `"external"` results are always appended even when cancelled (as `{"error": "cancelled"}`), preserving pairing.
- Name prefixing `<plugin>.<tool>` is how two plugins in one conversation never collide, and how the transcript later tells you which plugin acted without a join.

### 5.3 Tool round contract

For one assistant message with `tool_calls`:

1. Partition into `valid` (name in surface) and `invalid`. Append the assistant row (all calls) and durably store — abort turn on failure.
2. Append error results for `invalid` immediately.
3. Execute `valid` per 5.2; each handler is wrapped: exceptions → bounded `tool_error`, result normalized (`str` or multimodal envelope), size-capped per `max_result_chars` then per-turn cap (`conversation.tool_results.turn_budget_chars`), with a note in the result when capped. No spill-to-disk at backbone; a capped result says it was capped and by how much.
4. Append each result _as it lands_ (so a crash mid-batch leaves at most the unfinished ids unpaired, repaired by 4.2).
5. Fire `Observer.tool_round_done`.

Cap and dedupe of identical calls (hermes `_deduplicate_tool_calls`, `conversation_loop.py:7958`) is kept as a single pure function with a test; the delegate-cap one is dropped since children are spawned by DAG nodes, not by the model.

### 5.4 Skills and the child conversation primitive (the plugin seam)

The one thing the DAG needs from this block:

```python
@dataclass(frozen=True)
class ChildSpec:
    node_name: str                 # becomes part of the child key
    skill: SkillRef                # (plugin_name, skill_name) -> a SKILL.md on disk, agentskills.io shape
    input: str                     # the node's payload, rendered by the DAG
    tools: frozenset[str]          # subset of the parent surface, by name; may be empty
    model: str | None = None       # override or inherit

async def run_child(parent: Conversation, spec: ChildSpec) -> ChildResult
```

The child is a **new `Conversation`** (own key, own budget, own prompt hash, own transcript rows), created with:

- system prompt = `stable` tier + the skill's `SKILL.md` **body** (the full instructions, loaded once, part of the child's byte-stable prompt) + the hermes-style task framing (`delegate_tool.py:1247-1290`: task, context, "report what you did / found / changed / issues"). Keep the framing under 20 lines; it is the only prose this block ships.
- tool surface = parent surface filtered to `spec.tools` (a filter of a frozen value, so no rebuild, no drift), plus a `skill.read_reference(path)` tool bound to the skill's `references/` and `assets/` directories so progressive disclosure works without prompt mutation.
- `ChildResult = TurnResult + child_key`. The parent DAG decides what to do with `final_text`; this block never injects it into the parent transcript. That separation is what keeps "node output" a DAG concern.

For the parent conversation, the skills of mounted plugins are _not_ in the prompt and _not_ in a tool list. The catalog line per plugin (4.3) is the only thing the model sees; invoking the plugin means calling its entry tool, and the DAG decides whether a skill-driven child runs. This is the paradigm difference from hermes made concrete: the model picks a _plugin_, the plugin picks the _procedure_.

The degenerate plugin (one skill, one node) then costs nothing special: its entry tool's handler is `run_child(ChildSpec(skill=..., tools=<whatever the plugin exports>))`.

### 5.5 Observer (tests and tracing, not extension)

```python
class Observer(Protocol):
    def turn_started(self, key: TurnKey) -> None: ...
    def model_called(self, key: TurnKey, n: int, usage: Usage) -> None: ...
    def tool_round_done(self, key: TurnKey, results: list[ToolOutcome]) -> None: ...
    def turn_ended(self, result: TurnResult) -> None: ...
```

Sync, exceptions swallowed and logged, cannot return values. Tests use a recording observer instead of reading source (your "read source code in a test" prohibition). Plugins never receive one.

### 5.6 Concurrency model — async-first, and what each operation survives

The loop is `async def`. Reason: every plugin node is IO (webhooks, APIs, MCP, DB), and hermes's sync-core-plus-threads design is where `async_utils.py`, `deadline.run_bounded_async`, and the liveness watchdog came from. State each async operation's survival explicitly in the spec:

- **Model call**: cancellation discards the partial completion; nothing is appended; the iteration is _not_ consumed. Survives: nothing — a cancelled call is as if it never happened.
- **Tool round**: cancellation lets in-flight `effects="external"` handlers finish (they may have already fired a webhook) up to `conversation.tool_timeout_s`, then appends `cancelled` results for any id still unpaired. Survives: the transcript's pairing invariant, always.
- **Child conversation**: cancelled with its parent; its transcript rows persist with `ExitReason.INTERRUPTED`. Survives: the child's transcript, for audit.
- **Transcript append**: awaited, never fire-and-forget. Survives: process crash after return.

Timeouts: `conversation.timeouts.{model_call_s, tool_call_s, turn_s}` resolved config-first, clamped as in `deadline.py:189`. No env-var timeouts.

### 5.7 Config keys introduced

```yaml
conversation:
  max_iterations: 60
  run_budget_seconds: null
  child:
    max_iterations: 20
    max_depth: 2
  tool_results:
    result_chars: 100000
    turn_budget_chars: 200000
    plugin_result_chars: 50000
  timeouts:
    model_call_s: 300
    tool_call_s: 120
    turn_s: null
```

All behaviour; nothing secret. The `.env` bridge is not needed by this block.

---

## 6. The turn loop as a state machine

```
REPAIR ──▶ PROLOGUE ──▶ MODEL_CALL ──┬─ text ──────────────▶ EPILOGUE(COMPLETED)
   ▲          │             │        └─ tool_calls ─▶ TOOL_ROUND ─▶ (budget?) ─▶ MODEL_CALL
   │          │             ├─ ContextOverflow ──▶ compression seam ──▶ MODEL_CALL | EPILOGUE(CONTEXT_OVERFLOW_UNHANDLED)
   │          │             └─ ProviderFailure ──▶ EPILOGUE(PROVIDER_FAILED)
   │          └─ prompt hash mismatch ──▶ raise PromptDriftError (never an ExitReason)
   └── only mutation allowed before PROLOGUE: pair dangling tool_calls (4.2)
```

- `REPAIR`: 4.2 recovery; no-op on a clean transcript.
- `PROLOGUE`: assert prompt hash; append user row; snapshot `msg_seq` start. Equivalent of `build_turn_context` but it returns a small frozen `TurnContext` and mutates nothing but the transcript.
- `MODEL_CALL`: `budget.consume()` or exit `BUDGET_EXHAUSTED`; wall clock check; call the port.
- `TOOL_ROUND`: 5.3.
- `EPILOGUE`: build `TurnResult`; on `BUDGET_EXHAUSTED` with no `final_text`, do **one** tool-less model call asking for a summary (hermes `_handle_max_iterations`, called at `turn_finalizer.py:210`) — this is the single named exception to "no model call outside `MODEL_CALL`" and must be flagged in the spec as such.

Everything in the box is testable with a fake provider port that scripts `Completion`s and a fake transcript, no network, no threads.

---

## 7. Work items, in build order

Each line is one artifact chain. The order is dependency order; nothing later is needed to test something earlier.

1. **CONV-01 keys + transcript.** `ConversationKey`, sequences, in-memory transcript, both invariants, repair function. Tests: pairing violations raise; repair pairs dangling ids and touches nothing else.
2. **CONV-02 tool surface.** `ToolSpec`, `build_surface(specs)`, resolved-name description rendering, definitions hash, filter-by-names. Tests: a description referencing another tool changes when that tool is renamed in the resolved set; filter never rebuilds.
3. **CONV-03 budgets + config keys.** `IterationBudget` (audited copy), `WallClockBudget`, config schema for section 5.7 in the existing config block. Tests: process-isolated per your rule; no state-reset fixtures.
4. **CONV-04 provider port adapter.** Wrap the existing client into `ProviderPort`; typed `ContextOverflow` / `ProviderFailure`; argument parsing and id synthesis. Test with recorded responses.
5. **CONV-05 turn loop.** Section 6 with fake port + fake transcript. Tests enumerate all eight `ExitReason`s and the byte-stability assertion (turn 2 with a mutated prompt raises).
6. **CONV-06 conversation aggregate + runner.** `Conversation.create`, prompt tiers and hash, `defer_invalidation`, `run_turn`. Test: deferred invalidation is invisible to the running conversation and visible to the next created one.
7. **CONV-07 child conversation.** `ChildSpec`, `run_child`, skill body loading (agentskills.io frontmatter validation: `name` matches directory, `description` ≤ 1024 chars), `skill.read_reference` tool, depth and budget inheritance. Test: child cannot call a tool outside its subset; child key embeds parent key and node name.
8. **CONV-08 sqlite transcript + conversation store.** Unique constraints on the natural keys; persist-before-execute proven by a test that fails the append and asserts no handler ran.
9. **CONV-09 deploy evidence.** The section 1.2 script under `scripts/`, with two fixture plugins under `tests/fixtures/plugins/` that satisfy _only_ the `ToolSpec` + `ChildSpec` interfaces (a hand-written 3-node "DAG" in plain Python, because the DAG engine is a later block). Output pasted as Evidence.

CONV-01 through CONV-05 have no external callers when they land. That is the plan working.

---

## 8. Open questions to settle in `intent.md` (with my recommendation)

1. **Where does the plugin catalog line come from?** From the plugin manifest (repo root, one file, name + purpose + entry tool). Recommendation: define that manifest's _three_ fields now in this block's spec as `PluginCatalogEntry`, so the PLUGINS block owns the file format but the conversation block owns what it needs from it.
2. **Does the parent ever see a child's transcript?** Recommendation: no, only `ChildResult`. The DAG can expose a "show me what the subagent did" node if a plugin author wants it. Keeping this out of the loop is what makes children cheap.
3. **Compression seam signature.** Recommendation: `async def compress(conv: Conversation) -> RotatedPrompt | None`, called only on `ContextOverflow`; returns `None` at backbone (so the exit reason `CONTEXT_OVERFLOW_UNHANDLED` is exercised in the evidence script). Decide now so CONV-05 has a stable hole to leave.
4. **Interrupt source.** The loop needs a cancellation token. Recommendation: `asyncio.CancelledError` is the token; no `_set_interrupt` flag, no interrupt message. A gateway later cancels the task.
5. **Streaming.** Recommendation: not in this block. The port may stream internally; the loop consumes a `Completion`. Add `Observer.text_delta` later without touching the loop.
6. **Model per plugin?** hermes lets delegation override provider/model per child (`delegate_tool.py:1754-1759`). Recommendation: allow `ChildSpec.model` from day one (it is one field) but resolve it through the provider port, never in this block.
7. **One transcript per child or rows in the parent's table with the child key?** Recommendation: same table, keyed by the child's natural key; the lineage is readable from the key string alone.

---

## 9. Risks (for the trace section, only once you actually hit them)

- **Provider tool-call shape drift**: the port promises non-empty ids and parsed dicts. If the existing client cannot guarantee it for some provider, the fix goes in the port, never in the loop.
- **Skill body size**: a SKILL.md body over ~5 000 tokens in a child prompt eats the child's context; the agentskills.io guidance is < 500 lines. Validate at load, warn, do not truncate (your pagination rule: an instruction the model must read fully is never cut).
- **Two plugins exporting the same tool name**: impossible after prefixing, but a plugin _manifest_ name collision at mount time must fail loudly (unique constraint on plugin name in the surface).
- **Temptation to add a ninth `ExitReason`**: probably means a provider concern leaked into the loop; check the port first.

---

### Appendix — hermes files in your CSV, by verdict

| File                                                                                                                           | Verdict                                                       | Note                                                               |
| ------------------------------------------------------------------------------------------------------------------------------ | ------------------------------------------------------------- | ------------------------------------------------------------------ |
| `agent/iteration_budget.py`                                                                                                    | copy                                                          | drop `refund`                                                      |
| `agent/turn_context.py`                                                                                                        | copy the seam                                                 | `TurnContext` becomes frozen, no `agent` mutation                  |
| `agent/turn_finalizer.py`                                                                                                      | copy the seam                                                 | result dict → `TurnResult` (8 exit reasons)                        |
| `agent/conversation_loop.py`                                                                                                   | learn, do not copy                                            | keep: persist-before-execute, pairing, exit reason, wrap-up notice |
| `agent/tool_executor.py`                                                                                                       | learn                                                         | keep: append results as they land; drop name-based segment planner |
| `agent/tool_dispatch_helpers.py`                                                                                               | reject                                                        | policy by name                                                     |
| `agent/tool_result_classification.py`                                                                                          | reject                                                        | policy by name → `ToolSpec.effects`                                |
| `agent/turn_retry_state.py`                                                                                                    | reject (moves to provider block)                              | the inventory of what _not_ to put in the loop                     |
| `agent/deadline.py`                                                                                                            | copy `resolve_timeout`/`clamp_timeout` semantics              | config-first                                                       |
| `agent/turn_liveness.py`                                                                                                       | defer                                                         | phase 2, gateway                                                   |
| `agent/async_utils.py`                                                                                                         | reject                                                        | async-first removes the need                                       |
| `agent/oneshot.py`                                                                                                             | later, provider block                                         | stateless LLM call for non-agent DAG nodes                         |
| `agent/agent_init.py`, `agent_runtime_helpers.py`, `run_agent.py`                                                              | learn the coupling, copy nothing                              | ~90-arg constructor, ~40 mutable attrs                             |
| `tools/registry.py`                                                                                                            | copy: `ToolEntry` shape, result normalization, error bounding | drop scopes/leases/override policy                                 |
| `tools/budget_config.py`                                                                                                       | copy constants into config                                    | drop spill-to-disk                                                 |
| `tools/tool_search.py`                                                                                                         | defer                                                         | surface is a value; wrap later                                     |
| `toolsets.py`, `toolset_distributions.py`                                                                                      | reject                                                        | toolsets are replaced by plugin exports + `ChildSpec.tools`        |
| `registration_lifecycle.py`                                                                                                    | reject                                                        | no live replacement of registrations                               |
| `tools/checkpoint_manager.py`                                                                                                  | defer                                                         | not a conversation concern                                         |
| `tools/todo_tool.py`                                                                                                           | later                                                         | a candidate core tool, not loop logic                              |
| `tools/mcp_*`, `hermes_cli/mcp_*`, `mcp_serve.py`                                                                              | later block                                                   | MCP client                                                         |
| `hermes_cli/kanban*`, `tools/kanban_tools.py`, `plugins/kanban/**`                                                             | ignore                                                        | multi-agent board                                                  |
| `evals/**`                                                                                                                     | ignore                                                        | benchmarks                                                         |
| `plugins/google_meet/**`                                                                                                       | ignore                                                        | example plugin                                                     |
| `tools/blueprints.py`, `tip_tool.py`, `slash_confirm.py`, `hermes_cli/slash_exec.py`, `oneshot.py (cli)`, `mini_swe_runner.py` | ignore                                                        | CLI/skill bridges                                                  |
