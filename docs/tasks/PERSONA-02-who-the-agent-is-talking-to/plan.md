# Plan: Who the agent is talking to (from intent.md 2026-09-11)

## Files that change

    CLAUDE.md                                    the channel-envelope rule, approved at design (already in the tree)
    src/sadana/memory.py                         (new) `owner_account()`
    src/sadana/memory_store.py                   `adopt_scheduled_memories()`, `accounts_with_memories()`
    src/sadana/conversation_store.py             `conversation_accounts` table; `create()` takes `account_key`; `accounts_with_conversations()`
    src/sadana/persona_store.py                  `known_accounts()`, `account_exists()`
    src/sadana/stores.py                         (new) `ensure_schemas()` and `open_cli_store()` — step 6's amendment
    src/sadana/gateway_dispatch.py               keyword-only `account` override on `handle_inbound()`
    src/sadana/scheduling.py                     `tick()` states `memory.owner_account()`
    src/sadana/client_surface.py                 `_create()` passes the account; `open_runtime()` runs the adoption
    src/sadana/subcommands/persona.py            `accounts` verb; the refusal in `use`
    src/sadana/subcommands/chat.py               its inline account default becomes `memory.owner_account()`
    src/sadana/subcommands/memory.py             its own store scaffold becomes `stores.open_cli_store()`
    tests/unit/test_memory.py                    owner resolution: set, unset
    tests/unit/test_memory_store.py              adoption: moves, collides, is idempotent; `accounts_with_memories`
    tests/unit/test_conversation_store.py        the account row, a legacy conversation without one, ~17 `create()` call sites gain the keyword
    tests/unit/test_persona_store.py             `known_accounts` unions three sources; `account_exists`
    tests/unit/test_scheduling.py                a fired trigger runs as the owner, in the owner's voice; its fake `handle_inbound` gains the keyword
    tests/unit/test_gateway_dispatch.py          a webhook still derives its own account; an override reaches the door
    tests/unit/test_subcommands_persona.py       `accounts` output; the refusal; the owner exception
    tests/unit/test_client_surface.py            `create()` call site at :185
    tests/unit/test_subcommands_chat.py          the account default still resolves the same way
    tests/unit/test_subcommands_conversations.py its `create()` call site gains the keyword
    tests/conftest.py                            `make_runtime` ensures every schema through `stores`

Confirmed by reading, not assumed: `conversation_store.create()` has exactly
one production caller (`client_surface.py:250`) and no caller anywhere under
`scripts/` — checked explicitly, because PERSONA-01's own cold review found
four broken `scripts/` call sites a `src/`-and-`tests/` grep had missed.
`handle_inbound()` gains a defaulted keyword, so its four `scripts/` callers
keep working untouched.

## Order of work

Each step leaves the suite green. The two steps that change live behaviour —
the startup migration and who a scheduled run is — come after the functions
they depend on are proven by their own tests.

1. **The owner has a name.** `memory.owner_account()` reading
   `SADANA_MEMORY_ACCOUNT` with `"local"` as the default, beside
   `account_key_for()`. `subcommands/chat.py:85`'s inline
   `config.env("SADANA_MEMORY_ACCOUNT", "local")` becomes the call — same
   variable, same default, so no behaviour changes here at all; this step only
   moves the definition to where three other callers can reach it.
   `tests/unit/test_memory.py` covers set and unset.

2. **A conversation records whose it is.** `conversation_accounts`
   (`conversation_key TEXT PRIMARY KEY`, `account_key TEXT NOT NULL`) added to
   `conversation_store._SCHEMA` — a new table, so no `_MIGRATED_COLUMNS` entry
   and nothing touches the `conversations` row shape or `save()`'s upsert.
   `create()` gains a required keyword-only `account_key`, writing both rows
   inside the `write_txn` it already opens. `accounts_with_conversations(conn)`
   returns the distinct set. `client_surface._create()` passes its own
   `account`. Every `create()` call site in the tests gains the keyword.

