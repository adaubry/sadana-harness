# Capability import blueprint

Status: proposed. Written 2026-09-10, against `c299819`.
Cited by: (none yet — this document precedes its work items.)

**Moving target:** `GATEWAY-DAEMON-02-scheduled-and-resumable-triggers` was in
build while this was being written, and lands the `wait` node plus a scheduler.
§4.3, §5.4 and §5.6 are written against that item's `spec.md`, not against a
finished mechanism; re-check them once it closes.

Design authority for **importing hermes-agent's capability surface into sadana
as plugins**. `docs/reference/plugin_blueprint.md` settled what a plugin *is*
and closed itself as a record; this settles *which* hermes features become
plugins, in what shape, and in what order. Work items should cite it by section
(`§4.2`, `§6 Rule 3`) the way the PLUGINS items cite the plugin blueprint.

This document decides nothing on its own. Every import named here is still a
work item that enters the chain at `intent.md`.

---

## 1. The mapping error this document exists to prevent

The obvious plan — "hermes has 101 plugins, port the plugins" — is wrong, and
wrong in a way that costs months if it isn't caught first.

Of hermes's 101 `plugin.yaml` manifests, **approximately zero map to a sadana
plugin.** Counted by their own `kind:` field:

| `kind` | count | what it actually is |
| ------ | ----- | ------------------- |
| `model-provider` | 39 | a model endpoint behind hermes's provider registry |
| `backend` | 26 | an implementation behind a hermes capability registry (`web/exa` declares `provides_web_providers: [exa]`) |
| `platform` | 22 | a chat transport adapter (Slack, Discord, Matrix…) |
| `standalone` | 2 | `google_meet`, `teams_pipeline` — audio bridges |
| *(absent)* | 12 | memory backends, dashboard auth, housekeeping |

Every one of those plugs **into** a registry hermes already built. **None of
them exposes a tool.** A hermes plugin is an implementation slotted behind a
capability; a sadana plugin *is* the capability, and its `[[entry]]` is how the
agent reaches it. They are opposite constructions that share a word.

**The source list is hermes's 82 registered tools, not its plugin directory.**
A tool is a module-level `registry.register(name=…, toolset=…)` in `tools/*.py`,
imported by `discover_builtin_tools()` (`tools/registry.py:111`). That is the
surface the agent can actually reach, and it is the only surface worth porting.

Two consequences worth stating, because both invert the naive expectation:

- **The import list is far shorter than the inventory.** 82 tools collapse to
  roughly 12–15 plugins (§5). Most of the compression comes from §4's Shape B:
  a stateful multi-tool session becomes one goal-shaped entry.
- **hermes's own registries are the thing not to copy.** Nine provider
  subsystems (TTS, transcription, image gen, video gen, web search, browser,
  memory, terminal env, context engine, computer use) exist because hermes has
  many backends per capability. sadana has zero. See §6 Rule 1.

---

## 2. What the reference corpus actually holds

Counted from `docs/reference/hermes_core_blocks_kind.csv` joined against the
live repo. The two blocks the requester asked about first:

| block | CSV files | tools | plugins |
| ----- | --------- | ----- | ------- |
| BROWSER | 66 | 17 | 11 (3 browser backends, 8 web-search backends) |
| MEDIA | 51 | 5 | 10 (7 image gen, 3 video gen) |

**Both blocks are much larger than their tool counts.** MEDIA carries the whole
voice stack — `voice_mode.py`, `wake_word.py`, `transcription_tools.py`,
`tts_streaming.py`, `tts_text_normalize.py`, `neutts_synth.py`,
`voice_client_config.py`, `hermes_cli/voice.py` — none of which register an
agent-callable tool. Transcription is a provider subsystem
(`agent/transcription_provider.py` + `_registry.py`) driven by the voice loop,
never by the model. BROWSER is the same shape: `agent/browser_registry.py` and
`agent/web_search_registry.py` are block-resident and tool-invisible.

**Sizing either block by its tool count understates it by a lot.** That is a
property of every block here, and the reason §5's ledger is written against
tools rather than files.

### 2.1 The index has drifted

`hermes_core_blocks_kind.csv` lists `plugins/web/tavily/` (BROWSER) and
`plugins/image_gen/meta-ai/` (MEDIA). Neither directory exists in the corpus
today. The CSV is generated and must never be hand-edited (CLAUDE.md), so the
correction is not to patch it: **treat it as a snapshot for navigation, and
confirm against the live tree before citing a path as evidence.**

