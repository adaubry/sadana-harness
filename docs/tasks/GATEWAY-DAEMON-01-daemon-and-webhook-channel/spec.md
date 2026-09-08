# Spec: A sadana instance can only be reached while someone is typing at it

Intent: docs/tasks/GATEWAY-DAEMON-01-daemon-and-webhook-channel/intent.md

## Requirements

1. A single, long-running background process can be started and stays up
   independent of any interactive terminal, until it is deliberately
   stopped. (Intent §Proposed outcome.)
2. That process can receive a message from outside it — over a generic
   incoming webhook — and produce a real reply sent back over the same
   channel, with no person typing a command for that exchange. (Intent
   §Proposed outcome.)
3. The same webhook chat can carry more than one exchange over time, each
   one continuing the same conversation rather than starting a fresh one
   per request — the "smallest version still worth having" only holds if a
   second message from the same place lands in the same place. (Intent
   §Proposed outcome, implied by "left running.")
4. Every message in or out goes through sadana's existing conversation,
   turn, and plugin machinery; nothing here builds a second way to hold or
   act on a message. (Intent §Constraints.)
5. Only one instance of the background process runs against a given state
   directory at a time — two racing daemons answering the same webhook
   would double-reply or corrupt the conversation store. (Intent
   §Proposed outcome, "left running" implies exactly one is.)
6. A later, second channel can be added without changing the daemon's own
   lifecycle code — refined below (§Design, §Concerns) to what this item
   can actually deliver for one adapter versus what a second adapter
   earns. (Intent §Constraints and §Affected users and systems.)
7. Nothing here builds fleet or multi-instance behavior — federation, a
   hosted relay, scale-to-zero, or any control plane beyond this one
   instance. (Intent §Constraints.)
8. This item ships exactly one working channel adapter (a generic incoming
   webhook); every other adapter is later, separate work. (Intent §Shape.)

## Design

Four research passes covered hermes's `gateway/` (87 files) and `platforms/`
(part of CHANNELS, 120 files) before this spec was written. What follows
names what was adopted, adapted, or declined, and why — the corpus is
production-tested at multi-tenant, multi-gateway scale sadana does not have
and does not want yet, so most of the "why" is about trimming, not copying.

**Adopted, close to verbatim (small, hosting-agnostic, real value now):**

- **`systemd_notify.py`'s `sd_notify` pattern** — pure stdlib, a `AF_UNIX`
  `SOCK_DGRAM` write to `$NOTIFY_SOCKET`, a silent no-op when that variable
  is unset. This is exactly the "genuinely reusable regardless of hosting
  model" case the research called out, and gives real value the moment an
  EC2 instance's systemd unit is written for it, at the cost of ten lines
  and zero new dependencies. Adopted as `gateway_daemon._notify_systemd()`.
- **A crash-safe single-instance lock**, but only the cheaper of hermes's
  two tiers. hermes runs a PID file (`O_CREAT|O_EXCL`, for external
  inspection and `--replace` races) *and* an independent `flock` (crash-safe
  by the kernel, no cleanup code required). sadana has no `--replace`
  hot-swap and no external inspector reading the PID file yet — so this
  item takes only the `flock`, matching the research's own suggested
  minimal starting point. The PID-file half is easy to add later and isn't
  foreclosed by this choice.
- **`session.py::build_session_key`'s shape**: a session identity derived
  as a pure function of the inbound envelope (platform/chat/thread),
  never a live reference — this is also this project's own existing rule
  ("Use names, not pointers for anything long-lived"). Adopted as
  `gateway.session_key_for()`.

**Adopted, restructured to this project's own scale:**

