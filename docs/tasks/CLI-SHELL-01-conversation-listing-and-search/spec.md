# Spec: A saved conversation can be found again without already knowing its exact name

Intent: docs/tasks/CLI-SHELL-01-conversation-listing-and-search/intent.md

## Requirements

1. There is a way to see every conversation that has ever been saved.
   (Intent §Proposed outcome.)
2. There is a way to find a specific saved conversation by something
   remembered about it — part of its saved name, or something that was
   actually said in it — without already knowing its exact name. (Intent
   §Proposed outcome.)
3. Matching does not care about letter case. (Intent §Constraints.)
4. `create()`, `save()`, and `load()`'s exact-name contract are unchanged.
   (Intent §Constraints.)
5. A conversation found this way is the same full thing `load()` already
   returns for its key — not a lighter summary. (Intent §Constraints.)
6. Nothing in production code calls this yet — no registered command, no
   menu entry. Only a test drives it in this work item. (Intent
   §Constraints, §Affected users and systems.)

## Design

**Reference corpus.** `docs/reference/cli_shell_blueprint.md` is now the
canonical read of this block (written against this same work item, once it
turned out no such document existed yet — see that file's own §2). Its §4.2
table confirms hermes's own session-listing/search lives in `hermes_state.
py:10854`'s `list_sessions_rich` and `hermes_cli/session_listing.py`, filed
by hermes under SESSION-STORE, not CLI-SHELL — CLI-SHELL only ever holds the
thin `sessions` subcommand that calls it (blueprint §6, work item 3). This
work item is the blueprint's work item 1: the store-side prerequisite that
item 3 (the actual `hermes sessions list`/`search` command) needs before it
can exist. What I'm adopting: SQL-level substring matching against name-like fields is the right
altitude — resolving a match in Python would mean loading every row's full
message history just to filter most of it back out, which requirement 5
otherwise makes cheap to do correctly. What I'm declining, and why: `hermes`'s
version pages results (`limit`/`offset`), scores and orders by a recursion
over compression-continuation chains, projects branch/delegate children
forward, and additionally matches a punctuation-stripped variant of the
query. None of those concepts have any backing state in our schema —
`conversations`/`messages` has no `archived`, `pinned`, `parent_session_id`,
`source`, or `hidden` column, and no compression chain exists to walk. Adopting
that shape here would mean designing seven columns worth of stored state
this work item's intent never asked for, to satisfy scenarios (branch
sessions, archived sessions, paginated dashboards) sadana-harness has none
of. The punctuation-stripped match is a real, cheap idea, but it's a UX
preference the interview didn't ask for and intent's own trace explicitly
left match-semantics open to this stage's judgment, not a requirement to
carry forward from the reference — declined for now, easy to add later if
it's missed.

**Where this lives.** `conversation_store.py`, alongside `create`/`save`/
`load` — not a new module. CLAUDE.md's I/O-module rule ("a module that
touches real I/O is its own file") is about separating pure logic from I/O,
not about one function per file; this is more of the exact same I/O
(sqlite queries against the exact same connection) the module already owns.

**One function, not two.** `search_conversations(conn, query, *, now)`.
Listing "every conversation ever saved" (requirement 1) is search with an
empty query — every row's key/template_name/content trivially contains the
empty string, so `search_conversations(conn, "")` already is the list
operation with zero extra code. This is guideline 3 in miniature: the
"list everything" scenario is caught by the step that already exists
(search) made to handle one more input, rather than by adding a second
function with its own SQL statement to keep in sync with the first one's
`_CONVERSATION_COLUMNS` iteration and row→`Conversation` mapping.

**Query shape.**

```sql
SELECT DISTINCT c.key
FROM conversations c
LEFT JOIN messages m ON m.conversation_key = c.key
WHERE c.key LIKE ? ESCAPE '\'
   OR c.template_name LIKE ? ESCAPE '\'
   OR m.content LIKE ? ESCAPE '\'
ORDER BY c.key
```

`?` is bound three times to the same `%<escaped query>%` pattern. SQLite's
`LIKE` is case-insensitive for ASCII by default (no explicit
`PRAGMA case_sensitive_like` is set anywhere in this module) — the same
mechanism already gives requirement 3 for free. `_escape_like()` backslash-
escapes a literal `%`, `_`, or `\` in the caller's query before it is
wrapped in `%...%`, so a query containing one of those characters matches
it literally instead of as a SQL wildcard. `DISTINCT` collapses a
conversation that matches on more than one row (its key and a message, or
two separate messages) to one result. For each key the query returns,
`load(conn, key, now=now)` supplies the actual `Conversation` — this is
requirement 5 and guideline 4 in the same move: no second row→`Conversation`
mapping is written or maintained, `load()`'s existing one is reused
unchanged, so there is exactly one place that shape can drift.

**Ordering.** `ORDER BY c.key` — alphabetical, fully derived from the
existing primary key, no new column. Recency ordering (hermes's
`order_by_last_active`) would need a `last_active` timestamp nobody writes
today; adding one is new stored state (guideline 4) for an ordering
requirement 1 and 2 never asked for.

## Interface

```python
def search_conversations(
    conn: sqlite3.Connection, query: str, *, now: float
) -> list[Conversation]: ...
```

- **Input** `query`: any string, including `""`. No escaping is the
  caller's responsibility — `search_conversations` escapes `%`/`_`/`\`
  itself before building the `LIKE` pattern.
- **Input** `now`: forwarded to `load()` unchanged (turns each matched row's
  stored remaining-seconds figure back into a fresh deadline) — same
  parameter, same meaning, as everywhere else in this module.
- **Output**: `list[Conversation]`, one per distinct match, ordered by key.
  An empty list is a normal result (nothing saved yet, or nothing matched)
  — never an exception. This differs from `load()` deliberately:
  `ConversationNotFound` exists because asking for one exact name that
  isn't there is a caller error; asking a search question that happens to
  match nothing is not.
- **Errors**: none new. A malformed `conn` (already closed, wrong schema)
  surfaces the same `sqlite3.OperationalError` any other function in this
  module would raise; not caught or translated here, matching `load()` and
  `save()`'s own behavior today.

## Acceptance criteria

- [ ] `search_conversations(conn, "")` on an empty store returns `[]`.
- [ ] `search_conversations(conn, "")` returns every saved conversation,
      each equal to what `load()` returns for its own key.
- [ ] A query matching a substring of one conversation's `key` (any case)
      returns exactly that conversation.
- [ ] A query matching a substring of one conversation's `template_name`
      (any case) returns exactly that conversation.
- [ ] A query matching a substring that appears only inside one message's
      `content` (any case) returns that conversation.
- [ ] A query containing a literal `%` or `_` matches only that literal
      substring — not as a SQL wildcard.
- [ ] A conversation whose key, template_name, and more than one message
      all match the same query appears exactly once in the result.
- [ ] A query matching nothing returns `[]`, not an exception.
- [ ] `create()`, `save()`, `load()`'s existing tests are untouched and
      still pass — this work item adds a function, it does not modify one.
- [ ] `make verify` ends `VERIFY OK`.

## Non-goals

- A runnable CLI command, console entry point, or anything that wires this
  into a menu or an agent tool surface — a separate, later work item names
  that per intent.md's own "Affected users and systems".
- Ranking, relevance scoring, or fuzzy/typo-tolerant matching (e.g.
  hermes's punctuation-stripped variant).
- Pagination, `limit`/`offset`, or any bound on result size.
- Ordering by recency, or any other property requiring a new stored
  column.
- Full-text indexing (SQLite FTS5 or otherwise).

## Rejected alternatives

- **Adopting `list_sessions_rich` wholesale** — declined; see §Design. It
  answers scenarios (archiving, pinning, compression-chain projection,
  multi-source scoping) that have no corresponding state in this schema.
- **Two functions (`list_conversations()` and `search_conversations()`)**
  — declined; `search_conversations(conn, "")` is the list operation, and
  keeping one function keeps one SQL statement and one place the
  row→`Conversation` mapping can be wrong.
- **A lightweight summary row (id/template/message-count) instead of a
  full `Conversation`** — already rejected during planning (intent's
  §Changed during planning); would need a second mapping alongside
  `load()`'s, and a caller that then wants the actual conversation would
  have to load it again anyway.
- **SQLite FTS5 virtual table for the content match** — declined; needs a
  synchronized index (a `messages_fts` shadow table kept in step with
  every insert, i.e. new stored state with its own consistency story) to
  solve a performance problem this store, at a single maintainer's local
  scale, does not have yet. A plain `LIKE` scan is correct and the whole
  `messages` table already fits comfortably in a page cache at this size;
  revisit if search latency is ever actually measured and found wanting.
- **Recency ordering via a new `last_active` column** — declined; no
  requirement asks for it, and it is new mutable state (a value that goes
  stale relative to `save()` calls) for an ordering nobody described
  wanting. `ORDER BY key` is free and derived.

## Concerns

- **Policy skills named by design-skill but absent from this repo.**
  `project-structure` and `reference-lookup` do not exist under
  `.claude/skills/` here — there is nothing to load or apply beyond what
  CLAUDE.md itself says about module layout (applied above: same file,
  same reasons the module was split from `conversation.py` in the first
  place) and the general "consult the reference corpus" instruction
  (applied above against `cli_shell_blueprint.md`). Naming this so it's
  not silently skipped, per design-skill's own instruction.
- **This spec was written before `cli_shell_blueprint.md` existed, then
  revised once it did.** The revision changed only citations and framing
  (this is confirmed, not guessed, to be work item 1 of an ordered
  sequence) — none of the design decisions above moved, because none of
  them conflict with the blueprint's own constraints (§5: no second path
  to capability — this adds a query function, not a new one; one user per
  instance, no billing/accounts — untouched by a sqlite query either way;
  one exit-code/error-formatting convention — not applicable, this isn't a
  CLI handler). This holds even after the blueprint's own deployment-model
  correction (its revision note, and §1.1) — that correction changed
  whether a *daemon* belongs in this block eventually; it didn't touch
  anything a plain query function over an existing sqlite store depends
  on. Recorded so a reader doesn't wonder whether the blueprint changed
  something here silently.
- **`testing-conventions` is applied**: new tests extend
  `tests/unit/test_conversation_store.py` (same module under test, same
  file per the naming rule) using the same `tmp_path`-backed `open_store()`
  pattern every existing test in that file already uses — no new fixture,
  no mock, no source-file reading, real sqlite against a temp path.
- **The N+1 query shape** (one query for matching keys, one `load()` per
  match) is not a bet against performance at the scale this work item
  targets (a maintainer's local store, intent says "only the maintainer,
  and only a test, actually exercises this"), but it is the first thing
  to revisit — before the `LIKE` scan itself — if this store ever holds
  enough rows that a search visibly costs something. Not solved here;
  flagged so it isn't rediscovered as a surprise.
- **Guideline 2 (reduce bets / plugin seam)**: this work item sits well
  below the plugin seam — it is one query function added to an existing
  I/O module, not new core behavior a plugin would sit on top of. No
  tension to report there.
- No policy conflict was found between `testing-conventions` and anything
  else this design touches; the two are independent here (what gets
  tested vs. where the code lives), which is why only one paragraph above
  addresses each.