---

## 3. Other capability surfaces, and why they are not tools

Three hermes directories look like capability and are not:

- **`skills/` (13 categories) and `optional-skills/` (23 bundles)** — knowledge
  bundles, no execution. §5.2 explains why the tools around them are refused
  while the substrate is kept.
- **`optional-mcps/` (65 server configs)** — external MCP servers whose tools
  hermes registers *dynamically at runtime* (`tools/mcp_tool.py` registers
  variable names, not literals). §6 Rule 4.
- **`plugins/context_engine/`, `plugins/kanban/`, `plugins/hermes-achievements/`**
  — no manifest. Bundled apps and dashboards. `context_engine/` contains a
  single empty `__init__.py`.

---

## 4. The classifying rule

Every hermes tool falls into one of three shapes. **The shape is decided by
what has to persist between the agent's decisions, not by what the tool does.**
This is the rule to apply to anything this document does not name.

### 4.1 Shape A — one-shot procedure

Input → outside call → output, run to completion. No state survives the call.

**Becomes a plugin, near 1:1**: one `[[entry]]`, one `call` node, optionally an
`ask` node for judgement and a `route` for branching. This is where the most
capability arrives for the least architecture.

### 4.2 Shape B — an agentic session collapsed into one goal-shaped call

A loop that would be many tool calls, driven internally by the plugin's own
body, entered once with a goal and exited once with a result.

**Becomes a plugin.** hermes already made this decision and it should be read as
evidence, not invented here: `toolsets.py:57` marks `browser_exec` as
*"replaces other tools when browser.backend is browser-use"*. One tool takes a
natural-language task and runs browser-use's own agent loop; the twelve
`browser_*` tools beside it are Shape C. The same codebase ships both shapes of
the same capability, and says which replaces which.

**Browsing is a Shape B import.** Take `browser_exec`; leave
`browser_navigate`, `browser_snapshot`, `browser_click`, `browser_type`,
`browser_scroll`, `browser_back`, `browser_press`, `browser_get_images`,
`browser_vision`, `browser_console`, `browser_cdp`, `browser_dialog`.

Camofox (`tools/browser_camofox.py`, `browser_camofox_state.py`) is not a
capability — it is a fingerprint-evasion Firefox *backend* for the Shape C
tools. Wanting it is wanting Shape C, plus an anti-detection posture with no
justification in this project yet. Out.

### 4.3 Shape C — a session the model drives turn by turn

The model calls a tool, reads the result, decides the next call, and a live
session persists between them.

**Cannot be a plugin, and should not be** — but the reason has just changed, and
the new one is sharper.

`plugin_blueprint.md` §7.2 used to settle this outright: a DAG run lived and died
inside one `dispatch` call, with *"deliberately no place reserved"* for durable
run state. `GATEWAY-DAEMON-02` reserves one. A `wait` node now ends a run with
`DagResult.paused_node` set, and an inbound event on the same session key resumes
the walk with its `trace`, `artifacts` and threaded value intact.

**That does not open Shape C**, for two reasons its own spec states:

- **A resume is triggered by an external answer arriving, never by a model
  decision.** Shape C needs the model to look at a result and choose the next
  step; a `wait` node hands off to something slow and is woken by that thing
  replying. Different mechanism, different actor.
- **A resumed walk cannot reach a real `ask`** — GD-02 lists this under its own
  non-goals and resumes with an `_unsupported_ask`. So even the resumed half of a
  run has no model in the loop.

The older escape is closed too: `ask` does put a model back in the loop mid-run,
but that child is spawned with `tools=frozenset()`
(`src/sadana/plugin_dispatch.py:186`), so it can judge and cannot act — that is
`plugin_blueprint.md` §3.3 holding, not an oversight to route around.

**So the classifying line survives the change**: a plugin may now wait, but it
still may not be driven. Import a Shape C capability by finding its Shape B form
or not at all.

It is also the right outcome for the paradigm. *"An agent using a plugin becomes
an agent following an SOP"* — an SOP does not click buttons one at a time. A
Shape C capability is imported by finding its Shape B form or not at all.

---

## 5. The import ledger

Every hermes tool and plugin, with a verdict. Blocks in parentheses are from
`hermes_core_blocks_kind.csv`.

### 5.1 Already core in sadana — do not import