3. **The adoption exists but is not wired.**
   `memory_store.adopt_scheduled_memories(conn, owner) -> int` re-keys every
   `memory_entries` row whose `account_key` starts with `schedule:`, and on a
   `(owner, entry_key)` collision keeps the incoming row under
   `<entry_key>--<source account>` instead of overwriting. Returns the count.
   `accounts_with_memories(conn)` returns the distinct set.
   `tests/unit/test_memory_store.py` proves the move, the collision keeping
   both texts, that a second call moves nothing, and that a `schedule:` rubric
   override is left alone.

4. **Turn the adoption on.** `open_runtime()` calls it once, beside the two
   `ensure_schema` calls, wrapped so a failure logs and does not stop the
   process starting (spec.md § Concerns' resolved policy conflict).

5. **A scheduled run is the owner.** `handle_inbound()` gains keyword-only
   `account: AccountKey | None = None`, `None` deriving `platform:chat_id`
   exactly as today; `scheduling.tick()` passes `memory.owner_account()`.
   `test_scheduling.py`'s fake `handle_inbound` gains the keyword and asserts
   the value. `test_gateway_dispatch.py` gains two cases: a webhook event with
   no override still runs under `webhook:<chat_id>`, and an inbound payload
   carrying an `account` key changes nothing — CLAUDE.md's new rule, as a
   test rather than a promise.

6. **Look them up, and refuse the ones that do not exist.**
   `persona_store.known_accounts(conn)` (sorted union of the three sources)
   and `account_exists(conn, account)`. `subcommands/persona.py` gains the
   `accounts` verb, printing each account with an owner marker and its
   selected character; `cmd_persona_use` refuses an account that is neither
   known nor the owner, exiting 1 before it opens a write connection — the
   same order PERSONA-01 already established for an unknown character name.

7. **Self-check, then verify.** `/ponytail-review` and `/simplify` over the
   diff, sort the findings, apply the take-now bucket. Then an AST sweep of
   every `create(` and `handle_inbound(` call site in the repo — `scripts/`
   included — then `make verify`.

### Amended while implementing

**Step 6 landed a file this plan did not name, because a real failure forced
it.** `persona_store.known_accounts()` reads `memory_entries`, and the `sadana
persona` subcommand's own scaffold ensured only PERSONA's schema — so the new
`accounts` verb died with `no such table: memory_entries` on a store that had
never chatted. That is the "ensure_schema is now a three-item checklist"
finding PERSONA-01's own altitude review raised and this project deferred with
"two lines at two sites; revisit when a third table exists". The third table
is in this diff, and the gap bit within the hour. `src/sadana/stores.py` now
owns `ensure_schemas()`, and `open_runtime()`, both CLI scaffolds and
`conftest.make_runtime` go through it. The self-check then found the two CLI
scaffolds had become byte-identical copies, so `open_cli_store()` joined it and
both copies are gone.

**Five tests from PERSONA-01 met the new refusal and were adapted, not
weakened.** `test_subcommands_persona.py`'s `use`/`list` cases selected for the
account `"a1"`, which nothing had ever used; they now name `"a1"` as the owner
rather than assuming any string is selectable. Every assertion is unchanged.

**`create()`'s new keyword reached four test call sites this plan did not
list** (`test_gateway_dispatch.py`, `test_subcommands_chat.py` twice,
`test_subcommands_conversations.py`), found by an AST sweep rather than a
grep — the same instrument PERSONA-01's cold review had to use, applied here
before the suite could hide them.

**Roughly thirty test lines gained `# pragma: allowlist secret`.**
`detect-secrets` reads `account_key="..."` as a keyword-shaped secret, which
is the repo's own documented false positive (`.pre-commit-config.yaml`, and
the same pragma already sits on `account_key` lines in `test_memory.py`).

## Risks

