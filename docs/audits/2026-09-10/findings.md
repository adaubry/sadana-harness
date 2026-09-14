# Audit findings — 2026-09-10

Method: audit-skill. Fifteen blind pass-one reports (A–O, one subagent per
block, no access to intent/spec/plan or block names) + one pass-two report
(read intent.md/spec.md only, no code) + this three-way comparison against
`key.md`'s problem statements, done last.

Legend: **A** = what the code does (pass one) · **B** = what the work items
said they'd do (pass two) · **C** = what the plan said the block was for
(key.md problem statement).

---

## A — CONFIG as a contract (`ad5d185`)

- **C:** one place to answer "where do my files live" / "which model am I
  using", before anything starts asking.
- **B:** a typed env-access contract (paths + `env`/`env_bool`/`env_int`/
  `env_path`) that scales to N blocks reusing it, no secrets.
- **A:** `src/sadana/config.py` — `get_paths()` (state_dir, config_dir) +
  four generic typed-env primitives. No consumers wired yet (deliberate,
  bootstrap-only).

**A = B = C. Verdict: SOLVED.**

---

## B — The conversation cycle, on paper (`4c2e954`)

- **C:** write down who calls whom between CONTEXT / MODEL-ACCESS /
  CONVERSATION, on paper, before any of the three exist.
- **B:** design-only spec naming every boundary and failure mode, buildable
  against stubs.
- **A:** `docs/tasks/B2-cycle-contract/{intent,spec}.md` only — no `src/`
  touched, interfaces match B's description exactly.

**A = B = C. Verdict: SOLVED.**

---

## C — MODEL-ACCESS v0 (`f81d52b`)

- **C:** send a message to a model, get an answer back — one provider,
  working for real.
- **B:** prove `send()` against OpenRouter with a genuine round trip;
  preserve all 39 hermes provider identities without porting their code.
- **A:** `model_access.send()` + `classify()`, one working provider
  (OpenRouter), 38 name-only stubs raising `ProviderNotWired`.

**A = B = C. Verdict: SOLVED.**

---

## D — CONVERSATION (10 commits, `116e59c`…`c3603a3`)

- **C:** run the full think → tool → result → think → stop cycle; keep
  history straight.
- **B:** ten items building this up in strict sequence — transcript
  invariants, tool surface, budgets, provider port, turn loop, the
  continuing `Conversation` aggregate, `run_child`, a deploy-evidence
  proof, and one bugfix to that proof's own state handling.
- **A:** matches item for item — append/repair-only history, `consume_*()`
  budgets returning new-value-or-None, `complete()`/`run_turn` with 8 named
  `ExitReason`s, `Conversation`/`take_turn`, `run_child` with lineage-encoded
  keys.

**A = B = C. Verdict: SOLVED**, with one self-corrected note: this block's
own commits added skill-file loading (`load_skill`, frontmatter parsing) and
a `SADANA_PLUGINS_DIR` config key directly into `conversation.py` — a PLUGINS
concern, by this project's own later rule ("plugins.py and plugin_manifest.py
never import conversation.py"). Block I's `46b1067` already relocated this
out into `plugins.py`/`plugin_manifest.py`. No action needed — flagging only
because it was a real, if temporary, boundary violation.

---

## E — Eval harness (`1634433`)

- **C:** `make verify` proves crash-free, not behavior-correct; need a way
  to write "when asked this, it should do that" and check it automatically.
- **B:** `Task` = prompt + plain grading function, run once via a real
  `Conversation`, graded programmatically (never by a second model), result
  saved; proved with one deliberately minimal smoke task.
- **A:** `eval_harness.Task`/`run_task()`/`save_result()`, `SMOKE_TASK`,
  standalone `scripts/prove_eval_harness.py` against a live model.

**A = B = C. Verdict: SOLVED.**

---

## F — Checkpoint one real turn (`a698311`)

- **C:** *"No new code here."* Just prove the six prior things work
  together against a real model.
- **B:** EVAL-02 explicitly extends `run_task()` with a `dispatch_factory`
  parameter — "whatever it's still missing... to make this possible" — plus
  a new reusable `Task`.
- **A:** confirms real code was added: `dispatch_factory` param on
  `run_task()`, `scripts/eval/tasks/plugin_dispatch.py`, matching unit
  tests.

**A = B ≠ C — drift at plan time**, and a mild one. The problem statement's
"no new code" framing was wrong; a small, well-scoped extension was in fact
necessary to make the checkpoint mean anything (you can't structurally grade
tool dispatch without a way to inject a real dispatch handler), and the work
item said so plainly. This is not a defect — the process worked and the
extension is owned and documented — only `key.md`'s one-line premise was
inaccurate. No code change, no work item; just don't repeat "no new code" as
a checkpoint's premise without checking.

---

## G — CONTEXT (`73ac4f0`, `fa5a63a`)

- **C:** decide what to keep/summarize/drop as a conversation grows, without
  disturbing the byte-stable parts of the prompt.