| hermes | sadana equivalent |
| ------ | ----------------- |
| `memory` (LEARNING) + 8 `memory/*` backends | `MEMORY-01`: `memory.py`, `memory_store.py`, `AccountKey`, builtin memory plugin. The backends are a registry family; see §6 Rule 1. hermes's answer is honcho; sadana's is deliberately different — human-curated decisions, not agent-accumulated recall. |
| `delegate_task` (DELEGATION) | `run_child` + the `ask` node. **The locus differs and sadana's is the on-paradigm one**: hermes lets the *model* decide to delegate; sadana lets the *plugin author* declare it as a node. |
| `session_search` (SESSION-STORE) | `CLI-SHELL-01` + `conversation_store.py`. |
| `observability/langfuse` | `OBSERVABILITY-01` — `record_turn` / `record_plugin_run`. |
| `plugins/context_engine/` | the CONTEXT block (C10–C12). The hermes directory is an empty namespace stub. |
| `cron_providers/chronos` | `GATEWAY-DAEMON-01`, plus `GATEWAY-DAEMON-02`'s scheduler half, in build at the time of writing. |

### 5.2 Refused by the paradigm

**`skills_list`, `skill_view`, `skill_manage` (LEARNING), `skills/`,
`optional-skills/`.** This is the paradigm shift, already written in CLAUDE.md:
agent-authored skills get messy at scale; *"For sadana, learning means a human
creates a plugin."* `skill_manage` is precisely the tool that lets an agent
write its own procedural memory. Importing it undoes the thesis.

**Keep the substrate, refuse the tools.** `skills/<name>/SKILL.md` already
exists in the plugin layout, is validated by `plugin_manifest._check_skill`, and
is loaded for `ask` nodes. A sadana skill is knowledge scoped to a procedure,
owned by a plugin, authored by a human. hermes's 36 skill bundles are raw
material to re-author, never a directory to import.

**`setup_mcp` (LIFECYCLE), `mcp_tool`, `optional-mcps/`.** MCP is explicitly in
the paradigm — *"a plugin can interact with foreign environments (mcp,
database, webhooks, apis)"*. hermes's **mechanism** is what collides: see §6
Rule 4. Import MCP as something a `call` node reaches, never as a registrar.

**`xai_video_edit`, `xai_video_extend` (MODEL-ACCESS).** Provider-specific ops
that leaked into the tool surface — note they carry the `video_gen` toolset
while their file sits in MODEL-ACCESS, which is the smell itself. CLAUDE.md:
provider-specific wire-format knowledge lives in MODEL-ACCESS. If ever wanted,
they are backend detail inside a video plugin, never entries.

**`send_message`** (unregistered in hermes). Read `tools/send_message_tool.py:2489`:
the agent must not decide on its own to fire cross-platform messages.
**hermes solved this by deleting the tool; sadana solved it more generally** —
`approve()` gates every `call` node before its body runs
(`plugin_manifest.run_graph`). Keep the gate; the capability needs no refusal.

**`react_to_message` (GATEWAY-DAEMON), `tip`, `tour` (CLIENT-SURFACE).** Client
chrome.

### 5.3 No surface — out of scope

**All ten `desktop_ui` tools** — `desktop_preview`, `annotate_preview`,
`drive_preview`, `read_window_below`, `read_terminal`, `close_terminal`,
`focus_pane`, `apply_layout`, `tour`, plus `desktop_project` (CLIENT-SURFACE).
They need a GUI renderer to answer. sadana is a WSL CLI.

One idea inside them **is** worth keeping. `toolsets.py:38` excludes these from
the core toolset and enables them only for a session whose source is the desktop
app — *"never keyed on a process env var, which is blind to a desktop client
talking to a remote/cloud backend."* **Capability gating follows the session's
actual surface, never an environment variable.** That generalizes; the tools do
not.

Also out: the four `dashboard_auth/*` backends (no dashboard); `plugins/kanban/`
and `plugins/hermes-achievements/` (web dashboards, gamification);
`google_meet` and `teams_pipeline` (realtime audio bridges); `disk-cleanup` and
`security-guidance` (housekeeping, no entry).

**The 22 `platforms/*` plugins, `discord`, `discord_admin` (CHANNELS), and the
five `yb_*` Yuanbao tools** belong to the **CHANNELS** block, not to PLUGINS —
`channel_webhook.py`, `gateway_daemon.py`, `GATEWAY-DAEMON-01` have started it.
A chat adapter is transport; it exposes no procedure. Keeping them out of the
plugin system is what prevents hermes's own shape, where `discord` is somehow
both a platform plugin and an agent tool.