- **The dispatch bridge shape** (`adapter → handle_message → session key
  → load-or-create conversation → run the turn → send the reply`) is
  hermes's central insight and the one this item most needs. hermes's own
  version spans `platforms/base.py`'s ~300-line `handle_message`/
  `_process_message_background`, `run.py`'s ~4,900-line
  `_handle_message`/`_handle_message_with_agent`, background typing
  indicators, a 200ms interrupt-poll loop, and a thread-pool-executor
  handoff for the agent turn itself. sadana's own turn loop is already
  `async` end to end (`conversation.take_turn`, `model_access.resolve`),
  so none of the executor handoff is needed; there is no typing indicator
  or interrupt concept in sadana yet, so neither is built. What's kept is
  the shape, not the size: `gateway_dispatch.handle_inbound()` is the
  entire bridge, built directly on `plugin_dispatch.build_dispatch()` /
  `take_turn_and_reconcile()` — the exact bridge `cmd_chat` already uses,
  reused rather than duplicated.

**Declined outright, and why:**

- **`hosted_room_*.py`, `relay/*.py`, `scale_to_zero.py`,
  `drain_control.py`'s epoch/staleness logic, `cgroup_cleanup.py`'s
  cgroup-specific sweep, `browser_control_broker.py`'s multi-principal
  ticketing** — all multi-tenant, multi-gateway, or specific to a hosting
  provider's own infrastructure (Fly Machines API, container recreation
  semantics). None of this applies to one EC2 instance serving one user
  (Intent §Constraints); the research report's own §4 reached the same
  conclusion independently and is cited rather than re-argued here.
- **`shutdown_watchdog.py`'s dead-man's-switch, `shutdown_forensics.py`,
  `lifecycle_ledger.py`, `delivery_ledger.py`** — all judged genuinely
  reusable *in principle* by the research, and all declined *for this
  item* on the same reasoning this project already applies to
  `conversation_store.py`'s single connection: "revisit the moment either
  signal actually shows up, not before." Nothing in this item's own
  intent asks for crash forensics or at-least-once delivery guarantees;
  building them speculatively is exactly the "more core, not more
  plugins" bet guideline 2 warns against. Named in §Non-goals with an
  explicit upgrade trigger, not silently dropped.
