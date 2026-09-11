# Spec: One door in

Intent: docs/tasks/CLIENT-SURFACE-01-one-door-in/intent.md

## Requirements

1. One module owns everything between "a client has words from a person" and
   "the client has an answer": finding or creating the conversation, building
   the turn's dispatch, running the turn, persisting it, and handling a
   procedure that stopped to wait. *(intent: one way in)*
2. `sadana chat` and the webhook channel both reach a turn only through that
   module. No second copy of the sequence survives anywhere in `src/`.
   *(intent: both existing ways go through it)*
3. The caller states the account and the conversation key on every call. The
   module resolves neither from config, from the environment, nor from a
   fallback of its own. *(intent: whoever is asking always states who the
   person is)*
4. A procedure that stops to wait is persisted and resumable from `sadana
   chat`, exactly as it already is from the webhook channel. *(intent: the
   waiting-procedure defect is fixed for the terminal)*
5. What the module returns describes what happened. It prints nothing, writes
   to no stream, and accepts no printer, renderer or output callback.
   Rendering, exit codes and HTTP status are the client's own decisions.
   *(intent: turning that into an exit code, an error page or a reply is the
   client's business)*
6. Both existing clients keep their present observable behaviour byte for
   byte — same stdout, same stderr, same exit codes, same webhook replies —
   with requirement 4 as the single exception. *(intent: from the outside,
   the two ways in behave as they do now)*
7. An expected failure inside a turn comes back as an ordinary return value.
   The module raises only for a caller error: a conversation asked for by
   name that does not exist when the caller said it must. *(intent: a failure
   inside a turn still reaches whoever asked as an ordinary answer)*
8. Adding a further client requires no edit to this module, to either
   existing client, or to any other file: a new client is a new file that
   resolves its own identity, calls the door, and renders the result.
   *(intent: adding a further way to reach the agent is a small,
   well-understood piece of work)*
9. No table of ways-in, no swappable part with one implementation, no
   protocol, no message format, no server, no authentication, no rate
   limiting, no cross-deployment isolation. *(intent: constraints)*
10. One finished answer per turn, and the returned shape must be able to grow
    incremental delivery later as an added parameter rather than a retyping
    of every client. *(intent: an answer arriving in pieces is a known future
    need)*
11. The module introduces no new persisted state: no new table, no new
    column, no new file on disk. *(intent: nothing about how the agent takes
    a turn is written a second time — and nothing new is stored to make that
    true)*
12. `sadana chat` keeps working with nothing running in the background.
    *(intent: the terminal keeps working with nothing running)*
13. The module's own docstring is the contract: what each input means, which
    values it refuses, what the outcome's fields mean, what it raises, and
    what it guarantees under concurrent callers. *(intent: anyone who will
    build a client writes against this, so it has to be documented and hard
    to get wrong)*

## Design

One new module, `src/sadana/client_surface.py`, holding two calls: one that
opens the door for a process and one that takes a turn through it. It is not a
new layer — it is the sequence `gateway_dispatch.handle_inbound()` already
runs, with the channel vocabulary taken out of its mouth so the terminal can
use it, and the terminal's copy deleted. The subsections below give the
existing shape, the new module's boundaries, what each client becomes, the
concurrency contract, the policies applied, and the four design guidelines.

### What is already true

The door is not new. It exists in embryo as `gateway_dispatch.handle_inbound()`
(`src/sadana/gateway_dispatch.py:60`) and already has two callers — the
webhook channel via `subcommands/gateway.py:88`, and the scheduler via
`scheduling.py:81`, which chose it deliberately ("No second bridge, no new
turn-loop entry point", `scheduling.py:13`).

The terminal could not use it for one reason: its input is a `MessageEvent`
(`gateway.py:24` — `platform`, `chat_id`, `thread_id`, `text`), a
channel-shaped envelope a terminal has no honest way to fill, and it derives
the account from `platform` and `chat_id` inside itself
(`gateway_dispatch.py:129`). So `subcommands/chat.py` copied the sequence
instead, and its own docstring records the copy. The two then diverged: the
copy never passes `persist_pause`, so `build_dispatch` falls back to
`_noop_persist_pause` (`plugin_dispatch.py:124`) and a `wait` node reached in
the terminal is discarded with no row written and no message shown.

So this is not "add a layer". It is: take the step that already exists, remove
the channel vocabulary from its mouth, and delete the copy.

A third copy exists too, of the *assembly* rather than the turn: provider,
model, persona, plugin set, connection, memory schema and recorder are built
in `cmd_chat` (`subcommands/chat.py:106-112`) and again in `cmd_gateway_run`
(`subcommands/gateway.py:75-82`), and then threaded as seven keyword
arguments into the scheduler's tick thread (`subcommands/gateway.py:110-120`).
That assembly is where the divergence actually lives, so the door owns it.

### The module

`src/sadana/client_surface.py`. One file, named for the block. It touches
real I/O — it opens the SQLite store — so per CLAUDE.md it is its own file and
holds no pure helpers belonging to anything else.

It may import `conversation`, `conversation_store`, `plugin_dispatch`,
`plugins`, `plugin_manifest`, `memory`, `memory_store`, `observability`,
`persona`, `config`, `model_access`. It may **not** import `gateway`,
`channel_webhook`, `editor_server`, `scheduling`, or anything under
`subcommands/`. That direction is the whole point: channel vocabulary points
at the door, never the reverse.

### Two calls, not one

**`open_surface(...) -> Surface`** — once per process. Resolves provider and
model from config, loads or seeds the persona, seeds the memory plugin,
builds the plugin set, opens the store, ensures the memory schema, builds the
recorder, and calls `config.load_dotenv()` so a client that is not the CLI
cannot forget it (idempotent, never overrides a live variable —
`config.py:31`). Returns a frozen dataclass holding exactly those, plus one
`threading.Lock`.

**`async take_turn(surface, *, account, conversation, text, create_if_missing) -> TurnOutcome`**
— once per turn. This is `handle_inbound`'s body with the `MessageEvent`
removed: check for a pause and resume it if there is one, otherwise
load-or-create the conversation, build the dispatch with `persist_pause`
wired, run the turn, save, return.

The split is not decoration. Everything in `Surface` is what the three
assembly sites already build once per process; everything rebuilt inside
`take_turn` is what CLAUDE.md requires be rebuilt per turn —
`conversation_store.bind_persist()` explicitly, and the dispatch closure for
the reason `docs/reference/dispatch_closure_state_bug.md` records.

### What each client becomes

- `subcommands/chat.py` — resolves `--account` / `SADANA_MEMORY_ACCOUNT` /
  `local` itself and states it; `--resume KEY` passes
  `create_if_missing=False`, `--key NAME` passes `True`. Its loop is read a
  line, call the door, render. The pause defect disappears because the door
  is the same door the channel uses.
- `gateway_dispatch.py` — survives as the channel adapter and nothing else:
  `MessageEvent` in, `session_key_for(event)` and
  `memory.account_key_for(event.platform, event.chat_id)` out, door called,
  `(ok, text)` returned. It keeps two callers, so it stays a function rather
  than being inlined twice. `conn_lock` moves onto `Surface`.
- `subcommands/gateway.py` — one `open_surface()` instead of eight lines of
  assembly; the tick thread receives the surface instead of seven keyword
  arguments.
- `scheduling.py` — takes the surface, drops six parameters, and takes its
  connection lock from `surface.conn_lock`.

### Concurrency, stated exactly

`take_turn` holds `surface.conn_lock` for the whole turn, which is what
`handle_inbound` does today (`gateway_dispatch.py:118`) and for the same
reason: `conversation_store.open_store()` is safe for one thread at a time,
and the webhook server hands every request its own thread.

What it survives: two OS threads calling `take_turn` on the same surface
(they serialize); a scheduler tick landing mid-turn (it serializes against
the same lock). What it does not survive: two turns awaited concurrently on
one event loop — the lock is a `threading.Lock`, so the second would block
the loop rather than yield. Every caller today runs its own `asyncio.run`,
and the terminal's loop has exactly one caller. The ceiling is named in a
`ponytail:` comment with the upgrade path CLAUDE.md already specifies for
this exact condition: per-path refcounted connections once a second
long-lived in-process writer genuinely exists.

### Policies applied

- **`testing-conventions`** — applied; see Acceptance criteria. The new tests
  are unit tests under `tests/unit/test_client_surface.py`, they assert the
  relationship between a pause row and the next call rather than snapshotting
  text, and they read no source.
- **`project-structure` and `reference-lookup` do not exist** in
  `.claude/skills/`. Their subject matter is carried by CLAUDE.md, which I
  applied as the policy of record: the I/O-module rule, names-not-pointers,
  the registry rule, `bind_persist` rebuilt per turn, classified outcomes as
  a closed set rather than exceptions, one test runner, and the
  `conversation_store` single-connection revisit triggers.
- No brand, UX or security skill is present. Nothing in this item accepts an
  untrusted input or builds a filesystem path from a caller-supplied name,
  so CLAUDE.md's two input-sanity rules have nothing to bind here — the
  account and conversation key are opaque strings the store already treats as
  caller-supplied (`memory.py:20`).

### Guideline 2 — where this sits relative to the plugin seam

Past it, and deliberately not extending it. The plugin seam exists and has
real consumers; a *client* is not a plugin and gets no seam of its own. Future
value arrives as addition: a new client is a new file. Three bets are being
taken — the door's argument list, the outcome's three fields, and
`Surface` as a process-lifetime value. Each has three call sites today.
Declined bets: a transport, a client registry, an auth model, a streaming
shape, and any new persisted state.

### Guideline 3 — which of the three moves

Neither "add a step" nor "make a step heavier" on the hot path. The turn's
step count is unchanged: the same sequence runs, in one place instead of two,
with one parameter added (`create_if_missing`) to carry the one thing the two
callers genuinely disagreed about. The cheaper move I looked for and rejected
is in Rejected alternatives. Net effect on the tree is subtraction:
`gateway_dispatch.py` loses most of its body, `chat.py` and
`subcommands/gateway.py` lose their assembly, `scheduling.py` loses six
parameters.

### Guideline 4 — state inventory

| State | Lifetime | Why not derived |
|---|---|---|
| `sqlite3.Connection` | process | It is the I/O handle; there is nothing to derive it from. Already held for the process by both clients today. |
| plugin set, persona, provider, model | process | Snapshots. Same lifetime both clients already give them; rebuilding per turn would rescan and revalidate every installed plugin on every message for no change in result. |
| recorder | process | Wraps the connection; `observability.make_recorder(conn)` is already built once per process. |
| `conn_lock` | process | A lock cannot be derived. Moved, not added. |
| dispatch, tracker, `persist`, `persist_pause`, `now` | one turn | Rebuilt every call, by requirement. |
| conversation, pause row | not held | Loaded by name inside the turn and let go. The door holds the *name*, never the conversation — CLAUDE.md's names-not-pointers rule, which is exactly what makes two clients on one store safe. |

New persisted state: none. No table, no column, no file.

## Interface

```python
@dataclass(frozen=True)
class Surface:
    conn: sqlite3.Connection
    plugin_set: plugin_dispatch.PluginSet
    persona: str
    provider: str
    model: str
    recorder: observability.Recorder
    conn_lock: threading.Lock

def open_surface(*, provider: str | None = None, model: str | None = None) -> Surface: ...

@dataclass(frozen=True)
class TurnOutcome:
    ok: bool
    answer: str | None
    diagnostic: str

async def take_turn(
    surface: Surface,
    *,
    account: memory.AccountKey,
    conversation: conversation.ConversationKey,
    text: str,
    create_if_missing: bool,
) -> TurnOutcome: ...
```

**Inputs.** `account` and `conversation` are required and have no default;
`provider`/`model` on `open_surface` are per-run overrides for the CLI's
existing flags, falling back to config. `create_if_missing` has no default
either — the two callers disagree about it, so both must say.

**Outputs.** `ok` is `exit_reason == COMPLETED` for a turn, or
`failed_node is None` for a resumed procedure. `answer` is the turn's
`final_text` when there is one, or the resumed procedure's own text, and is
`None` when the turn produced no text at all. `diagnostic` is
`f"[{exit_reason.value}] {detail or ''}"` — the string both clients already
build independently (`chat.py:98`, `gateway_dispatch.py:179`) — and `""`
when there is nothing wrong.

Three fields rather than one because the two clients need different cuts of
the same outcome and today get them from the same two values: the terminal
prints `answer` to stdout when there is one and `diagnostic` to stderr when
there is not, then exits non-zero if `not ok`; the channel replies with
`answer or diagnostic` and reports `ok`. A `BUDGET_EXHAUSTED` turn carries a
real epilogue in `answer` while `ok` is `False`, which is why `ok` cannot be
recovered from the text and the text cannot be recovered from `ok`.

Not a closed set of outcome types: the branches differ by which fields are
populated, not by carrying different data — CLAUDE.md's own carve-out, the
same one `plugins.DagResult` already takes (`plugins.py:171`).

**Errors.** `conversation_store.ConversationNotFound` when
`create_if_missing=False` and the name is unknown — the existing exception,
reused, and the only one. Every turn outcome, including a provider failure, a
budget exhaustion, a persistence failure and a failed plugin node, returns
normally. An observability write that fails is swallowed and logged inside the
recorder, as it already is.

**Growing incremental delivery later.** An added keyword parameter taking a
per-chunk callback, with `TurnOutcome` unchanged as the final value. Possible
precisely because the door returns its answer instead of being handed a
printer.

## Acceptance criteria

- [ ] `make verify` ends `VERIFY OK`, pasted into `review.md` under
      `## Evidence`.
- [ ] `src/sadana/client_surface.py` exists and imports none of `gateway`,
      `channel_webhook`, `scheduling`, `editor_server`, `subcommands.*`.
- [ ] `plugin_dispatch.take_turn_and_reconcile` and
      `plugin_dispatch.build_dispatch` have exactly one call site each in
      `src/`, both in `client_surface.py`.
- [ ] `conversation.create_conversation` has exactly one call site in `src/`,
      in `client_surface.py`.
- [ ] `take_turn` has no parameter default for `account`, `conversation` or
      `create_if_missing`.
- [ ] A unit test proves a `wait` node reached during a terminal turn writes a
      pause row, and that the next call resumes it without calling the model.
- [ ] A unit test proves `take_turn` with `create_if_missing=False` raises
      `ConversationNotFound` for an unknown name, and with `True` creates it.
- [ ] A unit test proves a non-`COMPLETED` turn that still carries text
      returns `ok=False` with that text in `answer` — the distinction the
      terminal's stdout depends on.
- [ ] A unit test proves two threads calling `take_turn` on one surface
      serialize.
- [ ] `tests/unit/test_gateway_dispatch.py` passes unchanged except for
      construction of the surface, proving the channel's replies did not move.
- [ ] `sadana chat` on a completed turn, a budget-exhausted turn and a failed
      turn produces the same stdout, stderr and exit code as before.
- [ ] `sadana chat` runs with no gateway service running.
- [ ] No new SQLite table or column: `schema` in `conversation_store.py` is
      untouched.
- [ ] `client_surface.py`'s module and function docstrings state the inputs,
      the refusals, the outcome fields, the one exception and the concurrency
      guarantee.

## Non-goals

- Any transport, protocol, message format or server.
- A browser client, a desktop client, or an editor protocol adapter.
- Authentication, sessions, rate limiting, or keeping one deployment's people
  apart from another's.
- Incremental or streamed answers.
- Routing the read-only surfaces — `sadana conversations`, `sadana runs`, the
  plugin editor — through the door. They run no turns and are untouched.
- Any change to what a turn does, what the agent is told, or how a plugin
  executes.

## Rejected alternatives

**Widen `MessageEvent` and let `handle_inbound` be the door.** The cheapest
possible move — no new module at all, add an `account` field, let the terminal
fill in `platform="cli"`. Rejected because it puts channel vocabulary in the
terminal's mouth and makes `gateway.py` — which is pure, imports nothing
below it, and is documented as the module every channel adapter depends on
(`gateway.py:5`) — grow a concept that is not about channels. A terminal
inventing a `chat_id` to satisfy an envelope is the kind of lie that the next
client copies.

**Hermes's shape: a constructor each client assembles, plus one turn method.**
Their door is `AIAgent(**kwargs).run_conversation(user_message=..., task_id=...)`
(`run_agent.py:422`, `tui_gateway/methods_prompt.py:1464`), reached from
nine files across the ACP adapter and the TUI gateway. Adopted: the idea of
one named turn entry every client calls. Declined, specifically:

- *Each client assembles the door itself.* `acp_adapter/session.py:615-687`
  calls `load_config()`, reads `model.default` and `model.provider`, runs
  `resolve_runtime_provider()`, and builds a twelve-key kwargs dict — its own
  copy of the assembly. That is the divergence we already have, so our door
  owns the assembly and takes only what genuinely differs per caller.
- *Clients patch the core's attributes after construction.*
  `acp_adapter/session.py:688-694` sets `agent.session_cwd` and
  `agent._print_fn` to adapt the core to the client's stdout rules. Our
  `Surface` is frozen and the door returns its answer, so there is nothing to
  patch — which is also what keeps requirement 10 cheap.
- *The turn returns an untyped dict.* `methods_prompt.py:1476` reads
  `result.get("final_response", str(result))` — a fallback that exists
  because the shape is not guaranteed. We return a frozen three-field value.
- *The door is a 9,413-line class.* Not a shape to copy at 9,000 lines or at
  100.

Worth recording: `hermes_cli/web_server.py` is 20,070 lines and contains no
reference to `AIAgent` at all. Their browser surface is a control and
observation dashboard, not a turn client. So the count of clients typed
against their turn entry is two, not three — and it is a fair guess that our
own browser surface will first want the read-only side, which this item
explicitly leaves alone.

**Define the door, migrate the callers later.** Rejected at the plan stage and
still right: a door with no caller at the end of its own commit is a guess,
and the split leaves the pause defect live in between.

**A third throwaway client to prove the shape.** Offered at the plan stage and
declined by the user in favour of a rule checked at review. Recorded in
`intent.md` as the weaker guarantee.

**Return `TurnResult | DagResult` and let clients branch.** Rejected: the
branch is exactly what diverges. Two clients branching on a union is how we
got here.

**Leave `scheduling.py` alone.** Tempting — it is a closed work item and its
tests monkeypatch `gateway_dispatch.conn_lock`. Rejected because leaving it
keeps the assembly's third copy alive in the seven keyword arguments threaded
through `subcommands/gateway.py:110-120`, and the intent says every path
reaches the turn through one door. The edit is mechanical: a parameter list
and a lock reference.

**Derive the plugin set and persona per turn instead of holding them.**
Considered under guideline 4 and rejected on cost, not principle:
`discover_plugins()` scans and validates every installed plugin, which
`resume_paused_run` already refuses to do for the same reason
(`plugin_dispatch.py:287`). Holding them is the lifetime both clients already
give them, so this design changes no staleness story that did not already
exist.

## Concerns

**The guarantee for requirement 8 is judgement, not machinery, and it is the
weakest part of this document.** The user chose a rule checked at review over
a third caller written to prove it. Nothing in `make verify` fails if the
door turns out to need a fourth argument the moment a real browser client
appears. The acceptance criteria are the closest available proxy — no
defaults on the identity parameters, one call site for the turn primitives —
and a reviewer should treat the argument list as the thing most likely to be
wrong.

**Identity can diverge again, one layer up, and this design permits it.**
Requirement 3 moves the default out of the core, so the terminal's fallback to
the account `local` now lives in `chat.py`. A second client is free to pick a
different fallback, and then two clients disagree about who the person is
again — the same class of bug, relocated rather than eliminated. This is the
direct consequence of the user's choice that the caller must always state it,
and it is the right trade for readability, but it should be named: the door
cannot enforce a convention it is forbidden to hold.

**Two policies pull in opposite directions over `scheduling.py`'s tests.**
`testing-conventions` and CLAUDE.md both say fix the code, not the test, and
never edit a closed work item; the design nonetheless retargets one
monkeypatch line in `tests/unit/test_scheduling.py:131` because the lock it
patches moves onto `Surface`. I followed the simplification and am recording
the cost: a closed item's test file is edited, and the edit is only
defensible because it is mechanical — same lock, same assertion, new home. If
a reviewer prefers the other side, the alternative is a module-level
`client_surface.conn_lock` that nothing owns, and the third copy of the
assembly staying alive in `scheduling.py`. I think that is worse, but it is
close.

**Holding a `threading.Lock` across `await` for a whole turn is a real
ceiling, inherited rather than introduced.** It is correct for every caller
that exists — each runs its own `asyncio.run` — and wrong for the first
caller that awaits two turns on one loop, which is exactly what a browser
server serving two people at once would naturally do. That is the most likely
next client, so the ceiling is closer than it looks. CLAUDE.md already names
the upgrade and its trigger, and I have deliberately not built it early.

**`Surface` is a value holding live handles, which brushes against
names-not-pointers.** I read the rule as governing long-lived values holding
*other values* that can change independently, not a process-scoped owner
holding its own connection and lock — and the conversation, the thing that
genuinely changes underneath, is held only by name. A reviewer who reads the
rule more strictly would want the connection passed per call instead, which
is today's shape and the one that produced three assembly sites.

**One thing I could not test the way I would like.** That the two clients'
observable behaviour is unchanged is asserted by unit tests on the door plus
a manual run of `sadana chat`; there is no end-to-end test of the terminal's
stdout and exit code, because the suite may not call the model API. The
first real round trip therefore belongs in a standalone script outside `make
test`, pasted as Deploy-stage evidence, per CLAUDE.md.
