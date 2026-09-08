# Plan: search_conversations() in conversation_store.py (from intent.md 2026-09-08)

## Files that change

- `src/sadana/conversation_store.py` — add `_escape_like()` and
  `search_conversations()`.
- `tests/unit/test_conversation_store.py` — extend the `_conversation()`
  test helper with an optional `template_name` parameter, add
  `search_conversations` to the existing import block, add new test
  functions.

## Order of work

1. **Add `_escape_like(query: str) -> str`** in `conversation_store.py`,
   right before `search_conversations` (see step 2). Backslash-escapes a
   literal `\`, `%`, or `_` in that order — backslash first, so escaping
   `%`/`_` afterward doesn't get double-escaped by the backslash step.

   ```python
   def _escape_like(query: str) -> str:
       return query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
   ```

2. **Add `search_conversations(conn, query, *, now) -> list[Conversation]`**,
   inserted after `load()` (current file ends `load()` at line 261) and
   before `bind_persist()` (line 264) — it's a read path built on `load()`,
   same as `load()` is a read path on the raw tables; goes next to it.

   ```python
   def search_conversations(conn: sqlite3.Connection, query: str, *, now: float) -> list[Conversation]:
       """..."""  # per spec.md's Interface section
       pattern = f"%{_escape_like(query)}%"
       rows = conn.execute(
           "SELECT DISTINCT c.key FROM conversations c "
           "LEFT JOIN messages m ON m.conversation_key = c.key "
           "WHERE c.key LIKE ? ESCAPE '\\' "
           "OR c.template_name LIKE ? ESCAPE '\\' "
           "OR m.content LIKE ? ESCAPE '\\' "
           "ORDER BY c.key",
           (pattern, pattern, pattern),
       ).fetchall()
       return [load(conn, row["key"], now=now) for row in rows]
   ```

   SQLite's `LIKE` is case-insensitive for ASCII by default (no
   `case_sensitive_like` pragma is set anywhere in this module), which is
   what gives requirement 3 (case-insensitivity) for free. `DISTINCT`
   collapses a conversation matching on more than one row. An empty
   `query` produces pattern `"%%"`, which every `key`/`template_name`
   (both `NOT NULL`) satisfies — this is the "list everything" behaviour,
   with no second code path.

3. **Extend the test helper**: add `template_name: str = "t1"` to
   `_conversation()`'s signature (`tests/unit/test_conversation_store.py:47`)
   and thread it into the returned `Conversation(...)` in place of the
   current hardcoded `"t1"` literal. Default preserves every existing
   call site's behavior unchanged. Add `search_conversations` to the
   `from sadana.conversation_store import (...)` block.

4. **Add tests**, in this order (simplest assumptions first, the fiddly
   escaping case last):
   - `test_search_empty_store_returns_empty_list`
   - `test_search_empty_query_returns_every_conversation` — two
     conversations created, `search_conversations(conn, "", now=...)`
     returns both, each `== load(conn, key, now=...)`.
   - `test_search_matches_key_substring_case_insensitively`
   - `test_search_matches_template_name_substring_case_insensitively`
     (uses the new `template_name=` param)
   - `test_search_matches_message_content_substring`
   - `test_search_deduplicates_conversation_matching_multiple_fields`
     (one conversation whose key, template_name, *and* a message all
     contain the query — asserts it appears exactly once)
   - `test_search_no_match_returns_empty_list`
   - `test_search_escapes_percent_and_underscore_as_literals` — a
     conversation keyed e.g. `"50%off"`, queried with `"50%off"`, must
     match; a sibling conversation keyed `"50Xoff"` must *not* match a
     query of `"50_off"` (which would match it if `_` were treated as a
     SQL wildcard instead of a literal).

5. Run `make test` scoped to this file (or the narrowest command
   `CLAUDE.md` gives for a single test file), then `/ponytail-review` and
   `/simplify` against the diff per build-skill's phase-two close, then
   `make verify`.

## Risks

- **What could this break?** Nothing existing — `search_conversations` is
  a pure addition; `create`/`save`/`load`/`bind_persist` and every test
  that exercises them (`test_create_then_load_round_trips_every_field`,
  `test_save_upserts_repeatedly_on_owned_key`,
  `test_bind_persist_success_saves_growing_messages`, etc.) are untouched.
  The one edit to existing code is `_conversation()`'s new optional
  parameter, which is additive and defaults to today's behavior, so no
  existing call site changes meaning.
- **Riskiest step**: step 1/2's `LIKE` escaping. Getting the replacement
  order wrong (escaping `%`/`_` before backslash) would double-escape and
  silently break literal-wildcard matching in a way normal tests wouldn't
  catch unless they specifically test a query containing `%` or `_` —
  which is why that case is its own named test, ordered last so the
  straightforward matching behavior is proven first.
- **Drift check against spec.md's Rejected alternatives**: one function
  not two (list = search with `""`) — followed. Full `Conversation` via
  `load()`, not a summary row — followed. No FTS5 — followed, plain
  `LIKE`. No recency column/ordering — followed, `ORDER BY key`. No drift.

## Proof

- `tests/unit/test_conversation_store.py`'s eight new tests (named above)
  cover: empty store, list-everything, match-by-key, match-by-template,
  match-by-content, dedup-on-multi-field-match, no-match, and
  wildcard-character escaping — each asserting the returned list's
  contents/emptiness and, where relevant, exact equality against `load()`.
- `make verify` ends `VERIFY OK`.
