# PLUGINS block blueprint

Status: implemented. Written 2026-09-06, against `73ac4f0` (work order step 12); closed 2026-09-07 by `G3-real-plugin-under-eval`.
Cited by: D1-D4, E1, F1, G1, G2, G3 (`docs/tasks/`).

Design authority for the block that turns sadana's paradigm into a contract.
Work items cite it by section (`§5.2`, `§10 Risk 2`) the way the CONVERSATION
block's items cite `conversation_block_blueprint.md`.

---

## 1. Why this block exists

The paradigm says every capability is a plugin, and _even a prompt-only
capability is a plugin with one SKILL.md and a single-node DAG_. That sentence
has a consequence the work order does not: there is no path from the agent to
any capability that does not go through this block. It is not an extension
mechanism bolted to a core; it is the only way capability is reached.

The reference corpus disagrees, and the disagreement is inherited. In
hermes-agent, PLUGIN-SYSTEM is tier 3 — 88 files, 220 inbound edges, sitting
beside BROWSER and MEDIA — because hermes's capabilities are core code and
plugins are an optional seam. Copying that tier copies hermes's _answer_ to a
question sadana answers differently. Here this block is tier 1.

**The first plugin already ran.** `scripts/prove_conversation_e2e.py:121-160`
is a hand-written DAG — its own comments name "node 1: webhook-ish input node",
"node 2: subagent-with-skill node", then a branch on the child's reply —
executed against a live model, with two fixture plugins on disk at
`tests/fixtures/plugins/`. The mechanism works. It lives in a proof script
because no contract exists for it to live in.

---

## 2. When this block gets built

The work order is at **step 12 (CONTEXT)**. Ahead of it: 13 EXECUTION, 14
SAFETY, 15 checkpoint, 16 PLUGIN-SYSTEM. This block does not slot into step 16.
It splits in two around steps 13-14, and the split is a real dependency line,
not a preference.

### 2.1 What has no prerequisite (build now, before step 13)

Nothing in this half needs a line of code that does not already exist:

- the manifest, its parser, and load-time validation (§5)
- `DagResult` and the dispatch signature change (§7) — touches three closed
  functions, needs nothing new
- catalog and tool-surface production from installed manifests (§8) — `ToolSpec`
  and `PluginCatalogEntry` already exist and already reserve this producer
- the graph walker and the node kinds that have no outside effect: `compute`,
  `ask`, `route`, `stop` (§6). `ask` is `run_child()`, which is built and proven.

This half **is** the milestone as you described it — core paradigm constraints,
state contracts, abstraction interfaces — and it is buildable today.

### 2.2 Why it must come before step 13, not after

Step 13 is "EXECUTION — one sandbox backend", scoped from hermes, where
EXECUTION means eight terminal backends behind a provider registry. In sadana,
EXECUTION's real question is narrower and different: **what may a `call` node
do, and in what?** That question is unanswerable until a `call` node has a
definition, and a `call` node gets its definition here.

Build step 13 from hermes's scope and you build terminal backends for an agent
whose capability arrives through plugins. Build this half first and step 13
gets re-scoped by a contract instead of by a reference codebase.

### 2.3 What genuinely waits (after steps 13-14)

- **`call` nodes that run code with an outside effect** — needs EXECUTION.
  A `call` node that hits an HTTP API is `httpx` in-process; a `call` node
  running a marketplace author's code is a sandbox question.
- **Approval on effectful nodes** — needs SAFETY. B2's spec already reserves
  the shape: classify a call by expected effect, checkpoint before a
  side-effecting one.
- **Real plugins and the ported proof script** — the block's deploy evidence
  needs both of the above.

### 2.4 The resulting order

| step | was                                                   | becomes                                                           |
| ---- | ----------------------------------------------------- | ----------------------------------------------------------------- |
| 12   | CONTEXT                                               | unchanged — in progress                                           |
| 13   | EXECUTION (8 terminal backends)                       | **PLUGINS, contract half** (§11 items 1-5)                        |
| 14   | SAFETY                                                | EXECUTION, re-scoped: what a `call` node may do                   |
| 15   | checkpoint: multi-turn task with tools under approval | SAFETY: approval on effectful nodes                               |
| 16   | PLUGIN-SYSTEM                                         | **PLUGINS, execution half** (§11 items 6-8)                       |
| 17   | —                                                     | checkpoint: _a real plugin's DAG runs end to end, under approval_ |

