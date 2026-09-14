# Console fit plan — the harness side

Status: draft, written 2026-09-14. Authored by `H15-chain-approval-ownership-plan`.
Cited by: the console's own plan, which refers to the numbered steps in §3 by
their bare numbers, and by `docs/reference/cli_shell_blueprint.md` §7.

This is the harness half of a two-repository plan. The other half is frozen.
Where the two disagree, this document is wrong until it is amended here —
never patched silently on one side.

---

## 1. Why this plan exists

A hosted, multi-tenant console is being built above this harness. Its shape,
stated once so nothing below has to restate it:

- **One control plane over many boxes.** The console is a single hosted
  service. Each customer keeps their own box, running this harness.
- **The box dials out.** There is no inbound port on a customer's box and
  never will be. The box opens a connection to a relay and keeps it; the
  console reaches the box back down that connection.
- **An organisation's members share one box, through a browser.** Several
  people, each with their own identity, acting on the same box at the same
  time.

Every one of those three contradicts something this repository has written
down and decided. The console's plan is frozen and cites "harness steps 14,
18, 24, 25, 26, 27, 28, 30" — numbers that, until this document, existed in
nobody's repository. That is the immediate reason to write it: two plans that
cite each other, where only one of them exists, are already diverging.

The larger reason is that the contradictions are real and were reasoned
decisions, not oversights. They are reversed in §2, out loud, with the
scenario that reverses each. A decision quietly dropped is a decision that
comes back.

## 2. The decision reversed

Four rows of `docs/reference/cli_shell_blueprint.md` are overturned. Each is
quoted verbatim, followed by the scenario that now names it. §2.4 quotes its
question as it was *asked* — the same commit that wrote this document marked
that question Resolved in place, so the blueprint no longer reads that way.

### 2.1 "Each instance still has exactly one user" — §1.1, second bullet

> - **The scale is one EC2 instance per user, provisioned and torn down
>   automatically, at a fleet of thousands.** Not a shared multi-tenant
>   server — each instance still has exactly one user, so §5's "no
>   multi-tenant billing/accounts *within one instance*" still holds. What
>   changes is that "single process, no daemon" was never really a
>   constraint of *this block* — it was an artifact of nobody having a reason
>   to reach the box remotely yet.

**The scenario that names it.** An organisation signs up. Four of its members
open the console in a browser and work against the same box — one reviewing a
paused approval, one editing a plugin, two watching different runs stream.
Each is a distinct principal with a distinct identity, and "the user" is no
longer a phrase with a referent. The reversal is bounded and specific: a box
serves many *principals* of one *organisation*. It does not serve two
organisations, and it still carries no billing and no account creation — the
console owns both. §5(d) and §5(h) are what this reversal costs.

### 2.2 Cloud-relay enrollment — §4.2, the `gateway_enroll.py` row

> | Gateway/daemon, the cloud-relay-enrollment half (`gateway_enroll.py`) | A self-hosted gateway pairs with a hosted control-plane connector so the connector can route/wake it — a fleet-provisioning-shaped concern. | **No**, and for a sharper reason than before: fleet provisioning is explicitly out of scope for sadana-harness (§1.1) — a future control plane's own enrollment step, if it ever needs one, is that system's design to make, not something to build speculatively here. |

**The scenario that names it.** The control plane now exists, and it is not
speculative. A box with no inbound port cannot be reached unless it reaches
out first, so enrollment stops being "that system's design to make" and
becomes a handshake both systems have to implement to the same wire document.
The declining reason was exactly right when it was written — and it was
conditional on a control plane not existing. It does now. Step H30.

### 2.3 The pty bridge and the browser dashboard — §4.2, the `pty_session.py` row

