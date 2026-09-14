# Spec: parked approvals

Intent: docs/tasks/H18-parked-approvals/intent.md

Author: adam (engineer). Status: approved.

## Requirements

1. Before anyone is asked to approve, decline, or answer anything, the need
   for that answer is written down as one row in a new `approvals` table —
   never only as an in-memory value or a function's return value. Traces to
   intent's Proposed outcome ("written down before anyone is asked") and
   Constraints ("recorded before anyone is asked — never only in memory").
2. A `call` node's body never runs before it is approved. When a run reaches
   a `call` node under a non-interactive caller, the run stops there instead
   of blocking on `input()`, in exactly the same way a `wait` node already
   stops a run today. Traces to intent's Problem (the daemon-freeze) and
   CLAUDE.md's existing rule ("A `call` node's body runs only after
   `approve()` returns `True` — never unconditionally").
3. A parked wait or call can be answered from outside the process that
   created it, through the door, by someone who was not there when it was
   asked. Traces to intent's Proposed outcome ("can be found and answered
   from outside the process that created it").
4. A parked wait or call that nobody answers within a bounded time expires
   on its own; an expired call is treated as declined, an expired wait
   resumes the run with a decline, never a silent approval. Traces to
   intent's Proposed outcome ("If nobody answers... it expires... treated
   the same as an explicit refusal") and Constraints.
5. The interactive TTY experience — `sadana chat`, one person, one keyboard —
   is unchanged: a `call` node still blocks synchronously and asks right
   there, exactly as `F1-call-node-approval` and `G1-call-node-run` already
   built it. Traces to intent's Constraints ("the interactive, at-a-keyboard
   experience is not touched") and CLAUDE.md ("The interactive approver
   stays for a TTY").