Checkpoint 15 does not disappear, it moves and gets sharper. "A multi-turn task
with tools under approval" was hermes's shape for that milestone. Sadana's
equivalent is a plugin DAG running under approval, which is the same claim
about the same machinery, stated in this project's own terms.

---

## 3. What this block must satisfy

1. **Plugins are immutable and external.** The runtime never authors, edits or
   versions a plugin. Nothing in `src/` writes to a plugin directory.
2. **The repo is the plugin.** The distributable form is a git repository at a
   tag; the layout the runtime reads is the repository layout, unmodified. No
   build step, no packaging format.
3. **Hybrid determinism.** Control flow is data and is deterministic. Judgement
   is a prompt and is not. A node either runs code or consults a model, never
   both, so every non-deterministic step is one visible box.
4. **One shape all the way down.** A prompt-only capability is a one-node DAG.
   No simpler second path — a special case here is a second paradigm.
5. **A no-code editor is the end state.** Not now, but the contract is written
   as though it existed: anything a plugin author expresses must be
   expressible as boxes and arrows, round-trippable, and displayable without
   executing the plugin.

Constraint 5 is new to this draft and it decides §5.2.

---

## 4. What already exists that this block must not break

Citations are `src/sadana/conversation.py` unless stated.

| L                 | What                                              | Why it matters here                                                                                                     |
| ----------------- | ------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------- |
| 715, 1059, 1279   | `dispatch: Callable[[str, dict], Awaitable[str]]` | The seam a DAG is entered through. §7 changes its return type and nothing else.                                         |
| 162               | `ToolSpec(key, name, parameters, describe)`       | Its docstring declines `handler`/`effects` explicitly — those belong here, not there.                                   |
| 909               | `PluginCatalogEntry(name, purpose, entry_tool)`   | Docstring: "a future PLUGINS block is the real producer of this data."                                                  |
| 1108-1139         | `SkillRef`, `_plugins_root()`, `_skill_path()`    | Already reads `<plugins_root>/<plugin>/skills/<skill>/SKILL.md` from `SADANA_PLUGINS_DIR`. §5.1 is already implemented. |
| 1167              | `load_skill()`                                    | agentskills.io frontmatter, 1024-char description ceiling.                                                              |
| 1243, 1272        | `ChildSpec`, `run_child()`                        | §6's `ask` node is a thin wrapper over this.                                                                            |
| 184               | `build_surface()`                                 | Already raises on duplicate tool names — how two plugins claiming one entry tool fail loudly.                           |
| `config.py:31-83` | `env()`, `env_path()`, `get_paths()`              | Where plugin-facing settings resolve.                                                                                   |

No second way to do any of the above. A skill loads through `load_skill`; a
subagent spawns through `run_child`.

---

## 5. The plugin on disk

### 5.1 Layout

```
example-plugin/
├── plugin.toml          # identity, entries, and the graph — §5.2
├── skills/
│   └── example-skill/
│       └── SKILL.md     # already read by load_skill()
├── deps/                # connectors, MCP adapters, outside-system setup
└── init.py              # node bodies: the code `call` and `compute` nodes run
```

`skills/` and `SKILL.md` are already fixed by `_skill_path()` and
`load_skill()`. This block adds `plugin.toml`, `deps/` and `init.py`.

### 5.2 The graph is data. The node bodies are code.

The most consequential choice in this block, and constraint §3.5 settles it.

A visual editor produces boxes and arrows. It cannot produce Python, and it
cannot read Python back to redraw what a programmer wrote. So **the graph —
nodes, their kinds, their outgoing ports, and what each port connects to —
lives in `plugin.toml` as data.** Sadana walks it. `init.py` supplies only the
_bodies_: the function a `call` node runs, the expression a `compute` node
evaluates.

Four things fall out of that split, none of which are available if `init.py`
owns control flow:

1. The editor round-trips. A programmer's hand-written graph and a dragged one
   are the same artifact.
2. The marketplace displays a plugin's shape **without executing an untrusted
   repository** — a hard requirement for §3.2 that no amount of static
   analysis of arbitrary Python would satisfy.
3. The trace in §7 is derivable from the graph, not reconstructed by
   instrumenting someone else's code.
4. Suspension (§7.2, deferred) becomes a position in a declared graph rather
   than a Python call stack, which is the difference between a schema change
   and a rewrite.

