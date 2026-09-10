# Spec: You shouldn't have to reintroduce yourself every time

Intent: docs/tasks/MEMORY-01-write-recall-and-forget/intent.md

## Requirements

1. A caller-supplied `AccountKey` identifies a person across many
   conversations, distinct from `ConversationKey`. (Intent: multi-user from
   the start; recall must survive across conversation keys, and today
   nothing in the codebase names a person independent of one conversation.)
2. A memory entry is stored keyed by `(account_key, entry_key)` — a natural
   key with a table constraint behind it, never a synthetic id. Writing the
   same `entry_key` again for the same account updates it in place. (Intent:
   "a person can see what has been remembered about them and remove any
   entry"; CLAUDE.md's names-not-pointers rule.)
3. Capturing a memory requires no explicit "remember this" from the human —
   the agent notices something worth keeping during ordinary conversation
   and stores it unprompted. (Intent: confirmed write trigger.)
4. What counts as worth remembering follows a rubric: a deployer-set default
   ("whatever helps the agent know this person better" if unset), plus an
   optional per-account addition that sits on top of, never replaces, the
   default. (Intent: rubric customization, per-account too.)
5. A memory-write failure never interrupts or degrades the conversation it
   happened during. (Intent constraint, verbatim.)
6. One account's memories are never used in, or leak into, another
   account's conversation. (Intent constraint, verbatim.)
7. A person can list what's stored about them and delete an entry;
   once deleted, it stops being recalled. (Intent: view/forget, in scope.)
8. This does not duplicate the verbatim transcript the CONVERSATION block
   already keeps — what's stored here is distilled content the agent (or an
   operator) chose to keep, not raw messages. (Intent constraint.)
9. The agent's instructions/behavior are not changed by this block; it
   changes only what the agent knows. No skill or instruction file is
   authored or edited by the runtime. (Intent constraint, verbatim.)

## Design