### 5.4 Blocked — Shape C, or durable run state

| hermes tools | blocker |
| ------------ | ------- |
| `terminal`, `process`, `execute_code` (EXECUTION) | Shape C. A DAG-shaped version is "run one command and report", never "give me a shell". |
| `read_file`, `write_file`, `patch`, `search_files` (EXECUTION) | Shape C in practice, and hard-blocked: `execution.run_http` refuses every scheme but http/https, so a `call` body has no filesystem access at all. **Also a product question — these are coding-agent features, and sadana is not one. Phase 2 decides, not phase 1.** |
| `cronjob` (SCHEDULING) | **No longer blocked — being built.** This was the clearest case of `plugin_blueprint.md` §7.2's own stated trigger to revisit (*one plugin that genuinely cannot be expressed as run-to-completion*), and `GATEWAY-DAEMON-02` is that revisit: a scheduler that fires a stored trigger with nobody present. Import the capability *after* it closes, and note GD-02 deliberately builds the scheduler and the wait/resume mechanism as two independent pieces — do not design an import that assumes one shared interface. |
| `clarify` (GATEWAY-DAEMON) | **A genuine gap in the node vocabulary, not merely a blocked port.** See §7 OQ1. |
| The 14 `kanban_*` tools + `todo` (CONVERSATION) | Not blocked — undecided, and the decision is a product one. See §7 OQ2. |

### 5.5 Import candidates

Shape A unless noted. Ordered by ratio of capability to new architecture.

| # | capability | hermes source | notes |
| - | ---------- | ------------- | ----- |
| 1 | web search / extract | `web_search`, `web_extract` (BROWSER) | Pure HTTP, no artifact, no session. **One backend, called directly** (§6 Rule 1) — hermes has eight. |
| 2 | home automation | `ha_get_state`, `ha_call_service`, `ha_list_entities`, `ha_list_services` (CHANNELS) | **The strongest "use some software" candidate on the list**: real outside system, pure HTTP, no subprocess, no session. Four tools collapse to one plugin. hermes files it twice, as a tool *and* a `platform` plugin; only the tool half is wanted. |
| 3 | vision / video understanding | `vision_analyze`, `video_analyze` (MEDIA) | One plugin, two entries; both in `tools/vision_tools.py`. |
| 4 | image generation | `image_generate` (MEDIA) | Needs §5.6 gap 3 (artifact output directory). First real user of it. |
| 5 | video generation | `video_generate` (MEDIA) | Same gap. Import after image gen or not at all. |
| 6 | speech synthesis | `text_to_speech` (MEDIA) | Same gap. Note MEDIA's wider voice stack (§2) is *not* in scope: no wake word, no voice mode, no streaming. |
| 7 | transcription | `transcription_tools.py` (MEDIA) | No hermes tool exists — it is a provider subsystem behind the voice loop. Importing it means **authoring an entry hermes never had**, which is allowed and worth flagging as such. |
| 8 | web browsing | `browser_exec` (BROWSER) | **Shape B.** Blocked on §5.6 gap 1 (subprocess). The payoff is the largest on the list: thirteen hermes tools become one entry. |
| 9 | X/Twitter search | `x_search` (BROWSER) | Narrow; import only if actually used. |
| 10 | Spotify | `spotify` plugin (`kind: backend`) | HTTP API, self-contained, zero architectural weight. A pleasant demo, low priority. |
| 11 | Feishu docs/drive | `feishu_doc_read`, `feishu_drive_*` (CHANNELS) | Shape A in principle. Five tools of someone else's workflow — import only on real use. |

### 5.6 What blocks all of it

Four gaps. **None of them is a plugin**, and the first two block everything.

1. **`execution.py` does HTTP and nothing else.** 63 lines; `run_http` refuses
   every scheme but http/https; no subprocess, no filesystem, no process
   spawning. Every "use some software" import — browser-use (a CLI), ffmpeg,
   anything local — waits on a subprocess primitive that does not exist. This is
   an EXECUTION work item. hermes's own blueprint put EXECUTION *before* the
   execution half of PLUGINS (§2.4) for exactly this reason, and its §10 Risk 1
   says why: *a `call` node runs plugin code in-process*, acceptable only while
   every plugin is first-party. browser-use is not first-party.
