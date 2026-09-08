# Review: search_conversations() in conversation_store.py (from plan.md 2026-09-08)

Reviewed: HEAD..working tree (uncommitted; nothing committed for this work
item yet) — 2 files, +128/-1
Reviewer context: fresh session
Second opinion: none — plan.md § Order of work step 5 records a build-time
`/ponytail-review` + `/simplify` self-check; not repeated here by design
(deploy-skill's current scope runs the three passes once, cold).

## Evidence

```
$ make verify
docs/tasks/CLI-SHELL-01-conversation-listing-and-search: all present artifacts valid
CHAIN OK
trim trailing whitespace.................................................Passed
fix end of files.........................................................Passed
mixed line ending........................................................Passed
check for case conflicts.................................................Passed
check yaml...............................................................Passed
check toml...............................................................Passed
check json...............................................................Passed
check for merge conflicts................................................Passed
check for added large files..............................................Passed
check that scripts with shebangs are executable..........................Passed
check that executables have shebangs.....................................Passed
detect private key.......................................................Passed
ruff.....................................................................Passed
ruff-format..............................................................Passed
shellcheck...............................................................Passed
Detect secrets...........................................................Passed
docs/reference/ citations resolve to tracked files.......................Passed
LINT OK
Success: no issues found in 12 source files
TYPES OK
▶ hermetic run: TZ=UTC LANG=C.UTF-8 PYTHONHASHSEED=0, empty env
........................................................................ [ 20%]
........................................................................ [ 40%]
........................................................................ [ 61%]
........................................................................ [ 81%]
................................................................         [100%]
352 passed in 3.29s
TESTS OK
VERIFY OK
```

## Findings

Cold review, no builder context. Three passes run (bugs, security,
compliance) against `intent.md`/`spec.md`/`plan.md`, plus a files-touched
scope check, a line-by-line read of the diff, and the full compliance
checklist (Proof items, Acceptance criteria, Rejected alternatives, the five
design principles). No Important findings surfaced; everything checked came
back clean, detailed below.

### Scope check

`git diff --stat HEAD` shows exactly the two files plan.md § Files that
change names — `src/sadana/conversation_store.py` (+34) and
`tests/unit/test_conversation_store.py` (+95/-1) — nothing else touched, and
neither named file is untouched. No drift here.

### Bugs — none found

Traced `_escape_like()`'s replacement order (backslash, then `%`, then `_`)
against the one thing that would silently break it — escaping `%`/`_` before
the backslash step, which would double-escape. The order in the diff
(`conversation_store.py:270`) is backslash-first, matching plan.md's
explicit call-out of this as the riskiest step, and
`test_search_escapes_percent_and_underscore_as_literals`
(`test_conversation_store.py:430-436`) exercises both a literal `%` (must
match) and a literal `_` (must not wildcard-match `50Xoff`) — the exact case
that would have caught the wrong order. `LIKE ... ESCAPE '\'` is a single
backslash in the SQL string; `_escape_like` produces `\\` (one escaped
backslash) for a literal source backslash, `\%`/`\_` for the other two —
consistent with SQLite's own escape-char semantics.

The `LEFT JOIN` against `messages` with a nullable `content` column: SQL
`NULL LIKE ?` evaluates to `NULL`, not an error and not a false match, so a
conversation with zero messages or a message with `content IS NULL` is
handled correctly by falling through to the `c.key`/`c.template_name`
branches. Confirmed against the schema (`conversation_store.py:53-61`,
`content` has no `NOT NULL`).

Checked the `now` threading: `search_conversations` forwards its own `now`
to `load()` unchanged for each matched key (`conversation_store.py:295`),
same parameter/meaning as everywhere else in the module — matches spec.md §
Interface.

### Security — none found

The query string is bound as a parameter (`conn.execute(..., (pattern,
pattern, pattern))`) after only string-level `%`/`_`/`\` escaping done in
Python, never interpolated into the SQL text — no injection surface. No new
logging, no secrets, no widened I/O (same connection, same file).

### Compliance

**Proof (plan.md) — both items discharged:**
- "Eight new tests... cover: empty store, list-everything, match-by-key,
  match-by-template, match-by-content, dedup-on-multi-field-match,
  no-match, and wildcard-character escaping" — counted eight
  `test_search_*` functions in the diff (`test_conversation_store.py:348-436`)
  and each covers exactly the named case; `test_search_empty_query_returns_every_conversation`
  and the dedup test additionally assert exact equality/uniqueness rather
  than just non-emptiness.
- "`make verify` ends `VERIFY OK`" — discharged by the Evidence above, run
  fresh in this session.

**Acceptance criteria (spec.md) — all ten satisfied:**
1. Empty store → `[]`: `test_search_empty_store_returns_empty_list`.
2. Empty query → every conversation: `test_search_empty_query_returns_every_conversation`,
   asserts `found[i] == load(conn, key, now=...)` for each.
3. Key substring, case-insensitive: `test_search_matches_key_substring_case_insensitively`.
4. `template_name` substring, case-insensitive: `test_search_matches_template_name_substring_case_insensitively`.
5. Message `content` substring: `test_search_matches_message_content_substring`.
6. Literal `%`/`_` not treated as wildcards: `test_search_escapes_percent_and_underscore_as_literals`.
7. Multi-field match deduplicates to one result: `test_search_deduplicates_conversation_matching_multiple_fields`.
8. No match → `[]`, no exception: `test_search_no_match_returns_empty_list`.
9. `create`/`save`/`load`'s existing tests untouched and passing: the diff's
   only edit to existing code is `_conversation()`'s new optional
   `template_name` parameter defaulting to `"t1"` (today's hardcoded value),
   so every existing call site is unchanged; the 352-pass count in Evidence
   includes them.
10. `make verify` ends `VERIFY OK`: Evidence above.

**Rejected alternatives (spec.md) — checked for drift, none found:**
- `list_sessions_rich` wholesale — not adopted; no `archived`/`pinned`/
  `parent_session_id`/pagination/scoring appears anywhere in the diff or
  schema.
- Two functions (`list_conversations` + `search_conversations`) — only one
  function exists; `search_conversations(conn, "")` is the list path, no
  second SQL statement.
- Lightweight summary row — `search_conversations` returns
  `list[Conversation]` built by calling the module's own unchanged `load()`
  per key (`conversation_store.py:295`); no second row→`Conversation`
  mapping was written.
- FTS5 — schema (`conversation_store.py:38-62`) adds no virtual table, no
  shadow index; `search_conversations` is a plain `LIKE` scan.
- Recency ordering / new `last_active` column — `ORDER BY c.key` only; no
  schema change.

**Five design principles:**
1. *Learn from the reference first* — spec.md's Design section names
   hermes's `list_sessions_rich` and explains what's declined and why
   (pagination/scoring/archival state this schema has none of); the code
   matches that stated scope.
2. *Reduce the number of bets* — additive, reversible: one new function, one
   new private helper, no schema migration, no new column. Low cost either
   way.
3. *More plugins, not more core* — not applicable at this altitude (a query
   function in an existing I/O module, well below the plugin seam); spec.md
   § Concerns says the same and I found nothing that contradicts it.
4. *Catch the scenario at the least step-cost* — "list everything" is
   handled by calling the existing search path with `""`, adding zero new
   code paths, exactly as designed.
5. *Minimise mutable state* — no mutable state introduced. The N+1
   query-then-`load()`-per-row shape is a documented, accepted performance
   trade-off (spec.md § Concerns, plan.md § Risks), not a state concern.

### Nits

None worth recording — cap is five and I found none. (Docstring cites
`CONV-08 spec.md requirement 7's deferred half`; verified against
`docs/tasks/CONV-08-sqlite-transcript-store/spec.md:35-37`, which does say
"Looking a conversation up happens by its exact `key` only... adds no
listing, filtering, or search operation" — citation is accurate, not a
nit.)

## Decision

Approved by Adam Aubry, 2026-09-08.