- **A directory-scan, manifest-based discovery registry for channel
  adapters** (mirroring `plugins.py`/`plugin_manifest.py`'s own
  `discover_plugins()`, or hermes's own deferred-loader `plugin.yaml` +
  `register(ctx)` convention) — declined for this item on this project's
  own explicit rule (CLAUDE.md: "A registry or dispatch seam for a family
  of pluggable backends earns its cost only once a second real member
  exists to register — build the single member as a direct call, not a
  lookup table of one, no matter how certain a second member seems.").
  This item ships exactly one adapter. Full reasoning and consequence in
  §Rejected alternatives and §Concerns — this is the one place this spec
  refines a promise intent.md made before the design guideline was
  weighed against it.
- **hermes's async-ingress-plus-separate-outbound-delivery model**
  (`adapter.handle_message` spawns a background task; the real reply goes
  out later via `gateway/delivery.py`'s own transport resolution) —
  declined for this one adapter in favor of a synchronous
  request-in/reply-out HTTP exchange. Full reasoning in §Rejected
  alternatives.

### Where the new code lives, and why

Following this project's existing split (`plugins.py`/`plugin_manifest.py`;
`conversation.py`/`conversation_store.py`) — a pure module and, separately,
whatever module actually touches real I/O:

- **`src/sadana/gateway.py`** (pure, no I/O, no import of `conversation.py`
  or anything below it): `MessageEvent`, `session_key_for()`. Nothing else
  — no adapter interface, no registry (see above).
- **`src/sadana/gateway_dispatch.py`** (glue, the one place allowed to
  import both `gateway.py` and `conversation.py`/`plugin_dispatch.py` —
  same role `plugin_dispatch.py` already plays for PLUGINS and
  CONVERSATION): `handle_inbound()`, the whole bridge.
- **`src/sadana/channel_webhook.py`** (I/O: binds a real TCP socket):
  the one webhook adapter. Its request-parsing is a pure function inside
  this file (`parse_webhook_request()`), unit-tested directly with no
  socket involved; the actual `ThreadingHTTPServer` wiring is exercised
  only by the standalone proof script (§Acceptance criteria, testing
  conventions below).
- **`src/sadana/gateway_daemon.py`** (I/O: signals, sockets, the lock
  file, systemd's socket): process lifecycle only — `run()`, the
  single-instance lock, `sd_notify`, signal handling, graceful stop.
- **`src/sadana/subcommands/gateway.py`**: `sadana gateway run
  [--host] [--port]`, one file owning its own parser and handler, matching
  `chat.py`'s and `conversations.py`'s existing convention exactly.
- **`src/sadana/persona.py`** (new, moved out of `subcommands/chat.py`):
  `load_or_seed_persona()` and `persona_path_from_config()` are not
  chat-specific — the daemon needs the exact same "read the user's
  configured persona, seeding a default the first time" behavior for
  every reply it sends. Two real callers exist as of this item (`chat`,
  the daemon), which is what earns extracting this into its own module
  now rather than duplicating it or reaching from one subcommand file
  into another's internals — the mirror image of the registry-of-one
  reasoning above: a shared thing is only worth its own module once a
  second real caller needs it, and that has now happened.

### The webhook exchange itself

`POST /webhook`, header `X-Sadana-Webhook-Secret: <configured secret>`,
JSON body `{"chat_id": "<str>", "text": "<str>", "thread_id": "<str,
optional>"}`. Response is always JSON, `{"ok": <bool>, "text": "<str>"}` —
`ok` tells the caller whether the agent's turn actually completed;
`text` is either the real reply or, on a turn failure, the same
`[exit_reason] detail` shape `_chat_loop` already prints to stderr today,
so a bad-provider or budget-exhausted failure looks the same everywhere in
sadana, not different by channel. HTTP status separates transport-layer
outcomes from turn outcomes: `200` for a webhook exchange that ran end to
end (whatever the turn's own result), `401` for a missing or wrong secret,
`400` for a malformed body, `500` only for a genuinely unexpected
exception escaping the handler (a bug, not an expected outcome — the same
posture `run_graph` already takes toward its own node failures).

**The secret is mandatory, not optional.** `gateway_daemon.run()` refuses
to start if `SADANA_GATEWAY_WEBHOOK_SECRET` is unset — failing loud at
startup rather than silently serving an unauthenticated endpoint, the same
posture `config.env_int`/`env_bool` already take toward a malformed value.
Default bind host is `127.0.0.1`; reaching it from outside the EC2
instance requires deliberately setting `--host 0.0.0.0` (or the matching
env var), a real decision made once, not sadana's default posture toward
a process that can trigger a model call and run plugin bodies.

**Session identity.** `gateway.session_key_for(event)` is
`f"webhook:{event.chat_id}"`, or `f"webhook:{event.chat_id}:{event.thread_id}"`
when a thread id is given — a pure function of the inbound envelope,
matching `build_session_key`'s own shape trimmed to what one platform
needs. `gateway_dispatch.handle_inbound()` tries
`conversation_store.load()` for that key first; on `ConversationNotFound`
it creates one, the same get-or-create branch `cmd_chat` already has,
just driven by whether the key exists rather than by an explicit
`--resume`/`--key` flag (there is no flag here — the webhook payload is
the only input).

**Synchronous, not fire-and-forget.** The HTTP response *is* the reply.
`channel_webhook.py`'s request handler runs the whole turn inline (each
connection already gets its own thread from `ThreadingHTTPServer`) and
writes the response only once `handle_inbound()` returns. This is
deliberately simpler than hermes's async-ingress/separate-outbound-delivery
model — accepted cost and upgrade trigger in §Concerns.

**Lifecycle.** `gateway_daemon.run()`: acquire the `flock` (exit loudly if
already held — another instance is running), start `ThreadingHTTPServer`
in a background thread, call `_notify_systemd("READY=1")`, register
`SIGTERM`/`SIGINT` handlers that set a `threading.Event`, block on that
event, then `server.shutdown()` (from the main thread — the one calling
thread `serve_forever()` isn't running on, avoiding the same-thread
deadlock the two need to not share) and return. No explicit drain timeout
is added: Python's own interpreter shutdown already waits for every
non-daemon thread (`ThreadingHTTPServer`'s per-request threads are
non-daemon by default) before the process actually exits, so an in-flight
webhook request finishes before the daemon does. §Concerns names the
cost of not bounding this.

### The four design guidelines

1. **Learn from the reference first** — the whole §Design section above
   is this; nothing here was designed without first checking what hermes
   did and naming what was declined and why.
2. **Reduce the number of bets.** The single biggest bet this item could
   have made — a channel-adapter registry with real discovery machinery —
   is exactly the one declined, per this project's own existing rule.
   What ships instead (`gateway.py`'s two pure names, a direct call from
   the daemon into `channel_webhook.py`) is cheap to reverse: nothing a
   second adapter needs later requires undoing anything built here.
3. **Catch the scenario at the least step-cost.** The scenario this item
   catches — "sadana cannot be reached asynchronously" — is caught by
   *adding a step* (a new process, a new entry point) because no existing
   step in sadana could absorb it: nothing before this item runs longer
   than one command invocation. That is the cheapest of the three moves
   available for a scenario this shape; making an existing step (like
   `sadana chat`) heavier would mean bolting a listening socket onto an
   interactive REPL, which solves nothing for a user who isn't at the
   terminal.
4. **Minimise mutable state.** State inventory: the `flock` (must persist
   across the OS's own process boundary — cannot be derived, a real lock
   is the only way two processes agree on "who's running"); the
   `threading.Event` used for shutdown signaling (in-memory,
   process-lifetime only, derived fresh from the OS signal each run —
   nothing persisted); the webhook secret and bind host/port (config
   values, not state). Nothing else. No lifecycle ledger, no delivery
   ledger, no PID file — all declined above specifically because they'd
   be state with no consumer yet.

## Interface

```python
# src/sadana/gateway.py (pure)
@dataclass(frozen=True)
class MessageEvent:
    platform: str            # "webhook" for this item's one adapter
    chat_id: str
    thread_id: str | None
    text: str

def session_key_for(event: MessageEvent) -> ConversationKey: ...


# src/sadana/gateway_dispatch.py (glue)
async def handle_inbound(
    conn: sqlite3.Connection,
    event: MessageEvent,
    *,
    plugin_set: plugin_dispatch.PluginSet,
    persona: str,
    provider: str,
    model: str,
) -> str:
    """Load-or-create the conversation `session_key_for(event)` names, run
    one turn with `event.text` as the user input, persist it, and return
    the reply text — `final_text` on a clean turn, or the same
    `[exit_reason] detail` shape `_chat_loop` prints on any other one.
    Never raises for an expected turn outcome."""


# src/sadana/channel_webhook.py (I/O)
@dataclass(frozen=True)
class WebhookUnauthorized: ...

@dataclass(frozen=True)
class WebhookBadRequest:
    detail: str

def parse_webhook_request(
    body: bytes, headers: Mapping[str, str], *, secret: str
) -> MessageEvent | WebhookUnauthorized | WebhookBadRequest: ...

def make_server(
    host: str, port: int, *, secret: str, on_message: Callable[[MessageEvent], str]
) -> http.server.ThreadingHTTPServer: ...


# src/sadana/gateway_daemon.py (I/O)
def run(*, host: str, port: int, secret: str, on_message: Callable[[MessageEvent], str]) -> int:
    """Blocks until SIGTERM/SIGINT. Returns 0 on a clean stop, 1 if the
    single-instance lock is already held."""


# src/sadana/subcommands/gateway.py
def build_gateway_parser(subparsers) -> None: ...
def cmd_gateway_run(args: argparse.Namespace) -> int: ...


# src/sadana/persona.py (moved out of subcommands/chat.py, unchanged behavior)
def persona_path_from_config() -> Path: ...
def load_or_seed_persona(path: Path) -> str: ...
```

## Acceptance criteria

- [ ] `gateway.session_key_for()` returns the same key for two
      `MessageEvent`s sharing `chat_id`/`thread_id`, and a different one
      for a different `chat_id`.
- [ ] `gateway_dispatch.handle_inbound()`, called twice against the same
      `conn`/`event.chat_id` with a monkeypatched `model_access.send`
      (no real model call, matching `test_subcommands_chat.py`'s own
      precedent), produces one conversation row whose `next_turn_seq`
      reaches `2` — proving the second call continued the first
      conversation rather than creating a new one.
- [ ] `channel_webhook.parse_webhook_request()` returns a `MessageEvent`
      for a well-formed request with the correct secret, a
      `WebhookUnauthorized` for a missing or wrong one, and a
      `WebhookBadRequest` for malformed JSON or a missing required field
      — all as direct unit tests, no socket involved.
- [ ] `gateway_daemon.run()` refuses to start (returns `1`, binds
      nothing) when the webhook secret is unset.
- [ ] `gateway_daemon.run()` acquires the `flock`; a second call while
      the first holds it returns `1` without touching the socket.
- [ ] A standalone script (`scripts/prove_gateway_webhook_e2e.py`,
      matching `scripts/prove_plugin_dispatch_e2e.py`'s existing shape)
      starts a real `gateway_daemon.run()` in a background thread, POSTs
      a real HTTP request to it twice against the same `chat_id`, and
      asserts: the first response's `ok` is `true`, the second turn
      continued the first conversation, a wrong secret gets `401`, and
      `SIGTERM` (sent to the script's own process, or a direct call to
      the same shutdown path) lets an in-flight request finish before
      the process stops. Its output is pasted into `review.md` §Evidence
      (CLAUDE.md's rule for a block's first real external round trip).
- [ ] `persona.py`'s two functions pass the two existing tests, moved
      verbatim from `test_subcommands_chat.py` to a new
      `tests/unit/test_persona.py`; `chat.py` imports from `persona.py`
      and every existing `chat.py` test still passes unmodified.
- [ ] `make verify` ends `VERIFY OK`.

## Non-goals

- Every channel adapter besides the generic webhook — Telegram, Slack,
  email, SMS, Discord, and the rest of hermes's catalogue. Each is its
  own later CHANNELS-xx work item (Intent §Shape).
- A channel-adapter registry or discovery mechanism. Declined until a
  second real adapter exists to register (§Design, §Rejected
  alternatives) — the next channel item is what earns and builds it.
- Crash forensics (`shutdown_forensics.py`-equivalent), a lifecycle
  ledger, an at-least-once outbound delivery ledger
  (`delivery_ledger.py`-equivalent), and a dead-man's-switch wedged-loop
  watchdog (`shutdown_watchdog.py`-equivalent). All judged genuinely
  reusable by the research, all deferred until a real signal (an actual
  wedge, an actual "did it crash or did I stop it" support question)
  shows up — the same posture CLAUDE.md already takes toward
  `conversation_store.py`'s single connection.
- Fleet provisioning, multi-instance federation, a hosted relay, or
  scale-to-zero — out of scope for sadana-harness entirely (Intent
  §Constraints, `cli_shell_blueprint.md`'s own established EC2 model).
- A `--replace` hot-swap path or the PID-file half of hermes's two-tier
  lock — nothing here needs to identify a running daemon from outside
  the daemon itself yet; the `flock` alone answers "is one already
  running" for this item's own purposes.
- Updating `cli_shell_blueprint.md`'s own §6 table to mark items 5/6
  unblocked — left to whichever future work item actually builds
  `sadana gateway`'s full surface or `sadana setup`.
- A bounded shutdown-drain timeout — relying on Python's own
  non-daemon-thread join at process exit instead (§Design, §Concerns).

## Rejected alternatives

- **A manifest-and-directory-scan discovery registry for channel
  adapters**, mirroring `plugins.py`/`plugin_manifest.py`. Rejected per
  this project's own explicit rule against building a registry for one
  member. The cost of being wrong about this — a slightly bigger diff on
  the *second* adapter's own work item — is far smaller than the cost of
  building speculative discovery machinery now with nothing real to
  discover.
- **An abstract `ChannelAdapter` base class** (matching hermes's
  `BasePlatformAdapter`, three abstract methods) even without a registry
  behind it. Rejected: with exactly one implementation and a synchronous
  request/response model that doesn't even need a distinct `send()` step
  (the HTTP response itself is the send), an interface with one
  implementer is the literal case this project's own tooling already
  flags as unnecessary abstraction. `MessageEvent` is kept — it has a
  real second reason to exist (the shape `handle_inbound()`'s one
  parameter needs) — the adapter interface around it does not, yet.
- **hermes's async-ingress, separate-outbound-delivery model.** Rejected
  for this one adapter: it exists in hermes to support platforms whose
  own protocol is asynchronous by nature (a long-poll bot API, a
  websocket gateway) and to let a slow agent turn not block that
  platform's own event loop. A generic webhook has no such constraint —
  its caller is already waiting for an HTTP response — so the simpler
  synchronous model does the same job with no background task, no
  typing-indicator machinery, and no `gateway/delivery.py`-equivalent
  transport-resolution step to build. Named explicitly as an upgrade
  path in §Concerns, not silently foreclosed.
- **Binding `0.0.0.0` by default.** Rejected — a process that can trigger
  a real model call and run plugin bodies should not be reachable from
  outside the host until someone deliberately says so.
- **Keeping persona loading inside `subcommands/chat.py`, duplicated into
  the daemon.** Rejected once a second real caller (the daemon) needed
  the exact same behavior — see §Design's reasoning, the mirror image of
  the registry-of-one rule applied to file layout.

## Concerns

- **A slow turn can exceed the calling webhook's own timeout**, since the
  HTTP response only comes back once the whole turn (including any
  plugin `call` node's real network work) finishes. Accepted for this
  item — the "smallest version still worth having" is a real reply over
  a real channel, not a bounded-latency guarantee. The real fix (an
  immediate ack plus a later, separately-delivered reply) is exactly what
  hermes's async-ingress model buys, and is the natural shape of the
  *second* channel's own work item if a channel that actually needs it
  (a chat platform with its own delivery timeout) shows up before then.
- **This item does not fully deliver intent.md's own "can register it
  without touching the background process's own code" for a second
  adapter** — refined here, not silently dropped. Adding a second channel
  still means writing a new file shaped like `channel_webhook.py` and
  adding one call to it from `gateway_daemon.run()`'s own startup — a
  small, bounded touch, not a rewrite, but not zero either. A true
  "register without touching core" property starts holding from the
  *third* adapter on, once the second one's own work item has earned and
  built the registry this item deliberately declined. Flagged because it
  narrows a promise intent.md made in good faith before this design
  guideline was weighed against it, not because either was wrong to
  write down.
- **The mandatory shared secret is a single static header value**,
  checked with a plain string comparison rather than a constant-time one
  — a real, if narrow, timing-side-channel gap. Judged acceptable for a
  single-user instance behind a secret only its own owner sets, but named
  rather than silently accepted; a constant-time compare
  (`hmac.compare_digest`) costs one line and will be used in the actual
  implementation even though the risk is small, since there's no reason
  not to.
- **No policy skill named `project-structure` or `reference-lookup`
  exists in this repository** (`.claude/skills/` has only `plan-skill`,
  `design-skill`, `build-skill`, `deploy-skill`, `testing-conventions`).
  File layout above was instead inferred directly from this codebase's
  own existing module splits (`plugins.py`/`plugin_manifest.py`;
  `conversation.py`/`conversation_store.py`; the `subcommands/` package)
  — named explicitly per design-skill's own instruction to say so rather
  than leave the gap unaddressed.
- **No new cross-block CLAUDE.md rule is proposed by this spec.** Every
  constraint applied above is either an existing CLAUDE.md rule
  (real-I/O-is-its-own-module, names-not-pointers, the registry-of-one
  rule, the standalone-script rule for a first real external round trip)
  or specific to this one block, not a pattern that should bind every
  future block.