**What this could break that already works.** Five named things. (1)
`conversation_store.create()`'s new required keyword reaches ~17 call sites in
`test_conversation_store.py` plus one in `test_client_surface.py`; they are
mechanical, but a missed one is a `TypeError` at collection time, not a subtle
failure, so the suite will say so immediately. (2) `test_scheduling.py`'s fake
`handle_inbound` has a fixed two-argument signature and will raise
`TypeError` the moment `tick()` passes a keyword — expected, and it is the
test that proves step 5 landed. (3) Every existing scheduled conversation
changes whose memories it recalls, from `schedule:<name>` to the owner's: that
is the point of the item, and it means a trigger that had learned something
under its own name recalls the owner's facts from the next firing onward. (4)
`open_runtime()` gains a write at startup, on a path that until now only read
and created schemas — every client and every test that opens a runtime pays
one `UPDATE` against a table that is usually empty. (5) The developer's own
real store is migrated the first time they run any `sadana` command after
this, irreversibly; tests are unaffected because the autouse fixture redirects
the state directory.

**The riskiest step is 4, and it is fourth on purpose.** It is the only step
that mutates existing data, it runs on every process start, and it has no
undo. Step 3 lands the same function with its collision and idempotency tests
first, so by the time anything calls it the dangerous half is already pinned.
The alternative ordering — wire it in and then test it — would have the first
real store it touches be the developer's own. Step 5 is second-riskiest
because it changes behaviour every surface can observe, which is why it also
comes after the pieces it uses.

**Where this plan could drift back into something the spec rejected.** Four
places. In step 2, `create()`'s extra keyword will feel like noise at ~18 call
sites and the temptation is the column on `conversations` that spec.md
rejected — it is rejected for a reason the call-site count does not touch: the
value would have to become a `Conversation` field that `run_child` cannot
supply. In step 5, `scheduling.py` builds the `MessageEvent` itself, so
putting the account *on the envelope* is one line and reads naturally; that is
the alternative the reference corpus argued out of the design, and CLAUDE.md
now has a rule against it. In step 6, deriving the account list from the
memory tables alone is less code and cannot see a chat that never remembered
anything — the case the listing exists for. And in step 3, moving the rubric
overrides and character selections along with the entries is a literal reading
of the intent that spec.md § Requirements 5 deliberately narrowed.

**Known limitation, carried from spec.md § Concerns, not mitigated here.** A
person who deliberately keeps notes under a `schedule:` account — which
`sadana memory set-rubric`/the memory plugin allow today — has them relocated
on the next process start with no warning. Collisions keep both texts, so
nothing is destroyed, but nothing announces the move either.

## Proof

`tests/unit/test_memory.py` covers `owner_account()` with the variable set and
unset, asserting against `memory.owner_account()`'s own default rather than
the literal `"local"`.

`tests/unit/test_memory_store.py` covers the adoption in four cases: entries
under two different `schedule:` accounts all land on the owner; an entry whose
key the owner already holds is kept under the suffixed key with both texts
intact; a second call returns 0 and changes nothing; and a rubric override
under a `schedule:` account is still there afterwards (requirement 5's
negative). Plus `accounts_with_memories` over a store with three accounts.

`tests/unit/test_conversation_store.py` covers that `create()` writes the
account row, that `accounts_with_conversations` returns it, and that a
conversation row whose `conversation_accounts` row is missing — the legacy
shape — still loads and still appears nowhere in the account list rather than
raising.

`tests/unit/test_scheduling.py` covers that a fired trigger's account is
`memory.owner_account()` while its conversation key is still
`schedule:<name>` — both halves in one assertion pair, because the whole
design rests on those two being different.

`tests/unit/test_gateway_dispatch.py` covers that a webhook event with no
override still runs under `webhook:<chat_id>`, and that an inbound payload
carrying an `account` field does not change the account the door is called
with.

`tests/unit/test_persona_store.py` covers `known_accounts` seeing an account
that has only a conversation, only a memory, and only a selection — three
sources, three cases — and `account_exists` for a name in none of them.

`tests/unit/test_subcommands_persona.py` covers `persona accounts` output
(the owner marked, a selection shown), `persona use` refusing an unknown
account with exit 1 and writing nothing, and `persona use <owner> <character>`
succeeding against a store with no history at all.

Then the AST sweep of `create(`/`handle_inbound(` call sites across `src/`,
`tests/` and `scripts/`, pasted into review.md's Evidence, and `make verify`
ending `VERIFY OK`.