6. Exactly one outstanding pause exists per conversation at a time, matching
   `plugin_pauses`' own existing `PRIMARY KEY (conversation_key)` invariant —
   this work item does not relax it. Traces to CLAUDE.md ("One pause per
   conversation") and the existing `plugin_pauses` schema
   (`conversation_store.py:100-109`).
7. The console can find and act on parked approvals through the door's own
   grammar: list them (filtered by state/kind/run_id, ordered), read one,
   and drive it through its three actions (approve, decline, answer) —
   nothing about `GET/POST /v1/approvals...` is a special case of the
   grammar `docs/console/wire.md` §1 already defines. Traces to intent's
   Affected users and systems ("a way to list what's waiting, and a way to
   answer each kind of request") and the work item's own console-shape
   description.

## Design

**Reference-corpus check (guideline 1).** `../hermes-agent/tools/approval.py`
was already read in full for `F1-call-node-approval/spec.md`'s own Rejected
alternatives and declined there for carrying a population of concerns this
project has repeatedly declined — per-session locks and eviction, a
denial-breaker threshold, a gateway notify/resolve callback registry, a
shared timeout budget with an unrelated deadline system. Nothing in this
work item changes that verdict; if anything it sharpens it, since this item
is the concrete shape hermes's own machinery was solving for and this
project is solving it with one SQLite table, three actions, and a tick
function instead. No new file in the reference corpus was found analogous
enough to `plugin_pauses`' own resume-by-row shape to warrant a second look;
`GATEWAY-DAEMON-02`'s own spec already made that comparison for `wait` and
this item is its `call` sibling plus a wire surface, not a new mechanism.

**Where this attaches — one table, one column addition, one sentinel, seven
touched files.** The shape already half-exists: `plugin_pauses`
(`conversation_store.py:100-109`) already persists a `wait`-paused run before
anyone answers it; `_run_graph`'s `call` branch
(`plugin_manifest.py:506-526`) already gates on `approve()` per
`F1`/`G1-call-node-run`. This item does three things to that shape: (a)
teaches the `call` branch to *park* — return a paused result instead of
awaiting — when the caller is non-interactive; (b) gives every pause,
`wait` or `call`, a matching `approvals` row with a state machine and a TTL;
(c) wires the door's six verbs over that table.

### 1. `plugins.py` — additive fields only

- `DagResult` gains `paused_kind: Literal["wait", "call"] | None = None` and
  `paused_value: object = None`. Both trailing and defaulted, the same shape
  `paused_node` itself already took when `GATEWAY-DAEMON-02` added it
  (`plugins.py:236`) — every existing construction of a terminal `DagResult`
  means exactly what it meant.
- `ResumeState` gains `kind: Literal["wait", "call"]` and
  `decision: Literal["approve", "decline", "answer"] | None = None`. `kind`
  is not optional — a resume always knows which kind of node it is
  continuing past, because `conversation_store.Pause` (below) always carries
  it. `decision` defaults to `None` so a hand-built `ResumeState` in an
  existing unit test (there are several in
  `tests/unit/test_plugin_manifest.py`) keeps meaning what it meant: a
  `wait` resume with an answer, no `decision` needed until this item's
  branching reads it.
- Neither `plugins.py` nor `plugin_manifest.py` gains an import of
  `conversation.py` — CLAUDE.md's own leaf-acyclicity rule, unaffected by
  this item (approvals persistence lives in `conversation_store.py`, which
  already imports `plugins.py`, never the reverse).

### 2. `plugin_manifest.py` — the parking sentinel and the split `call` gate

`ApproveFn` (`plugins.py:971`) stays exactly the shape it is — a real,
callable, awaitable value, never `None` — because every existing call site
(`run_graph`'s own default parameter, `build_dispatch`'s, `resume_paused_run`'s)
already types it as required-with-a-default, and a `None`-sentinel would
mean threading an `is None` check through three modules instead of one.
Parking is instead a second concrete `ApproveFn` implementation, recognised
by **identity**, not by a flag or a new parameter:

```python
async def parking_approve(plugin: str, node: str, value: object) -> bool:
    """Never actually awaited for a real decision. `_run_graph` recognises
    this exact function object, by identity, before it would call it, and
    parks the walk instead — the same way `KINDS_NOT_RUNNABLE` is checked
    before a node kind's body ever runs. Exists as a real ApproveFn rather
    than `None` so `run_graph`/`build_dispatch`/`resume_paused_run` keep one
    required-with-a-default parameter shape throughout; if this ever *is*
    awaited (a caller that bypasses `_run_graph`'s own check), the safe
    answer is still `False` — parking is not the same value as approval."""
    return False
```

`_run_graph`'s `call` branch (`plugin_manifest.py:506-514` today) becomes:
check `approve is parking_approve` first; if so, return a paused
`DagResult(paused_node=node.name, paused_kind="call", paused_value=value,
text=f"{manifest.name}'s {node.name!r} step is waiting for approval.")`
without ever calling `approve`. Otherwise, the existing
`await approve(...)` / declined-fails / body-runs-on-a-thread sequence is
unchanged. This is the one-paragraph documentation the work item asks for,
stated once, at the sentinel's own definition and cross-referenced from the
branch — not repeated at every call site.

The resume branch (`plugin_manifest.py:459-472` today) splits on
`resume.kind`. For `kind == "wait"`, behavior is exactly what it is today:
`resume.value` stands in for the wait node's own output and the walk
continues from `wait_node.next`. For `kind == "call"`:
- `resume.decision == "decline"` ends the walk at that node exactly like any
  other declined `call` (`failed(node, "declined")`), without ever resolving
  or running the body.
- `resume.decision == "approve"` runs the body now, off the event loop
  (`asyncio.to_thread`, matching the live path at `plugin_manifest.py:511-514`),
  with `resume.value` (the `paused_value` a live park recorded) as its
  input — the exact value the body would have received had it run
  synchronously at park time. The `Artifact`-checking branch immediately
  below the live `call` path (`plugin_manifest.py:515-526`) is reused
  unchanged for this returned value; it is the same "a body's return value
  is inspected the same way regardless of when the body actually ran" shape
  `resume`'s own existing wait-side reuses `_coerce_text`/trace-append for.
- Any other `resume.decision` on a `call`-kind pause (`None`, `"answer"`) is
  a caller error — asserted, not silently accepted, the same posture
  `resume_paused_run`'s own `assert pause is not None` already takes toward
  its caller's obligation to have checked first.

`_default_approve` (`plugin_manifest.py:292-302`) is untouched — it remains
the one interactive implementation, unreachable from any non-interactive
call site after this item, since every one of them now passes
`parking_approve` instead of relying on the module-level default meant for
`run_graph`'s own signature.

### 3. `plugin_dispatch.py` — `resume_paused_run` grows a decision

`resume_paused_run`'s second positional parameter is renamed from
`conversation_key` to `conversation` (still a `str`/`ConversationKey` — no
type change, a naming-only cleanup that matches the value's own name at
every call site already, e.g. `client_surface.py:415`'s existing
`plugin_dispatch.resume_paused_run(conn, conversation, text)`). Its third
parameter, `payload_text: str`, is renamed `payload: str | None` and a new
keyword-only `decision: Literal["approve", "decline", "answer"]` is added
(no default — every caller, TTY included, now states which of the three this
is; there is no safe generic default the way `approve`'s interactive one
was). `answered_by: str | None = None` is added for the door's callers,
which know an acting account (`"console:" + sub`, `docs/console/wire.md`
§2) the TTY and webhook paths do not.

Inside, before building `ResumeState`, `resume_paused_run` looks at
`pause.kind` (new field on `conversation_store.Pause`, below):
- `pause.kind == "wait"`: `decision` must be `"answer"`; `payload` is the
  resuming text, unchanged from today's behavior.
- `pause.kind == "call"`: `decision` is `"approve"` or `"decline"`;
  `payload` carries the decline reason when given (`None` for approve).

`ResumeState(node=pause.node, value=payload if pause.kind == "wait" else
pause.paused_value, trace=pause.trace, artifacts=pause.artifacts,
kind=pause.kind, decision=decision)` is what actually reaches `run_graph` —
for a `call` resume, the value threaded through is the *parked* value
(`pause.paused_value`, what the body was always going to receive), not the
caller's `payload`, since `payload` here is a decline reason or nothing, not
the node's input.

After `run_graph` returns, exactly where `delete_pause`/`save_pause_from_result`
already run (`plugin_dispatch.py:389-398`), the same `write_txn` also marks
the conversation's approvals row: `state` set to `"approved"`, `"declined"`,
or `"answered"` (matching `decision`), `answered_by`, `answer=payload`,
`decided_at=now`. One new `conversation_store` function
(`mark_approval_decided`, below) does this, called from inside the same
transaction `delete_pause`/`save_pause_from_result` already open — "ledgered"
per the work item's own instruction means this function also calls
`ledger.record_change(c, noun="approvals", ...)` before that transaction
commits, the same pattern every other state-changing write in this store
already follows (`conversation_store.py:584-590` for the conversation-level
analogue).

`build_dispatch`'s own `approve` parameter (`plugin_dispatch.py:152`) is
untouched — it already accepts any `ApproveFn`, `parking_approve` included,
with no change needed there.

### 4. `conversation_store.py` — the `approvals` table and its accessors

New `CREATE TABLE IF NOT EXISTS approvals (...)` block appended to `_SCHEMA`
(`conversation_store.py:41-124`), after `conversation_accounts`, with
exactly the columns the work item names. `plugin_pauses` gains
`paused_kind TEXT` and `paused_value_json TEXT` via `_MIGRATED_COLUMNS`
(`conversation_store.py:317-321`) — nullable, no default, matching the
existing pattern's own reasoning ("no safe value to invent for a row older
than the column... reads back `NULL`... self-correct only once genuinely
rewritten," `conversation_store.py:283-289`): a `plugin_pauses` row written
before this item has no `paused_kind`, which `load_pause` reads back as
`kind="wait"` (every pre-H18 pause **was** a wait pause — nothing else could
park before this item existed) and `paused_value=None`.

`conversation_store.Pause` (`conversation_store.py:839-857`) gains
`kind: Literal["wait", "call"] = "wait"` and `paused_value: object = None`,
trailing and defaulted for the same legacy-row reason above.

`save_pause_from_result` (`conversation_store.py:901-928`) now also writes
the `approvals` row, in the same `write_txn` `save_pause` already opens:
`expires_at = requested_at + config.env_int("SADANA_APPROVALS_TTL_S", 86400)`,
`question = result.text` (the paused `DagResult`'s own generic sentence —
plugin- and node-named text, never re-derived or re-templated here; see
Concerns for why this, and not a richer prompt field, is what "untrusted"
refers to), `kind = result.paused_kind`, upserted by `(conversation_key)`
matching `plugin_pauses`' own one-outstanding-pause invariant — a second
pause on the same conversation overwrites its own prior *unanswered*
approvals row rather than leaving two, the same "at most one" shape
`PRIMARY KEY` already gives `plugin_pauses`. `id` is minted fresh
(`ids.make_id("appr")`) only on first insert of a given `conversation_key`'s
outstanding approval; the upsert's `ON CONFLICT` clause never rewrites `id`,
matching `conversations`' own identity-column asymmetry
(`conversation_store.py:452-461`) — an approval's `id` is stable across a
run's *own* re-pauses (there are none, today — a single `call`/`wait` node
either resolves or doesn't) but this keeps the shape consistent with every
other identity column in this file regardless.

New accessors: `load_waiting_approval(conn, *, conversation_key) ->
ApprovalRow | None` (the one row `client_surface.take_turn` needs to name in
its diagnostic when a call-parked conversation receives a text message, and
the one `door/nouns/approvals.py` needs for `search_doc`/`get`/`list`
without duplicating the SQL); `mark_approval_decided(c, *, conversation_key,
state, answered_by, answer, at)` (the function `resume_paused_run` calls,
described above — takes an already-open connection, exactly like
`_record_conversation_change`, never opens its own transaction).

### 5. `client_surface.take_turn` — the client's choice, and the stored-not-run branch

`take_turn` gains `approve: plugins.ApproveFn | None = None`, keyword-only,
appended after every existing parameter — no existing parameter's name,
order, or default moves (the amendment's own constraint, since the other
lane's only touch of this file is calling it). Resolved once, at the top of
the function body: `resolved_approve = approve if approve is not None else
plugin_manifest.parking_approve`. This is what makes every caller that
never heard of approval — `gateway_dispatch.handle_inbound`, and through it
`channel_webhook.py` and `scheduling.tick`'s own trigger firing — safe by
construction without editing either of those files: they call `take_turn`
with no `approve=` today and none is added, so they get `parking_approve`
for free. `resolved_approve` is threaded to both `build_dispatch(...,
approve=resolved_approve)` and, in the outstanding-pause branch, to
`resume_paused_run(..., approve=resolved_approve)` — one resolved value, two
uses, so a TTY conversation that resumes past a second `call` node deeper in
the same graph still gets the interactive prompt, and a non-interactive one
still never blocks.

The outstanding-pause branch (`client_surface.py:413-423` today) splits on
`pause.kind`:
- `"wait"`: unchanged in shape — `resume_paused_run(conn, conversation,
  decision="answer", payload=text, approve=resolved_approve)`, then append
  the resumed text as an assistant message, exactly as today.
- `"call"`: the incoming `text` is **not** a decision — a call pause is
  answered through the door's actions, not by texting the conversation.
  The message is appended to history as a `user` message (stored, not
  discarded — intent's own "stored and not run," and the only way a later
  reader of the transcript sees that someone tried to say something while a
  call sat parked) and no run happens: no `dispatch`, no `build_dispatch`,
  no model call. `conversation_store.load_waiting_approval` names the
  outstanding approval's `id` for the diagnostic:
  `TurnOutcome(ok=False, answer=None, diagnostic=f"[waiting_for_approval]
  {approval.id}")`.

### 6. `subcommands/chat.py` — one line

`_chat_loop`'s `client_surface.take_turn(...)` call
(`subcommands/chat.py:64-70`) gains
`approve=plugin_manifest._default_approve if sys.stdin.isatty() else None`.
Every other caller of `take_turn` in the tree today (only this one, plus
tests) is unaffected by the new keyword's default.

### 7. `door/nouns/approvals.py` — the six verbs, new file

Mirrors `tests/contract/fixture_noun.py`'s `WidgetsNoun` shape exactly
(the one real, non-stub six-verb noun in the tree today) but backed by
SQLite through `ctx.conns` instead of an in-memory dict:

```python
CAPABILITIES: tuple[str, ...] = ("approvals.wait", "approvals.call")

spec = NounSpec(
    plural="approvals",
    prefix="appr",
    filterable=frozenset({"state", "kind", "run_id"}),
    orderable=frozenset({"created_at", "requested_at"}),
    states=frozenset({"waiting", "approved", "declined", "answered", "expired"}),
    actions={
        "approve": ActionSpec(from_states=("waiting",), to_state="approved", capability=None, scope_verb="approve"),
        "decline": ActionSpec(from_states=("waiting",), to_state="declined", capability=None, scope_verb="decline"),
        "answer":  ActionSpec(from_states=("waiting",), to_state="answered", capability=None, scope_verb="answer"),
    },
    parent=None,
)
```

`capability=None` on all three actions: `approvals.wait`/`approvals.call`
gate the *existence of this noun's data at all* — `DECLARED` (Design §8) is
what turns parking on for `wait`/`call` respectively — not a per-action
toggle on top of the scope check `approve`/`decline`/`answer` already need.
Matches `harness.py`'s own `upgrade` action, which *does* name a
capability because upgrading is itself the gated behavior, not a
precondition already gated elsewhere.

`list`/`get` read straight off the `approvals` table via
`conversation_store` accessors, rendering the standard fields
(`docs/console/wire.md` §1) plus the fields the work item names (`run_id`,
`node_id`, `kind`, `question`, `requested_at`, `answered_by`, `answer`);
`create` and `update` return `unavailable(...)`
(`door/nouns/__init__.py:95-100`) — an approval is only ever created by a
paused run, never by a `POST`, and never edited except through its three
actions; `remove` likewise — nothing deletes an approval, `expired` is a
state, not an absence (CLAUDE.md's retention posture, `RETENTION-01`).

`act` dispatches on `name` (`"approve"`/`"decline"`/`"answer"`), checks the
row's own `kind` against `_REQUIRED_KIND[name]` (`"approve"`/`"decline"`
require `"call"`; `"answer"` requires `"wait"`) — a mismatch is `409
CONFLICT`, added during Deploy-stage cold review (see Acceptance criteria,
above) — then checks `if_match` against the row's own `version` with the
same `_check_if_match`-shaped helper `fixture_noun.py:151-157` demonstrates
(this item's own copy, not an import — `fixture_noun.py` is a test fixture,
never imported by production code), then calls
`plugin_dispatch.resume_paused_run` under `stores.conversation_lock(row.conversation_key)`
— the same lock `take_turn` itself takes for the whole of a turn, here held
only for the resume call, matching the work item's own "each action runs
resume_paused_run under the conversation's lock" instruction — with
`decision` and `state` both mapped from `name`, `payload` from
`body.get("reason")` (decline) or `body.get("answer")` (answer) or `None`
(approve), `answered_by= "console:" + principal.sub`, and
`approve=plugin_manifest.parking_approve` (a resumed walk that reaches a
*second* `call` node deeper in the same graph must not block the door's own
request thread any more than the first one did). Router.py's own state gate
(`router.py:264-275`) already answers `409 CONFLICT` for a non-`"waiting"`
row before `act` is ever called, so `act` itself does not re-check `state`
beyond the `if_match` race the router's read-then-dispatch shape can't close
on its own (`router.py:9-11`'s own comment on why `If-Match` is checked
inside the noun) — but `state` alone cannot tell a `call`-kind row from a
`wait`-kind one, since both sit in `"waiting"` identically, which is exactly
what the `kind` check above catches instead.

`search_doc(row)` returns `SearchDoc(title=row["question"][:80],
facets={"state": row["state"], "kind": row["kind"], "run": row["run_id"]})`
per the work item's own literal shape.

### 8. Registry, capabilities, `ensure_schemas`

- `door/capabilities.py`: `DECLARED` gains `"approvals.wait"` and
  `"approvals.call"` — both already present in `ALL`
  (`door/capabilities.py:16-27`), placed there by H19 in anticipation of
  this item by name. Two new lines appended; nothing reordered.
- The noun registry (`subcommands/door.py:93`, `nouns={"harness": harness}`
  today): one new import (`from sadana.door.nouns import approvals`) and one
  new dict entry, `"approvals": approvals`, inserted alphabetically — it
  sorts before `"harness"`, so it is the first entry, matching the
  amendment's "insertions... in alphabetical order" resolution rule.
- `stores.py` is **not** touched. `approvals` is not a table registered
  through `stores.ensure_schemas`'s per-module chain — it lives in
  `conversation_store.py`'s own `_SCHEMA`, exactly where `plugin_pauses`,
  `scheduled_triggers`, and `conversation_accounts` already live, all added
  by later work items without ever touching `stores.py`
  (`conversation_store.py:89-123`). `conversation_store.open_store()` is
  the one function that runs `_SCHEMA`, and every writer already calls it.
  This is a scope reduction the amendment's own shared-file table
  anticipates ("only if your artifact adds a table" through that specific
  mechanism) — this item's table does not.

### 9. `scheduling.py` — expiry on the tick

`tick()` (`scheduling.py:44-92`) gains one call,
`door.nouns.approvals.expire_due(runtime.connections, now)`, placed before
the existing due-triggers loop (expiry is not a trigger and does not touch
`scheduled_triggers`; ordering relative to trigger firing has no observable
interaction, so it goes first as the cheaper, simpler-to-reason-about
check). `expire_due` lives on the noun module because that module is
already the one place this table's state-machine transitions are written
once (guideline 3 — reusing an existing step rather than duplicating the
transition logic a second time in `scheduling.py` itself); `scheduling.py`
importing `door.nouns.approvals` is a new, one-directional dependency this
item introduces (see Concerns) but no cycle — nothing under `door/` imports
`scheduling.py`.

`expire_due(conns, now) -> int` reads every `approvals` row with
`state = 'waiting' AND expires_at <= now` off the reader connection, and for
each, calls `plugin_dispatch.resume_paused_run(conns.writer, row.conversation_key,
decision="decline", payload=None, answered_by="system:expiry",
approve=plugin_manifest.parking_approve)` — reusing the exact same marking
path `door/nouns/approvals.py`'s own `decline` action uses, so "expiry
declines" is enforced in one function, not duplicated. A run whose plugin no
longer resolves by the time it expires still gets its approvals row marked
(`resume_paused_run`'s own not-found branch, `plugin_dispatch.py:355-365`,
still reaches the `mark_approval_decided` call afterward — see Interface).
Failures are caught and logged per-row, never raised out of `expire_due`,
matching `tick()`'s own per-trigger posture (CLAUDE.md: "an
observability/recording write's failure is caught and logged" — applied
here to expiry's own per-row work, the same extension `scheduling.py`'s own
docstring already makes for trigger firing).

## Interface

**`plugin_manifest.parking_approve`** — `ApproveFn`-shaped, never truly
invoked when `_run_graph` recognises it by identity first; returns `False`
if it ever is. In/out/errors otherwise identical to any `ApproveFn`.

**`plugin_dispatch.resume_paused_run(conn, conversation, *, decision, state,
payload=None, answered_by=None, approve=plugin_manifest._default_approve) ->
plugins.DagResult`.** Updated from the plan-stage signature during
implementation (see `plan.md`'s own note on this): `state` — the
`Literal["approved", "declined", "answered", "expired"]` the conversation's
approvals row becomes — was originally going to be derived from `decision`
inside `resolve_pause`, with a `state_override` escape hatch for expiry
(`decision="decline"` but `state="expired"` — a person never answered,
which is a different fact than a person answering no). A `/simplify`
altitude pass on the finished diff found the derive-plus-override shape was
answering two questions (what the run does; what the row becomes) with one
implicit mapping and a carve-out, and that every caller already knows
`state` outright — so `state` became a second required keyword instead,
with no derivation left in `resolve_pause` at all. `decision` must still
match `pause.kind` (`"answer"` only for a `wait` pause; `"approve"`/
`"decline"` only for a `call` pause) — a mismatch is a caller error
(`assert`), matching this function's existing "trusts the caller already
checked" posture toward `pause is not None`; `door/nouns/approvals.act`
checks `kind` itself, ahead of this call, precisely so that assertion is
never reached from the door (see below). Every call — including the
not-found/no-longer-resolves branch — marks the conversation's approvals
row before returning, in the same transaction that clears or rewrites
`plugin_pauses`.

**`door/nouns/approvals.act`**, for `"approve"`/`"decline"`/`"answer"`: `In`
— `id`, action `name`, `body` (`{}`/`{"reason": str}`/`{"answer": str}`),
`if_match`. `Out` — the updated approval resource, or a `Problem`
(`404` unknown id, `409` wrong state — via router's own gate — `409` wrong
*kind* for the action — `approve`/`decline` require a `call`-kind row,
`answer` requires `wait`, checked by `act` itself before it ever calls
`resume_paused_run` — `412` stale or missing `If-Match`). The kind check is
a deploy-stage cold-review finding, not part of the original design: without
it, `approve` on a `wait`-kind row (or `answer` on a `call`-kind row) reached
`resume_paused_run`'s own `decision`/`kind` `assert`, which is not a
`Problem` — router.py's catch-all turned the resulting `AssertionError` into
an opaque `500 INTERNAL` instead of the `409` this contract promises. Per
`docs/console/wire.md` §1's own Actions contract, all three are
consequential (default `True`, left unset) and none declares a `capability`
(see Design §7).

**`scheduling.expire_due(conns, now) -> int`** (module: `door/nouns/approvals.py`)
— return value is rows expired, mirroring `tick()`'s own "how many fired"
convention (`scheduling.py:44`).

## Acceptance criteria

- [ ] A `call` node reached under `parking_approve` returns a paused
      `DagResult` (`paused_node`, `paused_kind="call"`, `paused_value=value`)
      without calling `approve`, and `save_pause_from_result` writes both
      the `plugin_pauses` row and a `waiting` `approvals` row in one
      transaction.
- [ ] `door/nouns/approvals.act(..., "approve", ...)` runs the parked call's
      body with `paused_value` and continues the walk to a terminal result;
      the `approvals` row moves to `approved` with `answered_by`/`decided_at`
      set, ledgered.
- [ ] `act(..., "decline", ...)` ends the walk at that node with
      `failed_node` set and trace `detail == "declined"`, without ever
      resolving or running the body; the `approvals` row moves to
      `declined`.
- [ ] A text message to a conversation parked on a `call` is appended to
      history as a stored `user` message and triggers no dispatch; the
      returned `TurnOutcome` has `ok=False` and
      `diagnostic == f"[waiting_for_approval] {approval_id}"`.
- [x] `act(..., "answer", ...)` on a conversation parked on `call` (state
      already not `"waiting"` for that action's `from_states`, or a `kind`
      mismatch) is `409 CONFLICT`. Corrected during Deploy-stage cold review:
      this was originally going to be "the router's own state gate," which
      is wrong — router.py's state gate checks only `state`, never `kind`,
      so a `call`-kind row that is still `"waiting"` sailed past it and
      reached `resume_paused_run`'s own internal `assert`, an uncaught
      `AssertionError` router.py's catch-all turned into `500 INTERNAL`.
      Fixed with `door/nouns/approvals._REQUIRED_KIND`, a second, noun-owned
      gate `act` runs itself before ever calling `resume_paused_run` — see
      Design §7 and Interface, above. Proven by
      `test_approve_on_a_wait_kind_row_is_409_not_a_crash` in
      `tests/contract/nouns/test_approvals.py`.
- [ ] `approvals.expire_due` moves every `waiting` row past `expires_at` to
      `expired`, resumes the run with `decision="decline"`,
      `answered_by="system:expiry"`, ledgered, and does not touch a row not
      yet past its `expires_at`.
- [ ] The TTY path (`sys.stdin.isatty()` true, faked via monkeypatching
      `sys.stdin`/`input`) still asks synchronously and blocks on an answer
      for a `call` node — `_default_approve` reached, `parking_approve`
      never reached.
- [ ] Each of the three actions is state-gated: attempting `approve` (or
      `decline`, or `answer`) on a non-`waiting` row is `409`, proven for
      at least one already-decided state per action.
- [ ] `GET /v1/approvals?filter=state%20%3D%20waiting&count=true` returns
      the badge shape (`"count"` present) against a store with a mix of
      waiting/decided rows.
- [ ] `tests/contract/nouns/test_approvals.py` proves the noun end to end
      against `router.handle()` directly, reusing `tests/contract/`'s
      `schema` fixture (imported, not copied) and its own local token-minting
      helper (not `test_console_grammar.py`'s private `_token`, per the
      amendment).
- [ ] `make verify` ends `VERIFY OK`.
- [ ] `scripts/prove_call_approval_e2e.py` parks a real `call` node through
      the webhook path (`gateway_dispatch.handle_inbound`, non-interactive,
      so `parking_approve` is genuinely exercised, not stubbed), approves it
      through a live `sadana door serve` process reached over real HTTP, and
      prints the `approvals` row before and after — Deploy-stage evidence,
      per CLAUDE.md's rule that a real external round trip is proven by a
      standalone script outside `make test`, never by relaxing the unit
      suite's network ban.

## Non-goals

- `ledger.inventory()` listing real `approvals` rows. `ledger._NOUNS`
  already reserves `"approvals"` with an empty `sources` tuple, naming this
  item by number in its own comment (`ledger.py:71`), but nothing in this
  work item's own acceptance criteria touches `GET /v1/inventory`, and
  populating `sources` would mean editing `ledger.py` — a file outside this
  item's four allowed shared-file edits and not one the user asked to add
  as a fifth. `changes_since`/the door's event feed already announce every
  approvals state transition via `ledger.record_change`, which is what a
  console mirror actually follows in steady state; inventory is the
  cold-start catch-up path, and a harness that has never taken a full
  inventory before an approval existed is exactly the "lost a change,
  resync" case `docs/console/wire.md` §3 already describes as recoverable
  by a later full inventory, whenever that lands.
- A per-account owner on an approval, or per-account visibility on
  `GET /v1/approvals`. The table carries no `account_key`; any principal
  holding `approvals:read`/`approvals:approve` etc. sees and acts on every
  approval on the box — matching this project's current single-operator,
  "`org` unset means any" posture (`docs/console/wire.md` §2) and avoiding
  inventing ownership semantics intent never asked for.
- Extending `door/grammar.py` with a per-noun default `order_by`. Resolved
  with the user directly (see Concerns): a bare `GET /v1/approvals` sorts
  `created_at desc`, the same universal default every other noun gets; a
  caller that wants `requested_at asc` states it explicitly.
- Any policy about what a decline or expiry means for the plugin that owned
  the node, beyond "the walk does not proceed past that node" — retries,
  notifications, escalation are all intent's own declared Constraints.
- `each` node parking, or any change to `ask`/`compute`/`route`/`stop` —
  none of the four ever call `approve` and none of them park; unchanged.

## Open questions

None. Every decision this section would otherwise raise was either already
settled by the work item's own dictated shape, or raised and resolved with
the user directly during this design pass (the `order_by` default, above).

## Rejected alternatives

**A `resume: ResumeKind` enum argument on `run_graph` instead of an identity
check on `approve`** — declined. `run_graph`'s signature already has one
seam for "how should a `call` node be gated" (`approve`), and a park is a
third *answer* to that same question ("yes"/"no"/"not now, ask elsewhere"),
not a second axis. A second parameter would let a caller pass a
contradictory pair (`approve=_default_approve, park=True`) that identity
dispatch makes structurally impossible — guideline 2, fewer bets, chose the
shape with fewer states to reason about, not the shape with fewer lines.

**A `Literal["wait", "call"]` field directly on `DagResult` reused from
`paused_node`'s own presence, instead of a new `paused_kind`** — declined.
`paused_node: str | None` already answers "did it pause"; conflating "which
kind of node" into a second meaning of the same field (e.g. `paused_node`
holding a `"call:"`-prefixed name) is exactly the "branches carry
meaningfully different data" shape CLAUDE.md's own outcome-typing rule
warns against collapsing into one field. One more optional field costs one
line per existing construction site (all of which already default it);
string-tagging an existing field costs a parser at every read site.

**Deriving `answered_by` for the TTY/webhook `"answer"` path from
`memory.owner_account()` instead of leaving it `None`** — declined for this
item. `memory.owner_account()` exists and is used elsewhere
(`scheduling.py:81`) for "whose voice a scheduled trigger runs as," but an
`answer` reached through a text message is not necessarily the box's own
owner (PERSONA-02 already lets more than one account hold conversations);
guessing an identity nobody stated is worse than an honest `NULL`, matching
this project's own "a value should hold another long-lived value's name,
never [a guess]" posture (CLAUDE.md). The door's own actions *do* know a
real acting account (`principal.sub`) and use it.

**Populating `ledger._NOUNS["approvals"].sources` now, since the item is
right here** — declined; see Non-goals. Named separately here because
guideline 2 (reduce the number of bets) argues directly against "while I'm
in the file" scope growth: every additional file this item touches that
isn't load-bearing for its own acceptance criteria is one more file the
parallel lane's merge has to reason about, for zero traceable requirement.

**Catching the `call`-parks-non-interactively scenario by making
`_default_approve` itself time out** (guideline 3: a heavier step, not a
new one) — declined. A timeout still leaves the *first* problem (a run
"stopped" only by an exception surfacing through `run_graph`'s own
catch-all, not a clean `paused_node` state) and answers nothing about
findability or resumability from outside the process — intent's actual
Proposed outcome. A heavier existing step was cheaper to write but did not
catch the scenario; the identity-dispatched park is a new step precisely
because no existing one could be made to cover it.

## Concerns

**The `order_by` default gap, named and resolved with the user.**
`door/grammar.py:parse_list_params` hardcodes `"created_at desc"` as the
default when a client's `order_by` is omitted, validated against the noun's
own `orderable` set with no per-noun override hook. The work item's own
line — "default order requested_at asc" — is therefore not literally true
of a bare `GET /v1/approvals`; it is true of one that states
`order_by=requested_at asc`. Extending `grammar.py` would fix this exactly,
at the cost of a fifth shared-file edit the amendment did not anticipate,
during a build running in parallel with another lane touching the same
framework area. Put to the user directly; the answer was to accept the
universal default rather than extend the framework mid-parallel-build. This
is the single clearest policy tension in this design — `docs/console/wire.md`
§1's own "Standard fields... `created_at`... on every resource" versus this
item's own stated UX — and it is resolved in the wire contract's favor.

**`scheduling.py` importing `door.nouns.approvals`.** `scheduling.py` today
imports only `client_surface`, `conversation_store`, `gateway_dispatch`,
`memory` — core turn-taking modules, nothing under `door/`. This item adds
the first dependency from the scheduler onto a door noun module, because
`expire_due`'s transition logic is genuinely the same logic `act`'s
`decline` path already has to have, and CLAUDE.md's own rule on registries
("earns its cost only once a second real member exists") argues just as
hard against a *second* copy of one state machine's transition function as
it does against a premature seam. No import cycle results (nothing under
`door/` imports `scheduling.py`), but it is a new direction of coupling
worth a reviewer's attention rather than a silent addition.

**`question = result.text`, and what "untrusted" means here.** The work
item marks `question` untrusted. This design reads that as: the text
originates from a plugin author's own node/plugin naming (external,
uncontrolled by this codebase) and must never be replayed as prompt or
system text to a model — exactly CLAUDE.md's existing rule ("Text a
plugin fetched from outside reaches the model only as a tool result, never
as system-prompt or skill text"), applied here to a *door response field*
rather than a tool result. `door/nouns/approvals.py` never does anything
with `question` but hand it back as JSON string data, so this is a
documentation concern, not a behavioral gap — named because it is the kind
of thing a later item (a console UI that renders `question` as literal HTML,
say) could get wrong without this note.

**Policy conformance, named explicitly.** `testing-conventions` applies and
is followed: `scripts/prove_call_approval_e2e.py` is the standalone,
real-round-trip proof the skill requires to live outside `make test`, never
as a relaxation of the unit suite's network ban; the unit and contract
suites touch no real stdin, clock, or network (the TTY test monkeypatches
`sys.stdin`/`input`; `expire_due`'s tests inject `now` rather than reading
the wall clock). No `project-structure` skill exists in this repository
(the same finding `F1-call-node-approval/spec.md`'s own Concerns already
made); CLAUDE.md's own file-placement rules (a module touching real I/O
gets its own file; a noun module lives under `door/nouns/`; a test file
per noun under `tests/contract/nouns/`) are this project's actual
project-structure policy and are followed above. No other policy conflict
found beyond the two named here.

**No new CLAUDE.md rule proposed.** The `parking_approve`-by-identity
mechanism is a one-seam pattern local to `run_graph`'s own `approve`
parameter, not a shape this codebase is likely to need a second instance of
before a second pluggable-callable seam exists to need it — guideline 2's
own caveat ("do not invent... early... speculative infrastructure with no
consumer") argues against codifying it as a general rule from one instance.