2. **No per-plugin config or secrets namespace.** `plugin_blueprint.md` §12 OQ3,
   still open: *"A plugin's API key is the plugin's, and `config.py` has no
   namespace for a third party."* `config.py` offers `load_dotenv`, `env_bool`,
   `env_int`, `env_path` — nothing scoped to a plugin. Every candidate in §5.5
   needs a key. **This is the smallest item on the list and it blocks all of
   them.**
3. **Artifacts have no output directory.** `Artifact.ref` is documented as "a
   URL, or a path under the run's own output directory", and G2's spec states
   plainly that *nothing there writes a file*. Any plugin producing an image,
   an audio file, or a screenshot has nowhere defined to put it.
4. **`each` is not runnable.** `run_graph` today fails both `each` and `wait`
   with *"not runnable yet"*; `GATEWAY-DAEMON-02` takes `wait` out of that branch
   and **explicitly leaves `each` in it**. Fan-out ("search, then extract the top
   five") must therefore still loop inside one `call` body — workable, but those
   iterations are then invisible in `trace`, which is the one thing the
   declared-graph split exists to prevent. This is the only one of the four gaps
   with no work item pointed at it.

---

## 6. Rules that govern any import

Decisions, not description. Each already has authority elsewhere; this section
applies it to imports specifically so no work item re-derives it.

**Rule 1 — one capability, one plugin, one backend.** CLAUDE.md: *a registry or
dispatch seam for a family of pluggable backends earns its cost only once a
second real member exists*. hermes has 8 web-search backends, 7 image-gen, 3
browser, 8 memory, 39 model-providers — all behind registries, because hermes
has the members. Importing the registry with one member transplants nine
subsystems' worth of machinery for a family of one. **Build the single backend
as a direct call. Add the seam when the second is real, not when it is
certain.**

**Rule 2 — provider knowledge stays in MODEL-ACCESS.** A capability plugin never
learns a provider's wire format. This is what keeps §5.2's `xai_video_*` from
recurring in every import.

**Rule 3 — an imported capability is entered by goal, not by step.** §4's shapes
restated as an instruction: if an import needs the model to make a decision
*between* two outside effects, it is either two entries or one Shape B body. It
is never a plugin that pauses.

**Rule 4 — MCP never installs tools into a live conversation.** hermes's
`mcp_tool.py` registers whatever an external server advertises, at runtime.
That mutates the tool surface, and the tool surface is in the system prompt.
CLAUDE.md holds that *the system prompt is byte-stable for the life of a
conversation*, compression the single named exception, deferred invalidation the
default for anything mutating prompt state — and `plugin_blueprint.md` §8
already concedes the same point for installs. **An MCP server is a foreign
environment a `call` node reaches, with a declared entry that existed before the
conversation started.** The 65 `optional-mcps/` configs are a lookup table for
plugin authors, not 65 tool surfaces.

**Rule 5 — a node returns one value, so text and artifact need two nodes.** If a
`call` body returns an `Artifact`, `run_graph` records it and threads
`artifact.ref` onward — the text is gone. A plugin producing both a report and a
file needs the artifact-emitting `call` as its own node. This catches every
import in §5.5 items 4–8 and is easiest to discover before the graph is drawn.

**Rule 6 — execution identity crosses in on a `_sadana_`-prefixed key.**
CLAUDE.md, applied literally by the memory plugin: a plugin's account/session
identity is merged into `arguments` last so it always wins over anything the
model supplied, never threaded through a body's own signature. Every import
needing to know *who it is running for* uses this channel and no other.

**Rule 7 — audit before copying.** CLAUDE.md's methodology, restated because
imports are where it is most tempting to skip: hermes's code carries assumptions
about its registries, its config loader, and its in-process trust posture. Copy
the decision and its cost; write our own answer.

---

## 7. Open questions

**OQ1 — does the vocabulary need a `clarify` node?** hermes's `clarify` asks the
*human* a structured question mid-run. Its implementation note
(`tools/clarify_tool.py:12`) is that the interaction lives in the platform layer
and the tool is *"a thin dispatcher that delegates to a platform-provided
callback"* — which is exactly the shape of `ApproveFn`, whose `_default_approve`
blocks on `input()` in a worker thread. sadana therefore already has
human-in-the-loop, as a yes/no gate rather than a question.

Recommendation: **treat this as a candidate eighth node kind, not an import.**
`plugin_blueprint.md` §10 Risk 5 governs — every added kind is a box a
non-programmer must understand, and adding one is a work item with an intent,
not a field in a spec.

**OQ2 — should the agent drive the artifact chain?** The 14 `kanban_*` tools plus
`todo` are hermes's work-tracking surface. sadana already has one: `docs/tasks/`,
the six-stage chain, `scripts/artifact.py`. The question is not whether to port
kanban; it is whether an SDLC plugin — a DAG whose nodes are the six stages —
should exist. That is a phase-2 intent, and considerably more sadana's than
hermes's. `todo` alone (an ephemeral in-conversation scratchpad) is a much
smaller, separate thing that probably belongs to CONVERSATION.

**OQ3 — what sandbox posture does the subprocess primitive take?** §5.6 gap 1
cannot be closed without answering it, and browser-use is the first import where
"every plugin is first-party" stops being true. Deferring the answer defers
every Shape B import.

**OQ4 — do file tools belong in sadana at all?** §5.4 raises it; phase 2 answers
it. Recorded here so the question is asked deliberately rather than settled by
whoever first wants `read_file`.

---

## 8. Work items, proposed order

Nothing here is approved. Each row is a proposed `intent.md`.

| # | item | closes | depends on |
| - | ---- | ------ | ---------- |
| 1 | Per-plugin config and secrets namespace | §5.6 gap 2; `plugin_blueprint.md` §12 OQ3 | — |
| 2 | A web-search plugin, one backend, direct call | §5.5 #1; proves the whole import path end to end on HTTP alone | 1 |
| 3 | Artifact output directory | §5.6 gap 3; G2's named-but-unbuilt contract | — |
| 4 | An image-generation plugin | §5.5 #4; first real user of 3 | 1, 3 |
| 5 | EXECUTION: a subprocess primitive, sandbox posture stated | §5.6 gap 1; §7 OQ3 | — |
| 6 | A browsing plugin over browser-use | §5.5 #8; §4.2 | 1, 5 |

Items 1–4 are reachable with what exists today. Item 5 is the real work and
should be expected to produce a design fight; item 6 is cheap once it lands.

**Item 1 first, not item 2.** It is the smallest, it closes a question the
plugin blueprint left open, and every later item is blocked without it.

### 8.1 What a Shape B import looks like at the end

Item 6's manifest, to make §4.2 concrete:

```toml
[[entry]]
tool = "browse"
purpose = "Give this a goal in plain English; it drives a real browser and reports what it found."
parameters = "schema/browse.json"
start = "run"

[[node]]
name = "run"          # subprocess to browser-use; approval-gated, off the event loop
kind = "call"
body = "init:run_browser_use"
next = "judge"

[[node]]
name = "judge"        # did it achieve the goal, or stall?
kind = "route"
body = "init:classify"
ports = ["achieved", "stalled"]

[[node]]
name = "achieved"
kind = "stop"

[[node]]
name = "stalled"
kind = "stop"
```

Thirteen hermes tools become one entry, one gated outside call, and one visible
judgement step — inspectable in `DagResult.trace`, and displayable from
`plugin.toml` without executing anything.

---

## 9. Risks

1. **The registry reflex.** The single likeliest failure is importing hermes's
   provider registry alongside its first backend, because the reference makes it
   look standard. §6 Rule 1 exists for this; it will still need enforcing at
   review, since every individual case looks reasonable.
2. **Shape C arrives disguised as a feature request.** "Let the agent use the
   terminal" reads like one import and is actually a request to abandon §7.2.
   The tell is any capability where the model must decide *between* two outside
   effects. Answer with §4.3, not with a plugin.
3. **`each` stays deferred while imports need fan-out.** §5.6 gap 4 pushes the
   loop inside a body, which is invisible in `trace`. Each such body is a small
   debt against the property `plugin_blueprint.md` §5.2 item 3 was protecting.
   Two or three of them is the signal to build `each`.
4. **Import volume outruns evaluation.** Each plugin is capability the eval suite
   cannot see unless a Task grades its `DagResult` — the same failure
   `plugin_blueprint.md` §10 Risk 4 names for `text`. An import without a
   grading Task is a regression surface nobody is watching.
5. **The corpus stops being read.** Once two or three imports are done, the
   fourth is tempting to write from memory of the pattern rather than from the
   reference. The methodology is copy *decisions*, and a decision not read is
   not copied.