- **B:** C10 wires all four lifecycle checkpoints with two made real (usage
  accounting, cache-boundary marking); C11 completes the other two
  (real compaction via a second model call, real result-spilling to disk).
- **A:** confirms all four checkpoints are real and wired into
  `conversation.py`'s `run_turn`, matching B closely.

**A = B = C for the live-conversation path. Verdict: PARTIAL.** The gap: a
conversation reloaded from the SQLite store loses its CONTEXT lifecycle
state — `conversation_store.load()` unconditionally resets
`context_state=ContextState()` and widens `stable_prompt_len` to the *entire*
stored `system_prompt` rather than the narrower `stable_prompt` a fresh
conversation would have (`src/sadana/conversation_store.py:251-259`, per
block G's own pass-one auditor). A resumed conversation's usage accounting
restarts at zero and its cache boundary is coarser than a freshly created
one — the byte-stable-prompt / usage-tracking guarantee this block exists to
provide does not survive the persistence round trip block H added. This is a
real gap at the G/H seam, not implemented by either block's own scope as
currently written.

---

## H — SESSION-STORE (`f2962bb`)

- **C:** a conversation should survive the process — write it down, read it
  back.
- **B:** durable create/save/load by exact key, all-or-nothing, collision
  protection on create, idempotent resave, portable wall-clock budget, real
  implementation of the turn loop's existing no-op persist seam.
- **A:** matches — SQLite store, `create()`/`save()`/`load()`,
  `bind_persist()`, offset-tracked O(messages) persistence.

**A = B = C. Verdict: SOLVED.** (The G/H interaction gap above is filed
under G, since it is G's invariant that breaks.)

---

## I — Plugin static contract becomes visible (`b1cdd7c`, `46b1067`, `6e3c3b3`)

- **C:** no structured result, nothing reads a plugin folder or validates
  it, the agent doesn't know what plugins exist, nothing follows a plugin's
  steps in order.
- **B:** three ordered layers — `DagResult` (D1), manifest validation (D2),
  real graph-walking dispatch with child-spawning `ask` and branching
  `route` (D3).
- **A:** confirms all three, plus `discover_plugins()`, `plugin_dispatch.py`
  as the sanctioned conversation-importing seam. `call`/`each`/`wait`
  correctly still refused (out of this block's scope, per C).

**A = B = C. Verdict: SOLVED.**

---

## J — Checkpoint smallest complete plugin (`6724977`)

- **C:** no new code, prove the simplest plugin (one file, one step) works
  end to end.
- **B:** make the harness capture and print the plugin's already-existing
  `DagResult` structurally, extending the proof script only.
- **A:** confirms **no `src/` changes** — only the proof script gained a
  capturing wrapper and structural assertions.

**A = B = C. Verdict: SOLVED.** (Contrast with F: here "no new code" was
actually true.)

---

## K — EXECUTION re-scoped (`14ccd04`)

- **C:** decide what a plugin step may touch and where it runs, now that we
  know what a step is.
- **B:** one function, one outward HTTP request as data, closed
  Success/Failure outcome, never raises, no sandboxing, deliberately
  unwired to any node or approval gate.
- **A:** `execution.run_http()` — scheme allow-list checked before
  `urlopen`, `HTTPError`/`OSError`/`ValueError` all converted to `Failure`,
  confirmed unwired.

**A = B = C. Verdict: SOLVED.**

---

## L — SAFETY one gate (`8d31042`)

- **C:** a person needs a chance to say yes before a plugin does anything
  real; do it now, before permission-retrofitting means touching
  everything.
- **B:** ask before every `call` step uniformly, block until answered, no
  memory/no policy, execution itself deferred.
- **A:** `ApproveFn`, `run_graph`'s `call` branch calls `approve()` and ends
  the walk either way (declined vs. not-yet-runnable), `each`/`wait`
  untouched.

**A = B = C. Verdict: SOLVED.**

---

## M — PLUGINS execution half (`66fcfce`, `33e4d1e`)

- **C:** plugins start doing real things (calling APIs, producing files and
  links); *also* — per key.md's phrasing — "we throw away our hand-written
  test script and replace it with real plugins."
- **B:** G1 (run an approved `call` body, forward its result) and G2 (an
  `Artifact` a `call` body returns is recorded on `DagResult.artifacts`) —
  narrower than C's second clause.
- **A:** confirms G1/G2 exactly; confirms nothing downstream (dispatch,
  conversation, eval_harness) reads `.artifacts` yet; no fixture-script
  replacement happens in this block's own diff.

**A = B, narrower than C.** The "replace the hand-written script with real
plugins" half of C's problem statement is not this block's work — it is
block N's (`G3-real-plugin-under-eval`), which pass two independently
confirms is exactly that job. Read as M-alone-vs-C this looks like PARTIAL;
read as (M then N) it is complete. **Verdict: SOLVED**, once N is counted —
`key.md`'s one-line problem statement for M simply bundles two adjacent
blocks' scope. Not actionable; note only.

---

## N — Checkpoint real plugin under approval (`f8900f2`)

- **C:** end of phase 1 — a real multi-step plugin runs start to finish,
  asks permission where it should, produces something. "If that works, the
  foundation is finished."
- **B:** convert the two hand-written fixture plugins into real on-disk
  plugins exercising a genuine approval-gated outward step; replace
  prose-grepping grading with structural `DagResult` grading; close
  `plugin_blueprint.md`.
- **A:** confirms real `plugin-a`/`plugin-b` fixtures, a real `call → ask →
  route → stop` graph, `fetch_webhook` hitting `https://example.com` via
  `execution.run_http`, structural grading via `DagResult.trace`,
  `plugin_blueprint.md` flipped to "implemented." **But**: pass one
  explicitly found that "a live `OPENROUTER_API_KEY` run of the two proof
  scripts was still outstanding as of this commit" — both the commit
  message and `plugin_blueprint.md`'s own closing note say so.

**A = B on code shape, but the checkpoint's actual defining act — a real,
live, model-driven run proving the foundation holds — was not shown to have
happened at commit time, even though the block was marked closed and the
blueprint was flipped to "implemented." Verdict: PARTIAL.** The missing
part is exactly what this block exists to produce: live evidence. (This is
consistent with existing project memory: the CONVERSATION block is also
known to be not fully closed for the same class of reason — a missing
`review.md`/evidence gap on `C7-turn-loop`.) Whether this live run has
since been performed in a later session is not visible to this audit from
the commits alone — worth confirming before treating N as closed.

---

## O — CLI-SHELL (`769a1ae` … `4a3a830`, 8 commits)

- **C:** "There is no way to just use this... we need a command you can
  type," plus conversation listing/search, deferred until now.
- **B:** pass two flags this block as **three distinguishable purposes**,
  not one: (1) the sequential CLI-SHELL-01→06 command-surface track: a
  sequential CLI-SHELL-01→06 command-surface track; (2) `GATEWAY-DAEMON-01`,
  a large infrastructure deliverable CLI-SHELL-05/06 were *blocked on*, not
  organically part of the CLI track; (3) `MODEL-ACCESS-01`, a
  provider-outcome consistency fix that conceptually belongs to
  MODEL-ACCESS, surfaced by but not really about CLI-SHELL.
- **A:** independently confirms the same split — a real `sadana` command
  with four subcommands, a real systemd-backed daemon + webhook channel
  (`gateway_daemon.py`, `gateway_unit.py`, `gateway_service.py`,
  `channel_webhook.py`), and a `model_access.resolve()` fix that centralizes
  unknown/unwired-provider handling for every caller, replacing a
  short-lived duplicate precheck this same block had added and then
  removed (`4f72266` → `7c29e5a`).

**A = B ≠ C (C too narrow), and B itself says the block has no single
purpose. Verdict:** not a code defect — **this is a compilation-granularity
finding about `blocks.md`, not about the code.** All three pieces are
individually sound, individually owned by their own `intent.md`/`spec.md`
(confirmed by pass two), and individually match their own scope. The
mismatch is that `key.md`'s one-line problem statement for "O" only
describes purpose (1) and silently absorbed (2) and (3) under the same SHA
list. Per audit-skill's own DRIFTED-at-plan-time resolution: **no code
change, no work item** — just don't group unrelated purposes under one
audit-block letter next time; split by work-item boundary, which the SDLC
had already done correctly.

---

## Summary table

| Block | Verdict | Note |
|---|---|---|
| A — CONFIG contract | SOLVED | |
| B — cycle contract | SOLVED | |
| C — MODEL-ACCESS v0 | SOLVED | |
| D — CONVERSATION | SOLVED | self-corrected boundary slip (skill-loading in conversation.py), fixed by I |
| E — Eval harness | SOLVED | |
| F — checkpoint: one real turn | SOLVED (plan-level drift) | key.md's "no new code" was wrong; extension was necessary and owned |
| G — CONTEXT | **PARTIAL** | context lifecycle state resets on reload from store (G/H seam) |
| H — SESSION-STORE | SOLVED | |
| I — plugin static contract | SOLVED | |
| J — checkpoint: smallest plugin | SOLVED | |
| K — EXECUTION re-scoped | SOLVED | |
| L — SAFETY one gate | SOLVED | |
| M — PLUGINS execution half | SOLVED | key.md's problem statement for M includes N's job; not actionable |
| N — checkpoint: real plugin under approval | **PARTIAL** | live proof run not shown to have happened at commit time despite blueprint closing |
| O — CLI-SHELL | SOLVED (compilation finding) | key.md's O bundles 3 distinct purposes (CLI track, gateway daemon, MODEL-ACCESS fix); audit-methodology note, not a code issue |

Two blocks carry real PARTIAL findings: **G** (context state lost on
reload) and **N** (live-run evidence for the phase-1 foundation checkpoint
not confirmed). Everything else is SOLVED, with three non-actionable notes
about how `key.md`/`blocks.md` described block boundaries (F, M, O).

No `intent.md` was created for any finding — that call belongs to the user.
