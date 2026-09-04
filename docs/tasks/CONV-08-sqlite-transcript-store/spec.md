# Spec: A conversation survives the process that was running it

Intent: docs/tasks/CONV-08-sqlite-transcript-store/intent.md

## Requirements

1. A whole `Conversation` value (key, template name, prompt + hash + epoch,
   tool surface, message history, turn/child sequence counters, iteration
   budget, wall-clock budget) can be durably saved and later loaded back by
   its own `key`, resuming with every one of those fields intact. (Intent
   §Proposed outcome, §Affected users and systems.)
2. Starting a brand-new conversation under a `key` that already has a saved
   row fails loudly rather than silently replacing what was there.
   Re-saving an already-owned conversation under its own unchanged `key`,
   as it makes progress, always succeeds — that is the normal way a
   conversation's record advances. These are two different operations, not
   one. (Intent §Proposed outcome; design-stage clarification — see
   §Concerns.)
3. Loading a `key` nobody ever saved is a clear, typed failure. It never
   returns an empty or default conversation standing in for a real one.
   (Intent §Proposed outcome; confirmed in the design interview.)
4. A save either lands completely or the caller finds out it failed; there
   is no state in which a saved row is half-written. (Intent §Proposed
   outcome, §Constraints — "ACID.")
5. The remaining wall-clock allotment is made durable as a portable amount
   of time left, not as the raw deadline it is normally checked against —
   because that deadline is measured on a clock that resets to zero on
   every process restart. (Intent §Constraints, §Changed during planning.)
6. What a conversation is allowed to do and its own instructions (its
   `ConversationTemplate`) are not made durable by this work item. Nothing
   in this store reconstructs one. (Intent §Constraints.) In practice this
   binds nothing new: `take_turn()` (`conversation.py:1024`) already needs
   only a `Conversation` value to resume a conversation's next turn, never
   a `ConversationTemplate` — see §Design.
7. Looking a conversation up happens by its exact `key` only. This work
   item adds no listing, filtering, or search operation. (Intent
   §Constraints.)
8. The existing turn loop's `persist` seam (`conversation.py:708`,
   `run_turn`'s `persist: Callable[[tuple[Message, ...]], Awaitable[None]]`
   parameter, defaulted today to `_noop_persist` at `conversation.py:615`
   with a docstring naming this exact work item) gets a real implementation
   backed by this store, so that a tool handler genuinely cannot run before
   the assistant message asking for it is durably written down. (Intent
   §Proposed outcome, §Affected users and systems.)
9. This work item does not change `run_turn`, `take_turn`, or any other
   part of the turn loop's control flow. It fills in a seam that already
   exists. (Intent §Constraints.)

## Design

A new module owns the store; nothing persisted needs deriving from a
template on load, because `Conversation` already holds nothing that isn't
plain data; the schema is two tables — one row per conversation, one row
per message, each carrying its own natural-key constraint, per blueprint
§4.1/§7 — and the transaction pattern, schema shape, and declined
machinery below are audited against hermes's own `SESSION-STORE` block
rather than invented from scratch.

### Where this lives

A new module, `src/sadana/conversation_store.py`, not another section of
`src/sadana/conversation.py`. Two reasons, not one:

- `conversation.py` is already 1309 lines across five prior work items and
  past this project's own ~400-line-per-file guidance (noted, unaddressed,
  in prior work items' own trace sections). This is confirmed with the
  user rather than assumed — see §Concerns.
- Every function in `conversation.py` today is pure: it takes values,
  returns values, touches no file, no clock, no network. This work item is
  the first in the block to need real I/O (`sqlite3`, a path on disk). That
  is a different kind of dependency, not just more lines, and a strong
  reason on its own to draw the module boundary here regardless of file
  length.

`conversation_store.py` imports `Conversation`, `Message`, `ConversationKey`,
`IterationBudget`, `WallClockBudget`, `ExitReason`-adjacent types, etc. from
`conversation.py`. Nothing in `conversation.py` imports from
`conversation_store.py` — the dependency runs one way, so `conversation.py`
never needs to know durability exists (guideline 2: this is growth as an
addition, not a modification of anything closed).

### What actually needs deriving, versus what needs storing