Two new modules (`memory.py`, pure; `memory_store.py`, I/O — two SQLite
tables reusing `conversation_store`'s own connection and file), one new
first-party plugin (`plugins/memory/`), one new optional parameter on
`plugin_dispatch.build_dispatch()`, and one new CLI subcommand
(`subcommands/memory.py`). Recall reaches the model through an existing,
already-unused parameter (`create_conversation`'s `system_message`);
capture reaches it through the existing plugin seam. Nothing in
`conversation.py`, `plugins.py`, or `plugin_manifest.py`'s own logic
changes.

### Reference corpus

`docs/reference/hermes_core_blocks_kind.csv`, block `LEARNING` (tier 3).
Read: `agent/memory_provider.py` (416 lines, the abstract provider contract),
`plugins/memory/honcho/session.py` (`sync_turn`/`prefetch_context`/
`dialectic_query`), `agent/curator.py` (2057 lines, skill-maintenance
orchestrator).

**Declined outright, per intent's own exclusion:** `curator.py`. It
autonomously edits, archives, and consolidates agent-authored skills — the
half of LEARNING intent.md names as not surviving this paradigm. Nothing in
this spec reads it for mechanism, only as the thing being excluded.

**Declined, audited:** the `MemoryProvider` abstract base and its
`MemoryManager` (one-external-provider-limit, pluggable Honcho/Hindsight/
Mem0/holographic backends, `sync_turn`/`prefetch`/`dialectic_query` hooks
wired into `run_agent.py`). Two specific things are wrong for this project,
not merely different:

- It is a live dependency on an external SaaS for the harness's own
  first-party capability (Honcho's dialectic endpoint, `session.py:867-950`).
  `plugin_blueprint.md §3.1`'s "plugins are immutable and external" already
  describes third-party capability; a memory store is core, and reaching an
  external network service on every turn for a core capability is a bigger,
  riskier bet than this backbone needs.
- Its hooks (`on_turn_start`, `sync_turn`, `on_pre_compress`) are registered
  directly against the agent's turn loop (`memory_provider.py`'s own
  docstring: "wired in run_agent.py"). That is exactly the kernel-inbound
  seam intent.md's "Changed during planning" section and the user's own
  framing reject. `conversation_block_blueprint.md §2.3` already rejected
  this shape once, for the same reason, for every hermes plugin hook: "a
  plugin _extends the agent_... in sadana a plugin _is invoked by the
  agent_." Memory capture is designed below to be invoked, not to extend.

**Adopted, in spirit not code:** the honcho provider's actual write
trigger — a turn's content is inspected and stored without the human
asking — is what intent.md's "the same way hermes-agent does this" answer
confirmed. What's adopted is the behavior (automatic, unprompted), not the
mechanism (a second background model call against an external deriver). The
mechanism used instead is the one already in this codebase for "the agent
decides to do something mid-conversation": calling a plugin's entry tool.

### Where this sits relative to the plugin seam (guideline 2)

PLUGINS' execution half is closed — `call`, `ask`, `route`, `stop` are all
"in the backbone" (`plugin_blueprint.md §6`), and a first-party `call` node
already runs today (`G1-call-node-run`). The caveat in this project's design
guideline ("don't invent a plugin system early") does not apply — the seam
exists and has real consumers. The full rule applies: this capability should
arrive as an addition (a plugin), not a modification to the turn loop or the
graph walker.

Recall is the other half of the feature, and it is **not** reached through a
plugin: nothing prompts the model to "go look up what it knows" at the start
of a conversation, so nothing can be gated behind the model choosing to call
a tool. Recall has to be something the harness places into context before
the first turn. `create_conversation()`'s `system_message` parameter
(`conversation.py:1057-1097`) is exactly that seam already: an opaque,
caller-supplied string, folded into the context tier once, at creation, and
never touched again for the conversation's life. Both call sites that use
it today (`chat.py:115`, `gateway_dispatch.py:90`) pass `system_message=""`
— it is unused, waiting exactly for something like this. Filling it in is
an addition to the caller, not a modification to `conversation.py`.

### Capture: a first-party plugin, one node

`src/sadana/plugins/memory/` (repository layout per
`plugin_blueprint.md §5.1`):

```
plugins/memory/
├── plugin.toml
├── schema/
│   └── remember.json
└── init.py
```

```toml
[plugin]
name = "memory"
version = "0.1.0"
description = "Remembers a durable fact or decision about the person in this conversation."

[[entry]]
tool = "memory.remember"
purpose = "Call this when you notice something worth keeping about this person for later conversations."
parameters = "schema/remember.json"
start = "write"

[[node]]
name = "write"
kind = "call"
body = "init:write_entry"
```

One node, no `next` — the degenerate, cheapest shape the vocabulary allows
(`plugin_blueprint.md §6`: "even a prompt-only capability is a one-node
DAG"; this one has an effect, but the same economy applies — no `ask`, no
`route`, because nothing here needs judgement beyond what the model, in the
parent conversation, already exercised by choosing to call the tool at all).

`schema/remember.json`: `{entry_key: string, content: string}`, both
required, `additionalProperties: false` — the model can request only these
two fields; nothing else it supplies reaches storage (see the identity
channel below).

`init.py`:

```python
def write_entry(value: dict) -> str:
    ctx = value["_sadana_memory_ctx"]          # memory_store.DispatchContext
    memory_store.write_entry(
        ctx.conn, ctx.account_key, value["entry_key"], value["content"], now=time.time()
    )
    return "remembered."
```

A `call` node's body is a plain function of one `value` (`plugin_manifest.
py:358-379` — the whole invocation is `_resolve_body(...)(value)`, run off
the event loop). `write_entry` never sees the raw model arguments alone; it
sees them merged with a trusted context object placed there by the harness,
below.

**Why not a judge-then-write DAG.** A richer design was drafted first: an
`ask` node consults a rubric, a `route` node parses its verdict, only then
does a `call` node write. Rejected: `route` does not replace `value`
(`plugin_manifest.py:360-363` reassigns nothing but `port`), so the `call`
node after it would still be operating on whatever the `ask` node's own
free-text output was — and the harness's trusted account key would have had
to survive as text through an LLM judgement hop, recoverable only by
instructing the child to echo it back verbatim. That is a reliability
problem invented for no reason: rubric guidance doesn't need to run *inside*
the plugin at all when the model that decides whether to call the plugin can
already read the rubric before deciding. Moving the rubric to the recall
side (below) gets the same customizable-guidance requirement with one node
instead of three, and one approval checkpoint instead of two.

### The identity channel — a real tension, not papered over

`run_graph()`'s node body contract is exactly one `value`: the entry's raw
arguments for the first node (`plugin_manifest.py:322-327`, citing CLAUDE.md:
"a plugin graph's node receives only its immediate predecessor's output").
There is no second channel — no `ToolContext`, nothing conversation-scoped —
reaching a node body today. `arguments` is also literally the model's own
tool-call payload. Requirement 6 (no cross-account leakage) means the
account a write lands in cannot be something the model supplies.

Resolution: `plugin_dispatch.build_dispatch()` gains one new optional
parameter,

```python
memory_context: memory_store.DispatchContext | None = None
```

(`DispatchContext(account_key: AccountKey, conn: sqlite3.Connection)`,
defined in `memory_store.py`). Inside `dispatch()`, when `memory_context` is
not `None`, the closure builds the arguments actually passed to
`run_graph()` as `{**arguments, "_sadana_memory_ctx": memory_context}` —
the trusted value merged in **last**, so it always wins regardless of
whatever the model supplied under that key (and `additionalProperties:
false` on the schema means a well-formed call from a real provider will not
even offer one). This is additive and optional exactly like OBSERVABILITY-
01's `record_turn`/`record_plugin_run` (`plugin_dispatch.py:133-134`): a
caller that never passes it gets today's behavior — `_sadana_memory_ctx` is
simply absent, and the memory plugin's own body would fail loudly (a
`KeyError`, caught by `run_graph`'s existing blanket `except Exception`,
`plugin_manifest.py:388-389`) rather than write anywhere.

`sqlite3.Connection` riding inside a plain Python dict is fine here — this
whole path is in-process function calls, nothing is serialized. Passing the
caller's own already-open connection (rather than having `write_entry` open
a second one) is the point: `conversation_store`'s single-writer posture
(CLAUDE.md) is preserved, not re-created as the exact multi-connection race
that rule warns about.

A `call` node's body only runs after `approve()` returns `True` — never
unconditionally (CLAUDE.md, verbatim). This item does not change that. See
Concerns for the resulting UX tension with requirement 3.

### Recall and the rubric: folded into an existing, unused parameter

`src/sadana/memory.py` (pure, no I/O — mirrors `conversation.py`'s own
split of state/logic from the modules that touch disk):

```python
AccountKey = str  # caller-supplied; not generated, not validated, not
                   # enforced unique here — the store's job, same posture
                   # ConversationKey and TemplateName already take.

@dataclass(frozen=True)
class MemoryEntry:
    account_key: AccountKey
    entry_key: str
    content: str
    updated_at: float

def render_recall(entries: tuple[MemoryEntry, ...]) -> str:
    """Empty string if `entries` is empty — same skip-empty-parts posture
    `_render_context` already uses for the catalog line."""

def render_capture_guidance(default_rubric: str, account_override: str) -> str:
    """One short paragraph: when to call memory.remember, using the
    default rubric, plus the account's own addition if there is one —
    'on top of', never replacing, the default (requirement 4)."""

def account_key_for(platform: str, chat_id: str) -> str:
    """`f"{platform}:{chat_id}"` — deliberately drops the thread id
    `gateway.session_key_for` includes, since an account outlives any one
    thread. A new function in a new module, not an edit to
    `gateway.py` (GATEWAY-DAEMON-01, closed)."""
```

`src/sadana/memory_store.py` (real I/O — its own file per CLAUDE.md's rule
that a module touching disk never shares a file with a block's pure-function
module; mirrors `observability.py`'s posture exactly, down to reusing
`conversation_store`'s own connection and file — no second SQLite store, no
second writer):

```python
@dataclass(frozen=True)
class DispatchContext:
    account_key: memory.AccountKey
    conn: sqlite3.Connection

def ensure_schema(conn) -> None: ...          # two CREATE TABLE IF NOT EXISTS
def write_entry(conn, account_key, entry_key, content, *, now: float) -> None: ...  # UPSERT
def delete_entry(conn, account_key, entry_key) -> None: ...
def list_entries(conn, account_key) -> tuple[memory.MemoryEntry, ...]: ...
def get_rubric_override(conn, account_key) -> str: ...       # "" if none
def set_rubric_override(conn, account_key, text, *, now: float) -> None: ...
```

```sql
CREATE TABLE IF NOT EXISTS memory_entries (
    account_key TEXT NOT NULL,
    entry_key   TEXT NOT NULL,
    content     TEXT NOT NULL,
    updated_at  REAL NOT NULL,
    PRIMARY KEY (account_key, entry_key)
);

CREATE TABLE IF NOT EXISTS memory_rubric_overrides (
    account_key TEXT NOT NULL PRIMARY KEY,
    rubric_text TEXT NOT NULL,
    updated_at  REAL NOT NULL
);
```

Both tables' primary key is the natural key requirement 1 and 2 ask for —
no autoincrement id anywhere in this block.

The deployer's default rubric is one env var, resolved where every other
block resolves its own behaviour config —
`config.env("SADANA_MEMORY_DEFAULT_RUBRIC", "whatever helps you know this person better")`
— read inside `memory.py`, not added to `config.py` itself
(`config.py`'s own docstring: "the resolution primitive every future block
uses to build its own typed config in its own module").

### Wiring: the two real callers

`chat.py:cmd_chat` and `gateway_dispatch.py:handle_inbound`, at the
`create_conversation()` branch:

```python
entries = memory_store.list_entries(conn, account_key)
override = memory_store.get_rubric_override(conn, account_key)
system_message = "\n\n".join(filter(None, [
    memory.render_recall(entries),
    memory.render_capture_guidance(default_rubric, override),
]))
conversation, _template = create_conversation(template, key, system_message, ...)  # was ""
```

`account_key` for `chat.py`: a new `--account` flag, defaulting to
`config.env("SADANA_MEMORY_ACCOUNT", "local")`. For `gateway_dispatch.py`:
`memory.account_key_for(event.platform, event.chat_id)`.

Both call sites also pass `memory_context=memory_store.DispatchContext(account_key, conn)`
into `plugin_dispatch.build_dispatch(...)` — on **every** turn, not only the
conversation-creation branch, since capture (unlike recall) is a live plugin
call available for the whole life of the conversation, not fixed at
creation.

`eval_harness.py`'s own `create_conversation()` call is untouched — evals
are not an end-user surface (Non-goals).

### CLI: view, forget, and set your own rubric

`src/sadana/subcommands/memory.py`, following the existing pattern
(`subcommands/conversations.py`, `subcommands/runs.py`,
`subcommands/plugin.py`): `sadana memory list <account>`, `sadana memory
forget <account> <entry_key>`, `sadana memory set-rubric <account> <text>`.
Direct reads/writes against `memory_store`, no model involved — requirement
7 (and the "per end-user too" rubric answer) has to work even if the model
never volunteers to expose it, so it is not implemented as a plugin tool the
model must be asked to call correctly.

## Interface

```python
# memory.py (pure)
AccountKey = str
class MemoryEntry(NamedTuple/dataclass): account_key, entry_key, content, updated_at
def render_recall(entries) -> str
def render_capture_guidance(default_rubric, account_override) -> str
def account_key_for(platform, chat_id) -> str
def default_rubric() -> str   # reads SADANA_MEMORY_DEFAULT_RUBRIC

# memory_store.py (I/O)
class DispatchContext: account_key, conn
def ensure_schema(conn) -> None
def write_entry(conn, account_key, entry_key, content, *, now) -> None
def delete_entry(conn, account_key, entry_key) -> None
def list_entries(conn, account_key) -> tuple[MemoryEntry, ...]
def get_rubric_override(conn, account_key) -> str
def set_rubric_override(conn, account_key, text, *, now) -> None

# plugin_dispatch.py — additive parameter, default None, no behavior change otherwise
def build_dispatch(..., memory_context: memory_store.DispatchContext | None = None) -> ...
```

Errors: `write_entry`/`delete_entry`/`set_rubric_override` raise
`sqlite3.Error` on failure exactly like every other `conversation_store`-
adjacent write; inside the plugin's `call` node this is already caught by
`run_graph`'s blanket exception handler and turned into a `failed_node`
DagResult (requirement 5) — nothing new needed there. The CLI subcommand
lets `sqlite3.Error` surface as a normal CLI failure (exit code, message);
it is a foreground operator command, not a background write inside someone
else's turn.

## Acceptance criteria

- [ ] A fact captured for account A in one conversation renders into
      `system_message` for a **new** conversation created for account A, and
      never renders for a different account B's conversation.
- [ ] Deleting an entry via `sadana memory forget` removes it from a
      subsequently created conversation's recall text.
- [ ] `sadana memory set-rubric` for one account changes that account's
      `render_capture_guidance()` output; a different account's output is
      unaffected; the deployer's default text is still present in both.
- [ ] A simulated `sqlite3.Error` from `memory_store.write_entry` inside the
      plugin's `call` node results in a `failed_node` DagResult, not a
      raised exception out of `dispatch()`/`run_turn()`.
- [ ] `memory_entries` and `memory_rubric_overrides` each enforce their
      primary key at the database level — a second write with the same
      natural key updates the row rather than creating a second one.
- [ ] No test in this item reads a `.py` source file's text
      (testing-conventions); `list_entries`/recall behaviour is asserted
      against a tmp-path sqlite file via the store functions, not by
      inspecting `init.py` or `memory.py`'s source.
- [ ] `create_conversation`, `TemplateRecipe`, `Conversation`,
      `run_graph`, and every existing `ToolSpec`/`Node`/`Manifest` field are
      unchanged by this item — the only kernel-adjacent diff is the two new
      optional `build_dispatch()` parameters.

## Non-goals

- Cross-account aggregation, analytics, or any reasoning over more than one
  account's memories at once.
- Retroactive reclassification of existing entries when a rubric changes
  (see Open questions).
- A chat-UI or dashboard surface for view/forget/set-rubric — the CLI is
  the backbone-level guarantee that the operation exists and is
  deterministic; a real product's own UI calling the same store functions
  is a later, product-specific concern.
- Recall refreshing mid-conversation. Fixed at `create_conversation()` time,
  matching the byte-stability contract; a long-lived conversation that is
  never recreated will not pick up memories captured after it started.
- `eval_harness.py` wiring.
- Any change to `plugin_manifest.run_graph()`'s node-body contract, the
  approval gate, or the node vocabulary.

## Rejected alternatives

1. **Hermes's `MemoryManager`/`MemoryProvider`, ported.** Declined per the
   reference-corpus audit above: external SaaS dependency for a core
   capability, and hooks registered directly against the turn loop — the
   kernel-inbound seam intent.md explicitly rejects.
2. **A `Recorder`-style kernel hook for capture, mirroring
   OBSERVABILITY-01** (`capture_memory` callable threaded through
   `build_dispatch`/`take_turn_and_reconcile`, called after every turn with
   the turn's messages). Rejected: OBSERVABILITY needed a hook because
   nothing else can see `TurnResult`/`DagResult` internals from outside the
   turn loop. Capture needs no such internals — it needs only what the
   model itself chooses to say — and the plugin seam already exists for
   exactly that. Building a second way to reach a model mid-conversation
   would be two paths to the same kind of thing, which
   `conversation_block_blueprint.md §2.3` already rejected once for
   hermes's ~30 loop hooks.
3. **A judge-then-write DAG** (see "Why not a judge-then-write DAG" above)
   — a real design considered and abandoned once the identity-channel
   problem showed it would need an unreliable verbatim echo through an LLM
   hop.
4. **A fixed taxonomy of memory types** (the shape this very session's own
   auto-memory feature uses: user/feedback/project/reference). Rejected
   explicitly during planning (intent.md, "Changed during planning") — one
   category, a customizable rubric instead of a fixed set of kinds.
5. **Folding the account's rubric override into the same row/blob as the
   deployer's default**, written once at some setup step. Rejected: the
   default lives in the plugin's own shipped version and changes when the
   deployer installs a new one; a per-account row is what makes it
   independently updatable without a migration every time the default
   changes (requirement 4's "floor plus adjustment").

### State inventory (guideline 4)

- `memory_entries` row per `(account, entry_key)`: the entire point of the
  feature — cannot be derived from anything else in the system.
- `memory_rubric_overrides` row per account: must outlive any single
  conversation and be settable independent of one, so it cannot be derived
  either.
- No new field on `Conversation`, `ConversationTemplate`, `TurnResult`, or
  `DagResult`. Recall rides in an existing, already-unused string parameter;
  capture rides in an existing, already-optional dispatch parameter slot
  pattern. Zero new mutable kernel state.
- `AccountKey` itself is not stored as a column anywhere it doesn't already
  need to be a primary-key component — it is recomputed at each call site
  from context the caller already has (a CLI flag, or the inbound event),
  never cached or duplicated.

## Open questions

1. **Rubric change vs. existing entries.** If a deployer or account changes
   the rubric after entries already exist, are they reclassified?
   Recommendation (unresolved in intent.md, carried forward): no automatic
   reclassification — a rubric only ever governs future capture decisions;
   an existing entry is removed only by an explicit `forget`. This is the
   derived-state-over-stored-state answer (no migration, no reclassification
   pipeline) but was never put to the user, so it stays open.
2. **Long-lived conversations and stale recall.** A gateway channel that
   keeps one `ConversationKey` per person forever (never recreating it) will
   never see anything captured after its first turn, per the byte-stability
   contract. No mechanism in this item addresses that; whoever wires up a
   real channel needs a conversation-recreation policy, which is product
   shape, not this block's.
3. **Approval friction for an "automatic" feature.** See Concerns.

## Concerns

**Approval gate vs. "no explicit ask."** Requirement 3 says capture must
feel automatic; CLAUDE.md's rule says a `call` node never runs without
`approve()` returning `True`. `_default_approve` is interactive — every
single memory captured at a WSL terminal would trigger a `y/N` prompt,
which is exactly the friction the intent explicitly ruled out. This spec
does not resolve the conflict by special-casing the memory plugin inside
`run_graph` or `_default_approve` (that would violate "never unconditionally
... never for ask/compute/route/stop" by inventing a plugin-name exception
inside the kernel). It resolves it at the level the codebase already makes
configurable: whoever wires `build_dispatch()` for a real deployment
supplies their own `approve` — permissive for the first-party `memory`
plugin, interactive for everything else. That is a deployment decision, not
a code change, and it is the single thing a build-stage implementer should
not quietly paper over by making `_default_approve` permissive in general.

**Trust in a value that outlives the model.** `_sadana_memory_ctx` is safe
against a spoofing model only because it is merged into `arguments` last, by
code the model never runs. A future node kind or a future change to
`run_graph()`'s merge order that put trusted keys first instead of last
would silently reopen requirement 6. Nothing enforces "last wins" as a type-
level invariant; it is a convention this spec states and a reviewer should
check by reading the actual merge line, not by trusting this document.

**No test in this item may read `.py` source** (testing-conventions), which
is why every acceptance criterion above is phrased as a behavior through the
public functions, not a check on `plugin.toml`/`init.py`'s literal contents.
The one thing that cannot be tested that way is the schema file's
`additionalProperties: false` — that has to be asserted by attempting a
call with an extra field and checking it's rejected or ignored, not by
opening `remember.json`.

**Policy skills not applied.** `.claude/skills/` contains no
`project-structure` or `reference-lookup` skill in this repository — both
named in design-skill's own checklist do not exist here, so neither was
applied. `testing-conventions` was loaded and applied throughout (the
fixture/tmp-path rule, the source-reading ban, and the invariant-not-
snapshot rule all shaped the acceptance criteria above).

Twelve-plus words, as required: this section is the trace of what a
reviewer should distrust hardest, not a formality.