The cost: a plugin author cannot express arbitrary control flow. That is the
point — §3.3 says control flow is deterministic and inspectable, and arbitrary
Python is neither. A procedure that genuinely needs control flow the vocabulary
lacks is a signal to extend §6, deliberately, not to hand the plugin a `while`.

```toml
[plugin]
name = "example-plugin"
version = "0.3.1"            # the git tag; the runtime never sets it
description = "One sentence, rendered verbatim into the catalog."

[[entry]]
tool = "example_do_thing"
purpose = "One sentence the model reads."
parameters = "schema/do_thing.json"
start = "fetch"

[[node]]
name = "fetch"
kind = "call"
body = "init:fetch_record"   # module:function in init.py
next = "interpret"

[[node]]
name = "interpret"
kind = "ask"
skill = "example-skill"
next = "route_on_kind"

[[node]]
name = "route_on_kind"
kind = "route"
body = "init:classify"       # returns a port name
ports = ["urgent", "normal", "unknown"]

[[node]]
name = "notify"
kind = "call"
body = "init:send_alert"
```

Edges are `next` (one successor) or `ports` (named successors). A node with
neither is terminal.

---

## 6. The node vocabulary

A node is **one effect** plus **a port shape**. Keeping those separate is what
lets the vocabulary grow without the executor's dispatch growing with it, and
it is how a visual editor renders: the effect is the box's icon, the port shape
is how many arrows leave it.

The vocabulary is a programmer's control-flow primitives, minus the ones that
cannot be drawn, plus the one thing no automation tool has — asking a model.