Before choosing a schema, it matters what `Conversation` (`conversation.py:942`)
actually contains, because the blueprint's original sketch (written before
any of this code existed) assumed `ToolSurface` held live `Callable`
handlers and would need to be excluded and re-derived from a template on
every load. That turned out not to match what got built:

- `ToolSurface = tuple[dict, ...]` (`conversation.py:158`) — plain,
  already-rendered, provider-format tool definitions. `ToolSpec.describe`
  (`conversation.py:176`, a `Callable`) is discarded inside `build_surface()`
  the moment it runs (`conversation.py:190-193` docstring: "not retained
  past this call"). By the time a `Conversation` exists, nothing
  unpicklable is reachable from it.
- Every other field on `Conversation` — `key: str`, `template_name: str`,
  `system_prompt: str`, `prompt_sha256: str`, `prompt_epoch: int`,
  `messages: tuple[Message, ...]` (itself `role`/`content`/`tool_calls:
  tuple[dict, ...]`/`tool_call_id`, all plain data), `next_turn_seq: int`,
  `iteration_budget: IterationBudget` (`max_total`, `used`, two ints),
  `wall_clock_budget: WallClockBudget | None` (one float), `next_child_seq:
  int` — is already plain data.

So there is nothing to derive on load. The whole `Conversation` is the
value this store saves and loads, verbatim, field for field. `take_turn()`
(`conversation.py:1024-1070`) takes a `Conversation` and needs nothing else
to run the next turn — no `ConversationTemplate`, no re-rendering step.
Requirement 6 ("the template is not made durable") is therefore free: there
was never anything template-shaped left inside `Conversation` to persist in
the first place. A caller only needs the template again if it wants to
`create_conversation()` a **new** starting point or apply
`defer_invalidation()` to one — neither of which this store's `load()` is
involved in.

The one field needing translation, not verbatim storage, is
`wall_clock_budget.deadline` (requirement 5): it is measured on
`time.monotonic()` (`conversation.py:280-285`'s own docstring), which is
meaningless across a process restart. `save()` converts it to
`wall_clock_remaining(budget, now)` (`conversation.py:290`, already a pure
function taking `now` as data, no clock read inside this module either);
`load()` reconstructs `WallClockBudget(deadline=now + remaining_seconds)`.
Both `save()` and `load()` take `now: float` as an explicit parameter for
exactly this reason — the same contract `wall_clock_budget_from_config(now)`
(`conversation.py:298`) already established, and the one this project's
testing conventions require (a test must not depend on the real wall
clock).

### Reference corpus: hermes's `SESSION-STORE` block

hermes's own sqlite-backed session persistence
(`docs/reference/hermes_core_blocks_kind.csv`, block `SESSION-STORE`, tier
1) is a ~30-file block covering far more than this work item needs — full
text search, export, checkpoints, foreign-session import, a kanban board.
Consulted narrowly, for the persistence primitive only, not the whole
block:

**Adopted:**
- The bare transaction shape from `hermes_cli/sqlite_util.py:31-49`'s
  `write_txn()` — a context manager doing `BEGIN IMMEDIATE` → yield the
  connection → `COMMIT`, with a guarded `ROLLBACK` in the `except` clause
  (guarded because SQLite may have already rolled back on its own under
  lock contention, and a second `ROLLBACK` raising would shadow the real
  error). This is the right-sized version of the same pattern hermes's
  much larger `hermes_state.py:5675-5744` `_execute_write()` implements —
  that larger version adds jitter retry-on-locked and WAL checkpoint
  bookkeeping on top, solving concurrent multi-process writer contention
  (hermes names 6+ concurrent openers of one `state.db`: gateway,
  dispatcher, dashboard, TUI, cron, kanban workers). sadana-harness has one
  process and one caller of this store; adopt the bare transaction,
  decline the concurrency machinery built on top of it.
- A `PRIMARY KEY` directly on the natural-key column
  (`hermes_state_common.py:395-454`'s `sessions.id TEXT PRIMARY KEY`, and
  `hermes_state_common.py:505-508`'s `state_meta(key TEXT PRIMARY KEY,
  value TEXT)` — the closer analog, a plain key→blob table). Adopted as
  the schema shape below.

**Declined, each with a stated reason:**
- Per-message-**incremental-append** storage (`append_message()` at
  `hermes_state.py:11526`, one `INSERT` per message as it happens, no
  whole-conversation write ever). Declined: this work item's caller hands
  over a whole immutable `Conversation` snapshot on every save (intent.md's
  own framing — "saved as it goes" means the whole value moves forward
  together), not one message at a time with nothing else known. A
  normalized `messages` **table** is still adopted (see §Schema below) —
  what's declined is hermes's incremental single-row-at-a-time write
  pattern, not the table shape itself.
- Missing-key returning `None` (`hermes_state.py:9976-9992`'s
  `get_session()`). Requirement 3 already decided the opposite — a clear
  exception — before this research ran; noting the divergence rather than
  silently drifting toward hermes's convention.
- Retry-on-locked jitter, WAL checkpoint cadence, and the WAL/journal-mode
  fallback matrix for NFS/SMB filesystems and older SQLite builds
  (`hermes_state.py:5680-5720`, `:1338-1456`). All solve multi-process
  writer contention or cross-platform filesystem quirks. Neither exists
  here: this project is WSL-only (CLAUDE.md) and single-process by every
  design decision made in this block so far (C7's loop, C8's aggregate —
  neither has ever considered two callers of one `Conversation`).
- `hermes_cli/sqlite_safe_read.py`'s path-keyed live-connection registry,
  which guards against one process's `close()` on an unrelated file
  descriptor silently cancelling a live `sqlite3.Connection`'s advisory
  lock on the same path — a hazard that only exists with multiple
  connections (often multiple processes) open on one file at once.
- `hermes_state_schema.py`'s `ALTER TABLE`-based migration/reconciliation
  across 26 schema versions, and `hermes_cli/sqlite_runtime.py`'s
  cross-interpreter SQLite version probe (an installer diagnostic). This
  work item ships one fixed schema with no migration history yet.
- `hermes_cli/session_recovery.py` and `session_lost_and_found.py` —
  offline, operator-invoked recovery from a physically damaged `.db` file
  (bad header, unreadable schema page). A different failure class from
  requirement 8's concern (was `persist()` called and did it commit before
  the next tool handler ran) — that concern is answered by transaction
  atomicity (adopted above), not file-level disaster recovery.

### Schema

Two tables, not one — corrected from an earlier draft of this section that
used a single key→JSON-blob table for the whole `Conversation` including
its messages. Re-reading the blueprint against an explicit instruction to
conform to it as closely as possible surfaced a mismatch that reasoning
about the in-memory invariants alone had missed: blueprint §7 item 8 names
this work item "unique constraint**s** on the natural key**s**" (plural),
and blueprint §4.1 defines `MessageKey = (ConversationKey, msg_seq: int)`
as a natural key in its own right, "checkable by scanning a range" — a
literal per-row SQL range scan, which a single opaque blob cannot support
regardless of what invariants already hold in memory before a value
reaches the store. `append()`/`repair()` (`conversation.py:94-144`) still
make a malformed sequence impossible before anything is ever saved — that
reasoning was correct on its own terms — but the blueprint's structural
intent for *this* block was for the store itself to carry the
`MessageKey` constraint too, independent of whether the in-memory path
that produced the value already enforced it:

```sql
CREATE TABLE IF NOT EXISTS conversations (
    key                    TEXT PRIMARY KEY,
    template_name          TEXT NOT NULL,
    system_prompt          TEXT NOT NULL,
    prompt_sha256          TEXT NOT NULL,
    prompt_epoch           INTEGER NOT NULL,
    tool_surface_json      TEXT NOT NULL,
    next_turn_seq          INTEGER NOT NULL,
    iteration_max_total    INTEGER NOT NULL,
    iteration_used         INTEGER NOT NULL,
    wall_clock_remaining_s REAL,              -- NULL: no wall-clock budget
    next_child_seq         INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS messages (
    conversation_key  TEXT NOT NULL REFERENCES conversations(key),
    msg_seq           INTEGER NOT NULL,
    role              TEXT NOT NULL,
    content           TEXT,
    tool_calls_json   TEXT NOT NULL,          -- '[]' when empty
    tool_call_id      TEXT,
    PRIMARY KEY (conversation_key, msg_seq)
);
```

`PRIMARY KEY (conversation_key, msg_seq)` is the literal `MessageKey`
constraint blueprint §4.1 names. `SELECT msg_seq FROM messages WHERE
conversation_key = ? ORDER BY msg_seq` now makes "gap-free, checkable by
scanning a range" true at the database level, independent of the Python
process that wrote it.

`create()`/`save()` write both tables inside one `write_txn` — the
`conversations` row (insert-only for `create`, upsert for `save`), then
one `INSERT OR IGNORE` per message in `conversation.messages`. `OR
IGNORE`, not `OR REPLACE`: a message row is never edited once written —
`append()`'s own invariant is that history only grows — so silently
skipping a `msg_seq` that's already present is the correct idempotent
behaviour for `bind_persist()` re-saving the same growing tuple every
tool round; `REPLACE` would instead accept silently rewriting a historical
row if a bug ever produced a seq collision with different content, which
`IGNORE` cannot do. `load()` does one `SELECT` on `conversations` (raising
`ConversationNotFound` if absent) and one ordered `SELECT` on `messages`,
reassembling `Conversation.messages` in `msg_seq` order.

`Conversation`'s remaining fields (`system_prompt`, `tool_surface`, the
two budgets, the sequence counters) still have nowhere else meaningful to
live but the `conversations` row's own columns — none of them carry a
natural key of their own the way a message does, so normalizing them
further would only be extra columns with no independent identity,
contradicting the state-minimisation guideline for no benefit. That part
of the original reasoning stands; only the treatment of `messages` was
wrong.

`PRAGMA journal_mode=WAL` is set once per connection (one line). Adopted
on its own merits — better crash-safety and non-blocking reads than the
default rollback-journal mode, standard advice for any application-shaped
SQLite use today — not as part of hermes's fallback matrix: there is no
NFS/SMB/old-SQLite-version detection here, because WSL-only means the
filesystem and SQLite version are fixed and known.

## Interface

```python
class ConversationAlreadyExists(Exception):
    """create() called with a key that already has a saved row."""

class ConversationNotFound(Exception):
    """load() called with a key nobody has ever saved."""

def store_path_from_config() -> Path:
    """SADANA_CONVERSATION_STORE_PATH, default
    config.get_paths().state_dir / "conversations.sqlite3"."""

def open_store(path: Path) -> sqlite3.Connection:
    """Opens (creating the file and the table if needed), sets
    journal_mode=WAL. One connection per caller; nothing here pools or
    shares connections across callers."""

def create(conn: sqlite3.Connection, conversation: Conversation, *, now: float) -> None:
    """INSERTs one row. Raises ConversationAlreadyExists on a primary-key
    collision — requirement 2's first half."""

def save(
    conn: sqlite3.Connection, conversation: Conversation, *, now: float, start_seq: int = 0
) -> None:
    """INSERT ... ON CONFLICT(key) DO UPDATE. Always succeeds for a key the
    caller already owns (already created or already loaded) — requirement
    2's second half. `now` is time.monotonic(), used only to convert
    wall_clock_budget.deadline to a portable remaining-seconds value before
    it is written. `start_seq` (default 0, correct for any caller with no
    memory of what it last saved): only `conversation.messages[start_seq:]`
    is (re-)inserted, `INSERT OR IGNORE` still making it idempotent either
    way. Added post-design during the build-stage self-check, once
    `bind_persist()` re-sending the whole growing history every tool round
    turned out to be the O(messages²)-per-turn cost §Concerns should have
    named up front — `bind_persist()` passes its own running offset so a
    turn's repeated persist calls cost O(messages) in total, not
    O(messages²). This is real mutable state (one `int`, held in
    `bind_persist()`'s closure via `nonlocal`) — see §Concerns for why it's
    accepted rather than reversed, and its one known limitation."""

def load(conn: sqlite3.Connection, key: ConversationKey, *, now: float) -> Conversation:
    """Raises ConversationNotFound if key has no row. `now` is
    time.monotonic() in the resuming process, used to turn the stored
    remaining-seconds figure back into a fresh WallClockBudget.deadline."""

def bind_persist(
    conn: sqlite3.Connection, conversation: Conversation, *, now: float
) -> Callable[[tuple[Message, ...]], Awaitable[None]]:
    """Returns an async function matching run_turn()'s `persist` parameter
    exactly. Each call re-saves `conversation` with `messages` replaced by
    the growing history the loop hands it, via asyncio.to_thread (same
    wrapping precedent as C6's complete(), conversation.py:501, over a
    sync client) — requirement 8. See §Concerns for what this callback's
    existing shape does and does not cover."""
```

`create`/`save`/`load` are plain synchronous functions — sqlite3 is
already sync and there is no reason to fake async around it (only
`bind_persist`'s *return value* needs to be awaitable, to satisfy an
interface `run_turn` already fixed).

### Config

One new key, following the block's existing `SADANA_CONVERSATION_*`
naming:

```
SADANA_CONVERSATION_STORE_PATH   default: <state_dir>/conversations.sqlite3
```

Read once via `config.env_path`, matching `config.py`'s existing
primitives — no new config mechanism.

## Acceptance criteria

- [ ] `create()` on a fresh key succeeds; `create()` again on the same key
      raises `ConversationAlreadyExists` and leaves the original row
      unchanged.
- [ ] `save()` on a key the caller already created succeeds repeatedly,
      each call replacing the row, never raising for that reason alone.
- [ ] `load()` on a key that was `create()`d or `save()`d returns a
      `Conversation` equal, field for field, to what was last saved —
      including `messages`, `iteration_budget`, and `tool_surface`.
- [ ] `load()` on a key nobody ever saved raises `ConversationNotFound`.
- [ ] A `WallClockBudget` saved with N seconds remaining and loaded back
      later (a different `now`) has a `deadline` that is `load_now + N`,
      not the original absolute deadline.
- [ ] A save interrupted partway (forced failure between `BEGIN` and
      `COMMIT`) leaves both the `conversations` row and every `messages`
      row exactly as they were before the save started — never a
      half-applied write across the two tables.
- [ ] Every message in a saved `Conversation` has exactly one `messages`
      row keyed by `(conversation_key, msg_seq)`; re-saving the same
      conversation (a `msg_seq` already present) is a no-op for that row,
      never an error and never a duplicate.
- [ ] `bind_persist()`'s returned callable, wired as `run_turn`'s `persist`
      argument against a real sqlite-backed store, reproduces
      `test_run_turn_persistence_failed`'s existing assertion
      (`tests/unit/test_conversation.py:733`) with a *real* failure (e.g. a
      closed connection) instead of a fake raising stub: `exit_reason ==
      ExitReason.PERSISTENCE_FAILED` and the tool handler was never
      called.
- [ ] `make verify` ends `VERIFY OK`.

## Non-goals

- Listing, searching, or filtering stored conversations (requirement 7).
- Persisting a `ConversationTemplate`/`TemplateRecipe` or anything a
  plugin/marketplace system will eventually own (requirement 6).
- Concurrent access from more than one process, or more than one
  in-process caller, to the same conversation. Nothing in this block has
  needed that yet (C7's loop and C8's aggregate are both single-caller by
  construction); adding protection for a scenario with no caller would be
  exactly the kind of speculative infrastructure guideline 2 warns against.
- Iteration-level (as opposed to turn-level) budget durability — see
  §Concerns.
- Changing `run_turn`, `take_turn`, or any other part of the turn loop
  (requirement 9).

## Rejected alternatives

- **A single key→JSON-blob table holding the whole `Conversation`,
  messages included** — this section's own first draft. Reasoned from
  guideline 3 alone ("least step-cost": `append()`/`repair()`
  (`conversation.py:94-144`) already make a malformed message sequence
  impossible before a value ever reaches this store, so a second, DB-level
  check would only re-catch an already-caught scenario). That reasoning
  is not wrong about the *behavioural* guarantee, but it answered the
  wrong question: blueprint §7 item 8 and §4.1 ask for the store itself to
  carry a `MessageKey` constraint and a range-scannable sequence,
  independent of whatever the in-memory path already guarantees —
  structural conformance to the named block, not just non-duplication of
  an already-caught scenario. Reversed once re-read against an explicit
  instruction to conform to the blueprint as closely as possible; see
  §Schema for the adopted two-table design.
- **One `save()` that always upserts, no separate `create()`.** Considered
  because it is fewer functions. Rejected because it would silently drop
  requirement 2's collision protection — two unrelated conversations
  minting the same key by mistake would clobber each other with no
  signal. Two functions, one INSERT-only and one upsert, is the smaller
  bet: it costs one extra function, not a class of bug with no way to
  detect it later.
- **Deriving `tool_surface` from a re-supplied template on every load**,
  as the blueprint's original §4.4/§5.4 sketch assumed a store would need
  to. Declined once reading the actual code showed `ToolSurface` holds no
  unpicklable state (see §Design) — reintroducing that dependency would
  add a required argument to `load()` (the template) for a problem that
  does not exist in the code as built, purely to match a document written
  before the code was.
- **WAL-mode fallback detection** (NFS/SMB, old-SQLite-version gating), as
  hermes does. Declined per §Design: WSL-only means the filesystem and
  SQLite version are fixed; a fallback path with no reachable trigger is
  dead code the moment it ships.

## Open questions

None. Every fork the interview or this design surfaced was resolved with
the user directly (create-vs-save semantics, module placement, wall-clock
portability, unknown-key behaviour) rather than left open.

## Concerns

- **The existing `persist` seam only ever carries `messages`, never the
  whole `Conversation`** (`run_turn`'s signature, `conversation.py:692-708`,
  fixed by C7 and out of reach here per requirement 9). It fires once per
  tool round *inside* a turn, but `iteration_budget` only settles into the
  `Conversation` value at the very end of `take_turn()`
  (`conversation.py:1064-1069`), after the seam has already done its job
  for that turn. Consequence: if the process crashes mid-turn, every
  `persist()` call that already succeeded durably protects the message
  history (requirement 8's actual guarantee — a handler never runs before
  its ask is recorded), but the *iteration count* consumed by that
  in-progress turn is not separately durable, and a resumed conversation's
  budget reflects its state as of the last **completed** turn, not the
  last **tool round**. This is not a bug this work item can close: C7's
  seam shape is already closed and requirement 9 forbids reopening it.
  Recorded here because it means requirement 1 ("resuming from exactly
  where it left off") is true at turn granularity, not iteration
  granularity — a real, narrower guarantee than the intent's own prose
  might suggest read in isolation, and worth a reviewer's attention rather
  than a silent gap.
- **Single-process, single-writer is an assumption, not something the
  user was asked to confirm in these exact words.** It follows from every
  design decision this block has made so far (C7, C8 — see §Non-goals) and
  from CLAUDE.md's "Isolate by process" rule, but if that ever changes,
  this store's transaction shape (adopted from hermes's own *small-store*
  precedent, not its multi-writer one) would need revisiting alongside
  it — flagged so a future reader knows where that seam is, not because
  it is unresolved today.
- **`save()`'s `start_seq` parameter and `bind_persist()`'s `flushed`
  counter are real mutable state added after this document's first
  draft** (deploy-stage review's Important finding — added during the
  build-stage self-check, once the design's original always-re-insert
  behaviour turned out to be O(messages²) of total I/O per turn; the
  offset itself is what guideline 5 asks to justify, not just note). It's
  accepted, not reversed, because the alternative (no offset, and letting
  `_insert_messages`'s `INSERT OR IGNORE` do the same idempotency work by
  re-attempting every already-durable row every call) is a real cost that
  grows with a turn's message count for zero behavioural difference — a
  `nonlocal int` local to one closure is a small, contained piece of state
  next to that. Its one real limitation: `flushed` only tracks progress
  for the lifetime of one `bind_persist()` closure. Nothing wires
  `bind_persist()` into `take_turn()`/`run_child()` yet (requirement 9
  keeps this work item from doing so), so the natural future call pattern
  — one fresh `bind_persist()` per turn, or resuming a loaded conversation
  with N already-durable messages — resets `flushed` to 0 each time; the
  O(messages), not O(messages²), guarantee holds only within one closure's
  own repeated calls, not across turns. `INSERT OR IGNORE` remains the
  actual correctness backstop in every case; `start_seq` is purely an
  efficiency knob on top of it, never required for correctness.
- No policy skill named `project-structure` or `reference-lookup` exists
  in `.claude/skills/` for this project (only `plan-skill`, `design-skill`,
  `build-skill`, `deploy-skill`, `testing-conventions`). CLAUDE.md's own
  "Layout and ownership" section and "methodology for learning from the
  reference" section stood in for them here; `testing-conventions` was
  loaded and applied directly (§Acceptance criteria's crash-safety test,
  and the module's tests living in `tests/unit/test_conversation_store.py`
  under the existing `_isolated_state` autouse fixture, which already
  redirects `SADANA_STATE_DIR` — this store's default path base — to a
  tmp directory with no changes needed).