> | pty/terminal bridging + web dashboard (`pty_session.py`, `pty_bridge.py`, `web_routers/*`, `web_git.py`, `web_models.py`) | Confirmed (only non-test callers: `web_server.py`, `web_routers/sessions.py`) to be one bundle: a loopback-bound browser dashboard, with the pty bridge letting a browser tab attach to a real pty of hermes's *own* TUI over a WebSocket. **Not** CLI-REPL infrastructure. | **Not named as the intended access model** (§1.1: messaging platform for end users, VNC for ops) — a real remote-desktop session over VNC already gives the maintainer's team an actual terminal, which is exactly what §4.1's dispatch shell already serves. Revisit only if a browser-based interface is later chosen over VNC — see §7. |

**The scenario that names it.** A browser-based interface has been chosen, so
the row's own revisit condition has fired. What is *not* adopted is hermes's
answer to it: the console does not attach a browser tab to a pty of the box's
own terminal UI. It speaks a typed API to a door (§5(e)) and streams events
(P6). A pty bridge would put a terminal, and everything reachable from a
terminal, behind a browser session — the opposite of a door with a closed
grammar and a token that says what its holder may do.

### 2.4 §7 question 6

> 6. **Is VNC assumed sufficient for ops/troubleshooting access
>    indefinitely, or does a browser dashboard eventually get built
>    alongside it** (§4.2's pty-bridge/web-dashboard row)? Not named as
>    needed today; worth deciding deliberately once it comes up rather than
>    building it speculatively now.

**The scenario that names it.** It came up. The question asked to be decided
deliberately when it did, and this document is that decision: a browser
interface is built, it is the console, it does not replace VNC for ops, and it
reaches the box through the door of §5(e) rather than through a terminal.
Question 6 is marked Resolved in place, pointing here.

### What does not change

Nothing about the paradigm moves: plugins remain SOPs with deterministic
graphs, memory remains decisions taken against known guidelines, behaviour
still changes because a human changed a plugin; a turn is still reached only
through the one door `CLAUDE.md` names; and the standard library is still the
first answer, with exactly the two runtime dependencies §5(c) permits joining
`jsonschema`.

## 3. The numbered steps

Steps 1–13 are closed. They are the blocks recorded in
`docs/audits/2026-09-10/blocks.md`, which carries the problem statement each
one was meant to solve, and the commit shas that solved it.

| # | Block | Where |
| --- | --- | --- |
| 1 | CONFIG as a contract | `blocks.md`, `ad5d185` |
| 2 | The conversation cycle, on paper | `4c2e954` |
| 3 | MODEL-ACCESS v0 | `f81d52b` |
| 4 | CONVERSATION | ten commits, `116e59c`…`c3603a3` |
| 5 | The eval harness | `1634433` |
| 6 | CONTEXT | `73ac4f0`, `fa5a63a` |
| 7 | SESSION-STORE | `f2962bb` |
| 8 | The plugin's static contract, and it becomes visible | `b1cdd7c`, `46b1067`, `6e3c3b3` |
| 9 | EXECUTION, re-scoped | `14ccd04` |
| 10 | SAFETY, one gate | `8d31042` |
| 11 | PLUGINS, the execution half | `66fcfce`, `33e4d1e` |
| 12 | CLI-SHELL | eight commits, `769a1ae`…`4a3a830` |
| 13 | GATEWAY-DAEMON, and everything merged since that audit | scheduling, memory, persona, the plugin editor, the marketplace, install, observability, and the six capability imports |

Two notes on that table. `blocks.md` also records three checkpoints — one real
turn, the smallest complete plugin, a real plugin under approval — which are
not numbered here because they added no block; they proved one. And step 13 is
the only row larger than its audit entry, because `blocks.md` was written on
2026-09-10 and everything after CLI-SHELL has landed since.

Steps 14–30 are this chain. One line each: the title, the promises of §4 it
discharges, and the console step it resolves.

| # | Title | Promises | Console |
| --- | --- | --- | --- |
| H14 | Tuned: the config file, settings and secrets by reference through the door | P9, P11 (secrets half) | 14 |
| H15 | Approval and ownership in the work-item chain, and this document | — | makes the console's citations of 14, 18, 24–28 and 30 resolve |
| H16 | The store: identity fields, the ledger, inventory, one writer, one lock per conversation | P1, P3, P8 | — |
| H18 | Parked: call approvals persisted, the approvals resource, and expiry | P7 | 18 |
| H19 | The door: grammar, Problem Details, idempotency, operations, the token, capabilities, the loopback listener, the conformance test, and the wire documents | P2, P4, P10 | — |
| H20 | Conversations, messages, runs, traces, spans and artifacts through the door; the turn client | — (serves P1–P4 over the turn-side nouns) | — |
| H21 | Watched: streaming deltas, live runs and spans, and stop | P6 | — |
| H24 | Plugins through the door: layout, install-from-git, inspect, save, workflows, nodes, tools | — | 24, 25, 28 |
| H26 | Agents and memory through the door | — | 26 |
| H27 | Schedules: cron, timezone, pause and resume through the door | — | 27 |
| H30 | The door behind the socket: enroll, tether, frames, events, reconnect, upgrade, deregister, purge, install, and the test relay | P5, P10, P11 | 30 |
| HCP | The real box on the staging EC2 behind the test relay (checkpoint) | P1–P11, graded | — |

The numbering is not contiguous, and that is deliberate rather than a gap in
the list. Harness ids track the console's own step numbers so a citation can
be read across without a translation table. Where one harness item closes
several console steps — H24 closes 24, 25 and 28 — the ids in between do not
exist here, and where a console step needs nothing new from the box, no
harness id was minted for it at all.

## 4. The rubric

Eleven promises. Every work item in §3 carries its promise's question in its
own `spec.md`, answered for that item's nouns — not cited, answered.

| id | name | the promise |
| --- | --- | --- |
| P1 | Named | Every resource carries an immutable id, a mutable name, wall-clock `created_at` and `updated_at`, a version, and a state from a written closed set. |
| P2 | Spoken | Every resource answers the same six verbs, four list params, error shape, paging, ETag and idempotency — read one noun's docs and you can call them all. |
| P3 | Logged | Every change lands in an ordered ledger with a cursor, deletes leave tombstones, and the whole inventory is cheap to enumerate. |
| P4 | Locked | One door; only short-lived tokens the console signed; the principal comes from the token, never the payload. |
| P5 | Tethered | We dial out, enroll once with a key we made, say what we are, heartbeat, and reconnect with backoff and jitter — no inbound port, ever. |
| P6 | Watched | Long work streams its progress as it happens and can be stopped mid-flight. |
| P7 | Parked | Anything waiting on a person is a durable, addressable, resumable resource, never a blocking prompt. |
| P8 | Shared | Concurrency at the grain of the thing being changed; per-principal state keyed by the principal the door derived. |
| P9 | Tuned | Configuration is a resource read through the door and applied without a restart; secrets are write-only. |
| P10 | Known | We report version, capabilities and health, we upgrade through the tether, and a conformance test against the published contract runs in our own CI. |
| P11 | Gone | We can be deregistered, and everything we hold for a principal can be enumerated for a purge. |

## 5. Decisions taken here and not reopened

Eight. Each is settled for the whole chain; a work item that wants to revisit
one amends this section first, in its own commit, and says why.

**(a) Keys, ids and names are three different things.** `key` stays the
immutable natural key, with a database constraint behind it, exactly as
`CLAUDE.md` already requires. `id` is minted, unique, and is what a ledger
entry and a foreign key point at. `name` is mutable and is what a person
reads. A rename touches the name and nothing else — never a key, never a
foreign key. This is the existing "names, not pointers" rule with the two
halves finally distinguished: a *stable* name is an address, a *displayed*
name is a label, and only the first can be depended on.

**(b) Legacy rows are filled in once, at first open, idempotently.** A row
written before identity existed receives its `id` and its timestamps the first
time it is opened, with `created_at = updated_at` at that moment. That instant
is a **floor**, not a fact — it is documented as one everywhere it surfaces,
because the row is older than the timestamp it now carries. No backfill
migration: the existing `PRAGMA table_info` + guarded `ALTER TABLE` pattern
adds the columns, and a legacy row keeps loading until it is genuinely
rewritten.

**(c) Two runtime dependencies join `jsonschema`, once each.** `PyJWT[crypto]`
in H19, and every single decode pins `algorithms=["ES256"]` — the
algorithm-confusion family of bug is precisely why nothing here is hand-rolled
and why the pin is not optional. `websockets` in H30. Nothing else; the
standard library remains the first answer everywhere else.

**(d) Accounts.** The acting account for a console principal is an
`AccountKey` of `"console:"` followed by the token's `sub` claim. The owner
(`SADANA_MEMORY_ACCOUNT`, default `local`) remains the terminal's account and
is not widened to cover console principals. A scheduled run runs as the
account that created it. This keeps `CLAUDE.md`'s existing rule intact: an
`AccountKey` is never derived by widening a `ConversationKey`, and the account
is derived from the envelope by the bridge, never parsed out of a payload.

**(e) One door, two transports.** The door is a pure `handle()`. Two transports
call it: the tether (H30) and a loopback listener (H19). The conformance test
calls `handle()` directly, with no transport at all — which is what makes the
test a statement about the contract rather than about a socket.

**(f) Locking.** One writer connection per process, behind a lock held only
inside a transaction. Readers are thread-local. One lock per conversation,
held for the duration of a turn. This is the point `CLAUDE.md` names as the
moment to revisit `conversation_store.py`'s single connection — a second
long-lived in-process writer is exactly what the console introduces.

**(g) Clocks.** Anything the console can see is timestamped with
`time.time()`, because a wall-clock instant is the only kind two machines can
compare. Budgets keep `time.monotonic()`, because a budget measures elapsed
time and must not care that the clock was stepped.

**(h) A box belongs to one organisation.** The box records the organisation
that enrolled it and refuses a token from any other, in addition to refusing a
token minted for a different harness id. Two checks, not one: the harness-id
check catches a token replayed at the wrong box, the organisation check
catches a correctly-addressed token from a tenant that has no business here.

## 6. What the box will report that the console's prompts did not anticipate

Three places where the console's plan assumes a shape the harness does not
have. Each is the box's answer, and each is written down here rather than
discovered during H20.

**Node kinds are seven, not the smaller set the prompts assume.** They are
`compute`, `ask`, `route`, `stop`, `call`, `each` and `wait`. Any console
surface that draws, validates or filters a workflow graph handles all seven,
and `wait` in particular is not an error state — it is the pause that P7 makes
addressable.

**A credential's value never travels with the thing that uses it.** The value
lives on the box, written through `PUT /v1/secrets/{name}`, which is
write-only (P9). Anywhere a plugin, a workflow or a config refers to a
credential, it carries a `credential_ref` that must name an existing secret;
a literal value in that position is a validation failure, not a convenience.

**The harness `exit_reason` vocabulary is eight values, and the console's is
five.** The harness reports `completed`, `budget_exhausted`,
`wall_clock_exhausted`, `persistence_failed`, `provider_failed`,
`context_overflow_unhandled`, `interrupted` and `invalid_tool_calls`. The
mapping onto the console's five is written once, in H20, on the box side — the
box translates before it answers, so the console never learns eight names and
the harness never loses the distinction between running out of turns and
running out of clock.

**A promoted `messages.create` operation carries `resource: null`.** Found
during H20, not anticipated by any prompt: the door's own `run_bounded`
computes a create's `resource` once, from the URL's own id segment, before
the slow call ever runs — and a create path carries no id segment to compute
one from. Read the assistant's reply from the conversation's own messages
once the operation settles (`succeeded` or `failed`), the same call the
message list already serves. H30, which reopens the door's framework, is
where a create-time resource hint gets added and this note retires.