| kind      | programmer's equivalent                | editor block   | ports out  | in the backbone?       |
| --------- | -------------------------------------- | -------------- | ---------- | ---------------------- |
| `compute` | an expression, an assignment           | Formatter      | 1          | **yes**                |
| `ask`     | — (sadana's own)                       | Ask the agent  | 1          | **yes**                |
| `route`   | `if` / `else`, `match` / `case`        | Paths          | N named    | **yes**                |
| `stop`    | early return, a guard clause           | Filter         | 0          | **yes**                |
| `call`    | a function call with an outside effect | Action         | 1          | after EXECUTION (§2.3) |
| `each`    | `for` over a collection                | Loop           | item, done | deferred (§10 Risk 2)  |
| `wait`    | awaiting an external event             | Trigger, Delay | 1          | Phase 2 (§7.2)         |

Notes that are decisions, not description:

- **`compute` and `call` are separate kinds**, though a programmer writes both
  as a function. `compute` is pure; `call` reaches outside. That line is
  already project vocabulary — B2's spec distinguishes tool results with no
  lasting effect from ones that mutated something — and it is what lets SAFETY
  gate exactly one kind rather than inspecting every body.
- **`route` has N named ports, not a boolean.** An `if` is a `route` with ports
  `["yes","no"]`. Modelling it as a boolean would have made a 5-way router a
  new node kind later; named ports make it free. This is the cheapest
  graft-host decision in the document.
- **`ask` is the only non-deterministic kind.** §3.3 in one line: if a
  procedure needs judgement _and_ an effect, that is two boxes, and the
  judgement is visible as its own box in the trace.
- **There is no `output` node.** Any node may emit an `Artifact` (§7.1). A
  dedicated output kind would suggest artifacts only appear at the end, which
  is false for anything that writes a file mid-run.
- **`each` is deferred but not foreclosed.** §7.1's `NodeTrace.visit` exists
  solely so a node that runs more than once is representable when it lands.

---

## 7. The state contract

### 7.1 `dispatch` returns a value, not a string

Today: `dispatch: Callable[[str, dict], Awaitable[str]]` (L715).
Proposed: `dispatch: Callable[[str, dict], Awaitable[DagResult]]`.

A string return means the transcript records that _something_ happened and
nothing about what. Four things break at once, all visible in the repo today:

1. `CONV-10`'s own commit: _"a dispatch handler still has no channel to report
   anything back to its own caller beyond a result string, left exactly as open
   as before for whichever future work item builds real plugin dispatch."_
2. An error and a result are the same type — B2's rule that a classified
   failure is returned as a named outcome, never raised, holds everywhere
   except here.
3. A plugin's output ("a link, an md file") has no typed channel.
4. `eval_harness.py` grades structurally by inspecting message history. With a
   string return, **DAG execution is invisible to the eval suite** — the one
   mechanism meant to catch behavioural regressions across the capability
   fan-out cannot see inside the thing that will carry all capability.

```python
@dataclass(frozen=True)
class Artifact:
    kind: str          # "link" | "file"
    name: str
    ref: str           # URL, or a path under the run's output directory

@dataclass(frozen=True)
class NodeTrace:
    node: str          # node name, from the manifest
    kind: str          # §6
    visit: int         # 0-based; > 0 only once `each` exists (§6)
    ok: bool
    port: str | None   # which port was taken, for a `route`
    detail: str | None # one line, for the transcript

@dataclass(frozen=True)
class DagResult:
    plugin: str
    entry: str
    text: str                        # what the model sees as the tool result
    artifacts: tuple[Artifact, ...]
    trace: tuple[NodeTrace, ...]
    failed_node: str | None          # None == the run reached a terminal node
```

`run_turn` renders `DagResult.text` into the tool-result message exactly where
it renders the string today, so the transcript's shape is unchanged and
`_cap_tool_result` (L661) still applies. Everything else is new information the
turn loop may record and the eval harness may grade.

### 7.2 In-process only, and what that forecloses

**A DAG run lives and dies inside one `dispatch` call.** No suspension, no
resume, no durable run state, and deliberately no place reserved for one.
`DagResult` is always terminal.

Right for the backbone; the cost is recorded so it is not rediscovered. A
`wait` node cannot exist, because it would outlive the tool call that started
it. The trigger to revisit is one plugin that genuinely cannot be expressed as
a run-to-completion DAG — not one that would merely be nicer with a listener.
When it arrives, §5.2's declared graph is what makes the change a schema
addition (a run's position is a node name) rather than a rewrite.

### 7.3 Control flow: who drives

The agent is the outer loop. A human talks to an agent, the agent's tool
surface is built from installed plugins, and calling an entry tool enters a
DAG. This is what exists (L993-1101) and what _"an agent using a plugin becomes
an agent following an SOP"_ describes.

A DAG-first runtime, where a conversational turn is one node kind, was
rejected: it subordinates a built and proven block to one that does not exist,
and it does not describe a product a person converses with.

---

## 8. What the agent sees

**Catalog.** Each `[[entry]]` contributes one `PluginCatalogEntry` (L909),
rendered into the prompt's context tier by `_render_context()` (L986).
Installing a plugin mid-conversation is a `defer_invalidation()` (L947), never
an in-place prompt edit.

**Tool surface.** Each `[[entry]]` contributes one `ToolSpec` (L162): `name`
from `entry.tool`, `parameters` from the referenced schema file, `describe`
composed at build time from the resolved set, per CLAUDE.md's rule that no
cross-reference is ever a literal.

**Validation at load, not at call.** Manifest parses; every `parameters` schema
file exists and is valid JSON Schema; every `ask` node's skill resolves to a
real `SKILL.md`; every node name is unique; every `next`/`ports` target names a
declared node; every `body` resolves to a callable in `init.py`; the graph
reaches every declared node from some entry. That last pair is what keeps
§5.2's data honest against its code.

---

## 9. Boundaries — what this block does not decide

- **The marketplace**: upload, discovery, the release webhook.
- **Installation**: fetching a repo at a tag into `SADANA_PLUGINS_DIR`. This
  block assumes plugins are already there, as `_plugins_root()` already does.
- **Sandboxing**: EXECUTION's question (§2.3).
- **Immutability enforcement**: §3.1 is a rule the runtime obeys, not one it
  polices. Verifying an install matches its tag is an installation concern.
- **Versioning and upgrade**, including what happens to a live conversation
  when an installed plugin changes.
- **`deps/` resolution**: how third-party dependencies get installed.
- **The editor itself.** §3.5 shapes this contract; it does not schedule the
  tool.

---

## 10. Risks

1. **A `call` node runs plugin code in-process until EXECUTION lands.**
   Acceptable while every plugin is first-party; unacceptable the day one is
   installed from a source the user did not write. §2.4 puts EXECUTION before
   the execution half for exactly this reason.
2. **`each` lands and the trace cannot hold it.** Mitigated by
   `NodeTrace.visit` existing from day one. If it is dropped as "unused," the
   next person pays for it with a schema migration.
3. **The manifest and `init.py` drift.** §8's load-time `body` resolution
   catches a missing function; it cannot catch a function that ignores its
   inputs. A node declared and never reached is detectable statically
   (reachability); a node that lies about what it does is not.
4. **`DagResult.text` becomes a dumping ground.** If authors write prose there
   instead of structuring `trace` and `artifacts`, the type is decorative. The
   eval harness is the check: a Task that grades on `trace` rather than prose
   keeps the structure load-bearing. §11 item 8.
5. **The vocabulary is too small and grows by exception.** Every added kind is
   a box in an editor a non-programmer has to understand. Adding one should be
   a work item with an intent, not a field in a spec.

---

## 11. Work items

Sized against this block's own precedent — CONVERSATION ran nine.

**Contract half — before step 13.** Needs nothing that does not exist.

| #   | Item                                    | Closes                                                                                                                                                             |
| --- | --------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| 1   | `DagResult` and the dispatch signature  | The three dataclasses; `run_turn`/`take_turn`/`run_child` take the new type; `text` renders where the string rendered. Wide diff across closed blocks, like C10's. |
| 2   | Manifest parse and load-time validation | `plugin.toml`; §8's checks; failures are named outcomes, not raises. No execution.                                                                                 |
| 3   | Catalog and tool surface from manifests | `PluginCatalogEntry` and `ToolSpec` produced from installed plugins; duplicate entry tools fail loudly.                                                            |
| 4   | The graph walker                        | Walks declared nodes and ports, accumulates `trace`. `compute` and `stop` only.                                                                                    |
| 5   | `ask` and `route`                       | `ask` wraps `run_child`; `route` resolves a port name from a body. The one-node prompt-only plugin (§3.4) works end to end here.                                   |

Item 1 first, not last: it changes three already-closed functions, and every
later item is cheaper once it has landed.

**Execution half — after steps 13-14.**

| #   | Item                      | Closes                                                                                                                                |
| --- | ------------------------- | ------------------------------------------------------------------------------------------------------------------------------------- |
| 6   | `call` nodes              | Bodies that reach outside, inside whatever EXECUTION defines.                                                                         |
| 7   | Artifacts                 | Where a file lands, how a link is returned.                                                                                           |
| 8   | Port the proof script     | `prove_conversation_e2e.py`'s hand-written DAG becomes two real plugins; the script shrinks to a driver. The block's deploy evidence. |
| 9   | An eval that grades a DAG | A Task whose grading function reads `trace`. Closes Risk 4.                                                                           |

**Closed.** Items 1-9 are done; the step-17 checkpoint (`§2.4`) — a real
plugin's DAG running end to end, under approval — is met. Items 8 and 9
landed together in `G3-real-plugin-under-eval`, by explicit decision: item
9 needed item 8's real plugins to have anything real to grade, and neither
was worth shipping without the other. `plugin-a`'s stand-in webhook step
was upgraded to a genuine `call` node rather than ported as-is, since a
compute stand-in would never have exercised approval at all — the step-17
checkpoint's own wording is what settled that. Items 6 and 7 had already
closed as `G1-call-node-run` and `G2-artifacts`; this block's own document
is now a closed record, not a live design surface — a future change to
this vocabulary is a new intent, not an edit here.

---

## 12. Open questions

1. **What does a node's body receive, and return?** The whole run's accumulated
   state, or only its predecessor's output? Whole-state is more expressive and
   makes every node's behaviour depend on everything before it;
   predecessor-only keeps a node testable in isolation, which
   `testing-conventions` favours, and is what a visual editor can draw as a
   wire. Decide in item 4 — it is the shape of the entire node API.
2. **Can one plugin's node call another plugin's entry tool?** Composition is
   valuable and turns the installed set into a graph with a cycle risk.
   Recommend: no, in the backbone.
3. **Where do a plugin's own settings and secrets live?** CLAUDE.md puts
   behaviours in config and secrets in `.env`. A plugin's API key is the
   plugin's, and `config.py` has no namespace for a third party.
4. **What is in `DagResult.text` when `failed_node` is set?** The model needs
   something useful and must not be handed a stack trace.
5. **Is `deps/` importable as a package or loaded by path?** Interacts with §9's
   deferred dependency resolution.
6. **Does `route`'s body run as `compute`, or is a port chosen declaratively?**
   A predicate per port (`when = "..."`) is drawable and inspectable; a body
   returning a port name is more expressive and opaque. §3.5 leans declarative.
