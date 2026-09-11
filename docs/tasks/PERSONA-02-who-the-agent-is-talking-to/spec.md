# Spec: Who the agent is talking to

Intent: docs/tasks/PERSONA-02-who-the-agent-is-talking-to/intent.md

## Requirements

1. **The installation has an owner, resolved in one place.**
   `memory.owner_account() -> AccountKey` reads `SADANA_MEMORY_ACCOUNT`,
   defaulting to `"local"` — the same variable and the same default
   `subcommands/chat.py:85` already resolves inline, so an installation that
   has set it keeps the identity it already had. `chat.py`'s inline default is
   replaced by the call. *(intent: "The assistant has an owner".)*

2. **Work the owner scheduled is the owner's work.** `scheduling.tick()`
   states `memory.owner_account()` as the account for every trigger it fires.
   The conversation key is unchanged — still `schedule:<trigger-name>` — so a
   trigger keeps its own continuing thread while the person behind it is the
   owner. *(intent: "A schedule they wrote speaks in their voice and remembers
   into their memory"; constraint: "A conversation is still one thread".)*

3. **A channel can never assert who it is.** `gateway_dispatch.handle_inbound`
   gains a keyword-only `account: AccountKey | None = None`; `None` keeps
   today's behaviour of deriving `platform:chat_id` from the envelope. The
   parameter is for a trusted in-process caller only: `MessageEvent` gains no
   account field, `channel_webhook` never sets one, and a payload key named
   `account` is ignored the way every other unknown key already is.
   *(intent: "Nobody becomes the owner by arriving".)*

4. **What the schedules remembered becomes the owner's, once, losing
   nothing.** On store open, every `memory_entries` row whose `account_key`
   starts with `schedule:` is re-keyed to the owner. Where the owner already
   holds that `entry_key`, the incoming one is kept under
   `<entry_key>--<source account>` rather than overwriting: the intent's
   "loses nothing" is stronger than tidiness. Idempotent — after one pass no
   `schedule:` row remains, so the next open does nothing. *(intent: "becomes
   part of what the assistant knows about the owner"; constraint: "Moving
   what the schedules remembered happens once, and loses nothing".)*

5. **Only remembered things move.** A rubric override or a character
   selection attached to a `schedule:` account is a setting somebody typed,
   not something the assistant learned, and moving it would silently
   overwrite a setting of the owner's own. Both stay where they are, and
   requirement 7's listing is what makes them findable afterwards.
   *(intent's constraint bounds the move to what was "learned"; see
   § Concerns, this is the spec's own reading of it.)*

6. **A conversation records whose it is.** A new `conversation_accounts`
   table (`conversation_key` primary key, `account_key`), written inside the
   same transaction that creates the conversation. Nothing reads it during a
   turn; it exists so requirement 7 can answer. *(intent: "a person can ask
   who the assistant has spoken with".)*

7. **`sadana persona accounts` lists the accounts the assistant has spoken
   with**, each marked with whether it is the owner and which character it
   has selected. The list is the union of the accounts that have a
   conversation, a remembered entry, or a character selection. *(intent: "a
   name they need is something they can look up rather than guess".)*

8. **Choosing a character for a name nothing has used fails, loudly, except
   your own.** `persona use <account> <name>` exits non-zero with a message
   when `<account>` is absent from requirement 7's listing. The owner's
   account is always accepted, listed or not, because picking a voice before
   saying anything is an ordinary first move. *(intent: "fails, and says so
   … except for the owner's own name".)*

9. **Nothing recorded before this change stops working.** No column is added
   to `conversations`; a conversation with no `conversation_accounts` row
   loads, answers, and lists as owned by nobody. *(intent's constraint.)*

## Design

One new idea — the installation has an owner — and four places that have to
agree about it. The owner is a *name*, resolved from config on every call,
never stored: nothing in this design writes down who the owner is, so nothing
can go stale when it changes.

### Modules

- **`src/sadana/memory.py`** (pure, +1 function). `owner_account()`, beside
  `account_key_for()` and `default_rubric()`, which already resolve identity
  and config-backed behaviour respectively. No new module: this is one
  expression in the module that already owns `AccountKey`.
- **`src/sadana/memory_store.py`** (+1 migration, +1 query).
  `adopt_scheduled_memories(conn, owner)` for requirement 4, and
  `accounts_with_memories(conn)` feeding requirement 7.
- **`src/sadana/conversation_store.py`** (+1 table, +1 parameter, +1 query).
  `conversation_accounts` in `_SCHEMA`; `create()` takes `account_key` and
  writes both rows in its existing `write_txn`; `accounts_with_conversations()`.
  A new table rather than a column on `conversations`, which keeps
  `Conversation`, `_conversation_row()` and the `save()` upsert path
  completely untouched — see § Rejected alternatives.
- **`src/sadana/persona_store.py`** (+1 query). `known_accounts(conn)` unions
  the three sources, and `account_exists(conn, account)` is what requirement 8
  asks.
- **`src/sadana/gateway_dispatch.py`** — the keyword-only `account` override.
- **`src/sadana/scheduling.py`** — passes `memory.owner_account()`.
- **`src/sadana/client_surface.py`** — `_create()` passes its `account` to
  `conversation_store.create()`; `open_runtime()` calls
  `adopt_scheduled_memories()` once, next to the `ensure_schema` calls.
- **`src/sadana/subcommands/persona.py`** — the `accounts` verb, and the
  refusal in `use`.
- **`src/sadana/subcommands/chat.py`** — its inline default becomes the call.

### Flow

Nothing changes on the turn path. A scheduled trigger now hands the bridge an
account instead of letting the bridge derive one; a conversation's creation
writes one extra row in a transaction it was already opening; and a process
start runs one `UPDATE ... WHERE account_key LIKE 'schedule:%'` that does
nothing on every start after the first.

### Policies applied

- **`testing-conventions`** — applied. New tests:
  `tests/unit/test_memory.py` (owner resolution from the environment and its
  default), `tests/unit/test_memory_store.py` (the adoption, including the
  collision case and idempotency), `tests/unit/test_conversation_store.py`
  (the account row, and a legacy conversation with none),
  `tests/unit/test_scheduling.py` (a fired trigger states the owner, not
  `schedule:<name>`), `tests/unit/test_gateway_dispatch.py` (a webhook event
  still derives its own account, and an override reaches the door),
  `tests/unit/test_subcommands_persona.py` (the listing, the refusal, the
  owner exception). Assertions are contracts, not snapshots: the scheduling
  test asserts the account equals `memory.owner_account()`, never the literal
  `"local"`.
- **`project-structure` and `reference-lookup` do not exist** in
  `.claude/skills/`. No security, brand or UX skill is present, which is
  worth saying out loud here because requirement 3 is a security requirement
  and no policy skill covers it — the reference corpus does, below.

### Reference corpus

Read `gateway/platforms/base.py` (CHANNELS, 4k lines; §§ around the
`authorization_is_upstream` and `enforces_own_access_policy` properties) and
`gateway/platform_registry.py`, after filtering the index to CHANNELS and
SESSION-STORE production code. Hermes has no end-user account concept of our
shape — it is single-tenant per `HERMES_HOME`, and its "owner" mostly means
process and lock ownership — but it has faced requirement 3 directly.

**Adopted**, and it is the strongest argument in this spec:
`base.py:3347-3357` records that owner binding is resolved by a trusted,
authenticated upstream *before* delivery, and that "the author id is read off
the event the connector observed, never gateway-asserted"; every
network-exposed adapter leaves the flag `False` and default-denies. Their
distinction between *authorization delegated to a trusted caller* and
*authorization absent* is exactly requirement 3's shape: `scheduling.py` is a
trusted in-process caller and may state an account; a socket may not, and the
default is to derive rather than to trust.

**Declined**: the machinery around it — per-platform allowlists
(`{PLATFORM}_ALLOWED_USERS`), adapter capability flags, subclass overrides
per channel. With one adapter and one owner that is a policy hierarchy with a
single member, which is the registry-of-one failure CLAUDE.md already names.
We take the rule and not the framework.

### Guideline 2 — reducing bets

After the plugin seam, so the narrow case applies: do not foreclose it. Three
bets. The owner *name* comes from the variable that already held it, so an
installation's identity does not change under it — that is the bet not taken.
The `conversation_accounts` table is a bet on recording ownership at all; it
is additive, unread by the turn path, and droppable without touching a line
of turn code. The adoption pass is the one irreversible bet in this item:
memories move, and there is no undo. It is bounded — one prefix, one
direction, one time — and § Concerns says what it costs.

### Guideline 3 — which of the three moves

It makes an existing step heavier, twice, in the cheapest places available.
Conversation creation writes one more row inside a transaction it already
opens; process start runs one no-op `UPDATE`. Neither lands on the turn path.
The cheaper alternative for the listing — deriving accounts from the memory
tables alone, adding nothing — was rejected because it cannot see the case the
listing exists for: a stranger who chatted and never triggered a memory write
would be invisible, which is precisely the account the owner needs to look up.

### Guideline 4 — state inventory

| State | Stored or derived | Why |
|---|---|---|
| Who the owner is | **Derived**, per call, from config | Nothing writes it down, so nothing goes stale when it changes. |
| Which account a conversation belongs to | **Stored**, one row per conversation | Not derivable: a conversation key carries a platform and a chat id, and the owner's scheduled conversations deliberately no longer match their own key. |
| The accounts that exist | Derived, a union of three queries | No index, no cache, no second copy to drift. |
| Where a scheduled run's memories live | **Stored**, and changed once by the adoption | The rows already exist; this moves them rather than adding. |

Net: one new table with one column of real state, one config-derived name, no
new cached value.

## Interface

**`memory.owner_account() -> AccountKey`** — `SADANA_MEMORY_ACCOUNT`, else
`"local"`.

**`memory_store.adopt_scheduled_memories(conn, owner: AccountKey) -> int`** —
returns how many entries moved. Idempotent. Raises nothing a caller must
handle; a failure to migrate must not stop a process from starting
(§ Concerns).

**`memory_store.accounts_with_memories(conn) -> frozenset[AccountKey]`**.

**`conversation_store.create(conn, conversation, *, now, account_key)`** — one
new required keyword. **`accounts_with_conversations(conn) ->
frozenset[AccountKey]`**.

**`persona_store.known_accounts(conn) -> tuple[AccountKey, ...]`** (sorted),
**`account_exists(conn, account) -> bool`**.

**`gateway_dispatch.handle_inbound(runtime, event, *, account=None)`** —
`None` derives `platform:chat_id` as today.

**CLI** — `sadana persona accounts` prints one line per account: the key, a
marker for the owner, and the selected character or nothing. `sadana persona
use <account> <name>` exits 1 with `error: no account named '<account>' has
used this assistant` when the account is unknown and is not the owner.

## Acceptance criteria

- [ ] A trigger fired by the scheduler runs under `memory.owner_account()`,
      not `schedule:<name>`, and its conversation key is still
      `schedule:<name>`.
- [ ] A character selected by the owner is the voice of a newly created
      scheduled conversation.
- [ ] A fact remembered during a scheduled run is visible in
      `sadana memory list <owner>`.
- [ ] Entries recorded under `schedule:daily` before the change appear under
      the owner afterwards, and a second process start moves nothing.
- [ ] An entry whose key the owner already holds survives the move under a
      suffixed key; neither text is lost.
- [ ] A rubric override under a `schedule:` account is not moved and does not
      overwrite the owner's.
- [ ] A webhook message still runs under `webhook:<chat_id>`, and a payload
      containing an `account` field changes nothing.
- [ ] `sadana persona accounts` lists an account that has only ever chatted,
      with no memories and no selection.
- [ ] `persona use nobody working` exits non-zero; `persona use <owner>
      working` succeeds on a store with no history at all.
- [ ] A conversation row written before this change loads, answers, and
      appears as owned by nobody rather than crashing the listing.
- [ ] `make verify` ends `VERIFY OK`.

## Non-goals

Naming a webhook correspondent, or promoting one to anything other than a
stranger. Serving more than one person from one installation. Moving a
scheduled account's settings (see requirement 5). Any command that *sets* the
owner — it is a config value, and config is edited where config lives.

## Open questions

Whether the adoption pass should ever be removable. It is a one-time data
migration living in a code path that runs forever; a year from now it is dead
weight that still runs on every start. Nothing decides when to delete it, and
whoever notices it should be allowed to.

## Rejected alternatives

- **An `account_key` column on `conversations`** — the obvious shape, and
  CLAUDE.md explicitly blesses the guarded `ALTER TABLE` for it. Rejected
  because `save()` upserts every column from `_conversation_row()`, so the
  value would have to live on the `Conversation` dataclass to survive a save
  — and that field would then be required at every construction site,
  including `run_child`'s, `eval_harness`'s and the tests', for a value a
  child conversation has no answer to. A side table keeps the hottest path in
  the codebase untouched.
- **Deriving the account list from the memory tables only** — no new state at
  all, and it cannot see a chat that never wrote a memory. Rejected under
  guideline 3: it fails the one case the feature exists for.
- **An `account` field on `MessageEvent`** — reads naturally ("the envelope
  says who sent it") and is exactly the shape hermes warns against: a
  network-filled struct asserting an identity. A keyword argument can only be
  passed by a Python caller; a dataclass field gets populated by whatever
  parses the payload next.
- **Making the account override required at both call sites** — would have
  pushed `account_key_for` back out into `subcommands/gateway.py`, undoing
  the single mapping `gateway_dispatch` exists to hold.
- **A one-shot migration command the person runs** — safer (nothing moves
  unasked) and rejected against the intent, which says nothing learned is
  thrown away *on the day this changes*, not on the day someone remembers to
  run a command.
- **Moving rubric overrides and character selections too** — a literal
  reading of "it is you, memory included". Rejected: those are settings, and
  a schedule account's setting overwriting the owner's own is a silent loss
  of something a person typed deliberately.

## Concerns

**Requirement 5 is the spec reading the intent, and a reviewer should check
me on it.** The user said a scheduled run "is you, memory included". I have
taken "memory" to mean things the assistant *learned* — `memory_entries` —
and excluded the two account-keyed settings, on the grounds that moving a
setting can destroy one the owner chose. That is a defensible line and it is
still a line the interview did not draw. If the user meant everything under
that account key, requirement 5 inverts and requirement 4 needs a rule for
which setting wins.

**The adoption is irreversible and runs automatically.** One `UPDATE` at
process start, no undo, no confirmation. The collision rule keeps both texts,
so nothing is destroyed, but a person who deliberately keeps notes under a
`schedule:` account — which `sadana memory` lets them do today — will find
them silently relocated on the next start. Nothing warns them. I chose
automatic over a command because the intent asked for the move to happen on
the day of the change; the cost is that it happens to people who did not read
the change.

**Two policies pull opposite ways on what happens when the adoption fails.**
CLAUDE.md says an observability write's failure is caught and logged rather
than raised into the run it observes, and `config.py`'s posture is that a
value that cannot be resolved should fail loudly at the one call site that
reads it. A half-finished memory migration is neither: it is not
observability, and it is not a config parse. I have followed the first —
`open_runtime()` must not fail to start because a migration could not
complete — and the cost is that a partial move can go unnoticed until someone
looks for a fact that is in neither place. The alternative, refusing to start,
turns a data-shape surprise into an outage on every surface at once.

**Requirement 3 is enforced by convention, not by construction.** Nothing
stops a future channel author from reading an account out of a payload and
passing it as the override — the parameter exists and is trusted. A test pins
that today's webhook path does not, and the CLAUDE.md amendment below is the
rule that makes it reviewable, but a keyword argument is a weaker guarantee
than a type that cannot be constructed from untrusted input. Given one
adapter, I judged a trusted-caller parameter cheaper than a capability type;
a second network adapter is the moment to revisit it.

**The refusal will reject selections people were already told to make.**
`persona use schedule:daily-standup working` works today and is the documented
workaround for PERSONA-01's open finding. After requirement 2 that account is
never resolved again, and after requirement 8 the command that created it
refuses to repeat it. The rows are not deleted, so `persona accounts` shows
them; nothing tells the person they are inert.

## Changed during design

Two things moved between reading the intent and writing this.

The account-on-the-conversation question started as a column, which is what
CLAUDE.md's own migration rule is written for, and reading `save()` turned it
into a table: the column would have had to become a `Conversation` field to
survive an upsert, and that field has no answer for a child conversation. The
rule still applied — it just pointed at the other option once the save path
was read rather than assumed.

The channel override started as a field on the envelope, which is the natural
place for "who is this from", and the reference corpus argued it out of the
design inside ten minutes: hermes's own note that an author id is "read off
the event the connector observed, never gateway-asserted" is the same
decision, made by somebody who had to live with the alternative.
