# Spec: Serving the console's first screens through the door

Intent: docs/tasks/H20-door-nouns-turn-side/intent.md

Author: Adam Aubry (maintainer). Status: approved.

## Requirements

Each group traces to `intent.md`. This work item runs concurrently with a
second, separately-scoped lane building a different set of nouns from the
same commit of `main` — see § Design's "Parallel-lane discipline" for what
that constrains.

**Conversations** (intent § Proposed outcome ¶1 "list and open the
conversations that belong to their own account, rename or archive one")

1. `door/nouns/conversations.py` declares `NounSpec(plural="conversations",
   prefix="conv", filterable={"state","agent_id","created_at","updated_at"},
   orderable={"created_at","updated_at","name"}, states={"active","archived"},
   actions={"archive": active→archived, "unarchive": archived→active,
   "rename": either→same (also reachable via PATCH {name})})`.
2. `list`/`get` join `conversations` to `conversation_accounts` on
   `conversation_key` and return only rows whose `account_key ==
   account_key_for(principal)`; an id that exists but belongs to another
   account is `404 NOT_FOUND`, never `403` (wire.md §1 Visibility).
3. `create({name?, agent_id?})`: mints `conv_<uuid7>` via `ids.make_id("conv")`
   and calls `client_surface.open_conversation(runtime, account=account_key_for
   (principal), conversation=<that id>, template_name="console")` — the minted
   id **is** the `ConversationKey` (Rules: "Conversations created through the
   door have key == id"). `agent_id`, if given, is resolved to a character
   name via `SELECT name FROM agents WHERE id = ?` (404 if unresolvable — a
   name, never a pointer, stored in `conversations.agent` by
   `client_surface._create` from the account's `persona_store.get_selection`
   unless overridden — see § Design for how an explicit `agent_id` reaches
   that column). Returns `201` with the row; `name` defaults to the minted
   id if omitted (Rules: "name defaults to the key").
4. `remove` is always `HARNESS_CAPABILITY_MISSING` — no `NounSpec.actions`
   entry and `remove()` returns `nouns.unavailable("conversations", "remove")`
   unconditionally; the docstring states nothing deletes a conversation.
5. `search_doc = {title: name, subtitle: agent_id, body:
   last_message_preview, facets: {state, agent_id, harness: harness_id}}`.
6. `agent_id` is rendered by looking up `conversations.agent` (a name) against
   `SELECT id FROM agents WHERE name = ?`; a conversation whose agent name no
   longer resolves (a deleted character) renders `agent_id: null` rather than
   raising — the character file is gone, not the conversation.
7. `message_count` and `last_message_preview` are derived at read time —
   `SELECT COUNT(*), (SELECT content FROM messages WHERE conversation_key=?
   ORDER BY msg_seq DESC LIMIT 1)` — never stored (guideline 4: nothing here
   changes independently of the messages table itself, so caching it would be
   a value that goes stale the moment a message is appended).

**Messages** (intent § Proposed outcome ¶1 "send a new message and watch it
become a turn whose answer shows up as a finished message and a finished run")

8. `door/nouns/messages.py` declares `NounSpec(plural="messages",
   prefix="msg", parent="conversations", filterable={"role","created_at",
   "run_id"}, orderable={"created_at"}, states={"streaming","sent","failed"},
   actions={})`. `streaming` is declared for schema uniformity with H21's
   future live rows; nothing in this work item ever renders it.
9. `list`/`get` scope through the parent conversation exactly as
   Requirement 2 (join `conversation_accounts`); an unknown or foreign
   `parent_id` is `404`. A message on a conversation this account cannot see
   is unreachable the same way.
10. `create({content})` is the turn. `src/sadana/door/turn_client.py` (new,
    owned by this artifact alone) exposes one function:

    ```python
    async def send(runtime, *, account, conversation, text) -> client_surface.TurnOutcome:
        return await client_surface.take_turn(
            runtime, account=account, conversation=conversation, text=text, create_as=None,
        )
    ```

    It calls `take_turn` and nothing deeper (Rules) — no `conversation_store`,
    no `plugin_dispatch` import. `messages.create()` runs inside the door's
    thread pool (it is `fn` under `operations.run_bounded`, per
    `router.py:274`'s generic dispatch — see H19 spec.md requirement 22-24),
    so it calls `asyncio.run(turn_client.send(...))`, exactly the pattern any
    synchronous `fn` running on its own pool thread uses to drive an
    `async def`.
11. Before calling `turn_client.send`, `create()` loads the conversation once
    (`conversation_store.load`) to fail fast with `404` if the parent id does
    not resolve to an existing, visible conversation, and `409 CONFLICT` (
    detail `"create requires state active; current state is archived"`) if
    `state != "active"` — Requirement: "a message on an archived conversation
    → 409 (archived is not a state a turn runs from)". This is implemented as
    an explicit `from_states=("active",)` check inside `create()` itself
    (`NounModule.create` has no generic state gate — only `act` does, per
    router.py's own requirement-25 pipeline — so this check is this noun's own
    code, not framework-provided).
12. On success (`outcome.ok`), the newly-appended message rows are located and
    tagged with `run_id` (Requirement 15), and `create()` returns the **user**
    message's own rendered row (not the assistant's — see Requirement 17 for
    why). Router's own generic 201-vs-202 split (`bounded.done`) then decides
    whether that row is ever seen directly by the caller.
13. On failure (`not outcome.ok`), any newly-appended assistant-role message
    row is updated to `state = "failed"` and nothing else (no diagnostic is
    written onto the message — see Requirement 16). `create()` returns
    `problems.make("INTERNAL", outcome.diagnostic)` (chosen over a new
    error code — the closed 13-code list has no closer fit than `INTERNAL`
    for "the turn itself failed", and `INTERNAL`'s own definition ("an
    unexpected error") is stretched here on purpose: every other code in the
    list names a *request-shape* problem, and this is the one place a
    perfectly well-formed request's *outcome* is the failure). `_on_done`
    (operations.py, unmodified) turns this `Problem` return into the
    promoted operation's `state: "failed"`, `error: {..., "detail":
    outcome.diagnostic}` automatically — no new code needed there.
14. A conversation with an outstanding pause takes `take_turn`'s existing
    resume path (Decisions: do not reopen). `turn_client.send` does not
    special-case this — `client_surface.take_turn` already branches on
    `conversation_store.load_pause` internally, invisibly to this module.
    The resumed reply is appended as one `assistant` message with **no**
    `run_id` (no `turn_runs` row is written for a resume — `persist_pause`'s
    own resume path never calls `record_turn`). Requirement 19 documents how
    this message's `turn_seq` renders.
15. **`run_id` backfill.** Immediately before calling `turn_client.send`,
    `create()` reads `before = conversation_store.load(reader, key, now=…)`
    and keeps `turn_seq_before = before.next_turn_seq`,
    `msg_count_before = len(before.messages)`. After `send` returns (success
    or failure), it reads `after = conversation_store.load(reader, key,
    now=…)`; the newly-appended rows are `after.messages[msg_count_before:]`.
    It looks up `SELECT id FROM turn_runs WHERE conversation_key = ? AND
    turn_seq = ?` using `turn_seq_before` — present whenever the turn actually
    ran (not the resume path, Requirement 14) — and, if found, issues one
    `UPDATE messages SET run_id = ? WHERE conversation_key = ? AND msg_seq >
    ?` covering every row from this turn (user, assistant, and any
    intermediate tool-call/tool-result rows alike — they are all part of the
    one turn `run_id` names). See § Concerns for the narrow, accepted race
    this two-read design carries.
16. **No `tags.diagnostic`.** `messages` has no `tags_json` column and this
    work item does not add one (§ Design "Where storage was declined").
    Every message's rendered `tags` is `{}`, always — including a `failed`
    one. The diagnostic is available exactly where it is authoritative: the
    parent run's `exit_reason`/`detail` (Requirement 20) and, transiently, the
    failed create-operation's own `error.detail` (Requirement 13). A page
    reloaded after a failure sees the same explanation both times, from the
    same place, rather than a field that reads populated once and empty ever
    after.
17. **`operations.resource` is `null` for a promoted `messages.create`, and
    stays `null` on success too.** Traced in full in § Design "The
    operation-resource wall" — `router.py:274` computes `resource =
    (noun.spec.plural, route.id) if route.id else None` before `run_bounded`
    ever calls `fn`, and a `POST /v1/{plural}` route's `route.id` is always
    `None` by the grammar (create has no id segment); `operations.py`'s
    `_on_done`/`_transition` never revisit `resource_noun`/`resource_id` once
    set. This is an inherited H19 framework limitation, not an H20 defect,
    and fixing it (a router.py/operations.py change) is out of this
    artifact's authorized file set — see § Concerns and § Rejected
    alternatives. **When `messages.create` finishes inside the router's own
    2-second bound** (the fast path — `bounded.done`, no operation object at
    all), the response is the real, direct `201` with the user message row,
    fully populated, exactly like any other noun's create. Only the
    *promoted* (202) case carries `resource: null`; the golden-journey test
    (Requirement 22) asserts this explicitly, not by omission, so a future
    change to the behaviour is caught rather than silently absorbed.
18. Once an operation reads `succeeded` or `failed`, the console's own path to
    the assistant's reply is `GET /v1/conversations/{id}/messages` — the same
    call the message list already serves. Documented in
    `docs/console/nouns/message.md`, `docs/reference/console_fit_plan.md` §6
    and `docs/console/wire.md` §6 in the same commit (wire.md's own docstring
    already states the two stay in sync) — see § Design.
19. **`turn_seq` is derived, not stored.** `messages` has no `turn_seq`
    column. It renders as `SELECT turn_seq FROM turn_runs WHERE id =
    messages.run_id` — `null` when `run_id` is `null` (a resumed reply,
    Requirement 14, or a legacy pre-H20 row). This corrects the brief's
    listing of `turn_seq` as a bare (non-optional) field: a resumed message
    genuinely has none, and inventing one would misattribute it to an
    adjacent turn.
20. **Default order.** `grammar.parse_list_params` hardcodes `"created_at
    desc"` as its default with no per-noun override parameter (`grammar.py:96`)
    — a framework-wide function this artifact does not edit (§ Rejected
    alternatives). `messages.list` therefore defaults to `created_at desc`
    like every other noun, not the chronological ascending order a chat
    transcript wants by default; the console gets ascending order by passing
    `order_by=created_at asc` explicitly, which `orderable` already permits.
    Documented in `docs/console/nouns/message.md`.

**Runs** (intent § Proposed outcome ¶1 "list runs with what each one cost and
why it ended, jump from a run back to the message that started it")

21. `door/nouns/runs.py` declares `NounSpec(plural="runs", prefix="run",
    filterable={"state","exit_reason","agent_id","conversation_id",
    "created_at"}, orderable={"created_at","duration_ms"},
    states={"running","waiting","done","failed","stopped"}, actions={"stop":
    ActionSpec(from_states=("running","waiting"), to_state="stopped",
    capability="runs.stop", scope_verb="stop")})`. `"runs.stop"` is already in
    `capabilities.ALL` (H19); this work item does **not** add it to
    `DECLARED`, so the action is reachable in the grammar and answers `501
    HARNESS_CAPABILITY_MISSING` — the same "declared, not turned on" shape
    `harness.upgrade` already uses.
22. `list`/`get` read `turn_runs` only (never `plugin_runs` — those surface as
    `traces`, Requirement 24), scoped through `conversation_accounts` by
    joining on `conversation_key`. Rendered fields:
    - `conversation_id`: `SELECT id FROM conversations WHERE key = ?`.
    - `message_id`: `SELECT id FROM messages WHERE run_id = ? AND role =
      'user' LIMIT 1` — a reverse lookup through the very column Requirement
      15 backfills; needs no correlation by `turn_seq` at read time.
    - `agent_id`: `conversations.agent` (fixed for the conversation's whole
      life — `client_surface._create`'s own docstring: "a conversation takes
      its voice once, at birth") resolved through the agents index exactly as
      Requirement 6.
    - `plugin_id`: always `null` in this work item. A `turn_runs` row that is
      itself a plugin-spawned child turn (`ask`'s own `record_turn` call in
      `plugin_dispatch.py`) has no column anywhere recording which
      `plugin_runs` row spawned it — deriving it needs a change outside this
      artifact's file set. Documented as a known gap, not silently guessed.
    - `duration_ms`: `duration_s * 1000`.
    - `iterations`: `model_calls`.
    - `exit_reason`: mapped per wire.md §6's table, with the `failed_node`
      override — `completed` renders as `failed_node` instead when
      `EXISTS(SELECT 1 FROM plugin_runs WHERE conversation_key = ? AND
      turn_seq = ? AND failed_node IS NOT NULL)`.
23. `search_doc = {title: "Run " + id[-6:], subtitle: exit_reason ?? state,
    body: agent_id, facets: {state, exit_reason, conversation: conversation_id,
    harness: harness_id}}`.

**Traces** (intent § Proposed outcome ¶1 "look at a run's trace")

24. `door/nouns/traces.py` declares `NounSpec(plural="traces", prefix="run",
    parent="runs", filterable={"state","started_at"}, orderable=frozenset(),
    states={"open","closed"})`. **`prefix="run"`, not the reserved `"trc"`**
    — see § Design "Where the trace-id prefix stayed put" and § Concerns.
25. `list`/`get` read `plugin_runs` filtered by the parent run's own
    `(conversation_key, turn_seq)` — resolved from the parent run id via
    `turn_runs` — never by a bare `plugin_runs.id` lookup (which would
    collide with `runs`' own id space; the child-nesting route always carries
    the parent id, so this never needs to happen). Rendered fields:
    `root_node` (== the row's own `entry` — the node a DAG execution begins
    at is definitionally the graph's entry node, so no second value is
    derived or stored), `node_count`, `started_at`, `ended_at`, `plugin`,
    `entry`, `failed_node`. `state` is `"closed"` unless `failed_node is not
    None`, in which case `"failed"` — Requirement 24's declared enum lists
    `open|closed`; `"failed"` is the state actually stored
    (`observability.PLUGIN_RUN_STATES`), rendered as-is (`open` is reserved
    for H21's live traces and never occurs here, matching runs' own
    "post-hoc rows are done|failed until H21").

**Spans** (intent § Proposed outcome ¶1 "its per-step detail, honestly empty
where that detail isn't produced yet")

26. `door/nouns/spans.py` declares `NounSpec(plural="spans", prefix="spn",
    parent="traces", filterable={"status","kind","node_id"},
    orderable=frozenset(), states={"ok","error","skipped"})`. `list` always
    returns `grammar.ListResponse(data=(), next_page_token=None, count=0 if
    count requested else None)` — no table exists yet (H21 adds it); `get`
    always answers `404 NOT_FOUND` — never an error about the *table* being
    missing, which would leak an implementation detail the grammar has no
    code for.

**Artifacts** (intent § Proposed outcome ¶1 "list and download the files a
run's plugin left behind")

27. `door/nouns/artifacts.py` declares `NounSpec(plural="artifacts",
    prefix="art", filterable={"run_id","kind","mime","created_at"},
    orderable=frozenset(), states={"ready","expired"}, actions={"download":
    ActionSpec(from_states=("ready",), to_state="ready", capability=
    "artifacts.download", scope_verb="download", consequential=False)})`.
    `CAPABILITIES: tuple[str, ...] = ("artifacts.download",)` at module level
    (Rules), and this work item's whole contribution to `capabilities.py`'s
    `DECLARED` is appending that one name.
28. `list`/`get` read the `artifacts` table, scoped through
    `conversation_accounts` by `conversation_key`. `run_id` is derived:
    `SELECT id FROM plugin_runs WHERE conversation_key = ? AND turn_seq = ?
    AND seq_in_turn = ?` against the artifact row's own composite key — the
    same value Requirement 24's `traces.id` renders, so an artifact's
    `run_id` and its owning trace's `id` are the same string by construction,
    not by convention.
29. `create` is `HARNESS_CAPABILITY_MISSING` (Rules: nothing creates an
    artifact through the door).
30. **`download`** (`POST /v1/artifacts/{id}/actions/download`, `act()`):
    resolves the artifact row, computes `run_dir =
    artifact_store.for_run(conversation_key, turn_seq, seq_in_turn)`, and
    checks `artifact_store.contains(run_dir, str(run_dir / row["ref"]))`
    **before any open**. A `..`, an absolute path, or a path resolving
    outside `run_dir` is `400 VALIDATION`, detail `"artifact path escapes its
    run directory"` — `contains()` already does the resolve-both-sides check
    (`artifact_store.py:110`), reused verbatim, not reimplemented. On success,
    streams the file with the row's `mime`/`size_bytes` as `Content-Type`/
    `Content-Length`. This is the one noun whose `act()` returns bytes, not a
    JSON resource — `router.py`'s `_dispatch`/`_handle` pipeline (requirement
    24 of H19 spec.md) always calls `noun.act(...)` and wraps whatever comes
    back as a `DoorResponse`-shaped JSON body; see § Concerns for the one
    router-shape question this raises, and § Design for the resolution that
    needs no router change.
31. `search_doc = {title: filename, facets: {kind, run: run_id}}`.

**Cross-cutting** (intent § Constraints, all four)

32. Every noun's `list`/`get`/`act` scopes through `conversation_accounts`
    joined by whatever path reaches a `conversation_key` for that row
    (direct on `conversations`; via `conversation_key` on `messages`,
    `turn_runs`, `plugin_runs`, `artifacts`). No noun ever trusts a
    caller-supplied account value — `account_key_for(principal)` (already
    `door/auth.py:165`) is the only account any handler uses, matching
    Rules: "Nothing in a body names an account."
33. `docs/console/nouns.md` is left exactly as H19 wrote it. Each noun's
    prose lives at `docs/console/nouns/<noun>.md` — new files, this
    artifact's own — per the amendment; H30 concatenates the directory into
    `nouns.md` later.
34. `tests/contract/nouns/test_<noun>.py`, one per noun, plus
    `tests/contract/nouns/test_golden_journey.py` for the cross-noun
    scenario — all new files, building their own `DoorContext` with the six
    real noun modules (never editing `test_console_grammar.py`'s
    `harness`+`widgets`-only fixture — see § Design "Reusing H19's test
    scaffolding without editing it").

## Design

**Prior art consulted, and what was taken.**

This work item's own predecessors (H16, H19, OBSERVABILITY-01) are its real
reference corpus — the nouns it serves are entirely sadana's own schema, so
there is little to learn from `../hermes-agent`'s core about *this* shape.
Two narrower things were worth checking against it:

- **The artifact-download path guard.** `hermes-agent`'s
  `tools/path_security.py` (`kind == production-code`) is the resolve() +
  `relative_to()` pattern this project's own `artifact_store.contains()`
  (`artifact_store.py:110`) already implements, independently, with the extra
  discipline of resolving *both* sides and rejecting the directory itself as
  a candidate. **Declined to import or re-derive anything from
  `path_security.py`**: `artifact_store.contains()` is already this
  project's own, already-audited version of the identical idea, and
  Requirement 30 reuses it verbatim rather than writing a second
  implementation of the same check next to it.
- **A generic conversation/message REST listing layer.** No close analogue
  exists in hermes's core — its dashboards and connectors (`plugins/platforms/
  slack/adapter.py`, `tui_gateway/server.py`) are channel adapters, not a
  token-scoped multi-account REST surface over a shared store. Nothing was
  taken from or declined against them; the shape here is H19's own
  (`grammar.page`, `filter.evaluate`, the `NounModule` protocol), which this
  work item is the first real (non-`harness`, non-fixture) consumer of.

Inside this repository, the load-bearing precedent is
`tests/contract/fixture_noun.py`'s `WidgetsNoun` — the one already-proven
noun-module idiom: `list()` gathers every row the account may see into plain
dicts and hands them to `grammar.page(rows, params)`, which does the
filtering, ordering and keyset paging generically (`grammar.py:141`). Every
noun's `list()` in this work item follows that exact shape — one SQL read,
one `grammar.page` call — rather than any noun re-implementing pagination or
filter evaluation itself.

**Parallel-lane discipline.**

Per the amendment: new files only, everywhere possible. The four files this
artifact may edit that the other lane might also touch are `capabilities.py`
(one line: `artifacts.download`, appended — `DECLARED` is already
one-entry-per-line, so no reformatting is needed, unlike the amendment's
"convert once" contingency), the H19 noun registry (`subcommands/door.py`'s
`nouns={"harness": harness}` dict literal at line 98 — six lines added,
alphabetical by plural: `artifacts`, `conversations`, `messages`, `runs`,
`spans`, `traces`), `pyproject.toml` (untouched — no new dependency), and
`stores.py` (untouched — no new table). Everything else this artifact needs
lives in files only this artifact writes.

**Where storage was declined, and where it wasn't.**

Two schema gaps surfaced during design that the brief's field list assumed
were already closed:

- **`conversations.name`.** No column exists (H16's own additions to
  `conversations` are `id, created_at, updated_at, version, state, tags_json,
  agent` — no `name`). Rename is a first-class, explicitly required action,
  not something derivable from anything else, so this work item adds
  `conversations.name TEXT` via the exact idempotent `PRAGMA table_info` +
  guarded `ALTER TABLE` pattern `conversation_store.py`'s own
  `_MIGRATED_COLUMNS`/`migrate_columns` already use for every prior column
  added after a table shipped (CLAUDE.md's own rule for this). This is a
  fifth shared file — `src/sadana/conversation_store.py`, outside the
  amendment's pre-cleared four — approved explicitly for this one column
  after the tradeoff was raised: the column is additive, nullable-then-
  defaulted (`created_at`-shaped precedent: no safe default to invent for a
  pre-existing row, so `NULL` until read, and `conversations.py`'s own `get`/
  `list` render `name = row["name"] or row["key"]`, i.e. "name defaults to
  the key" is applied at *read* time, not by a backfill — CLAUDE.md's own
  rule: "a column with no safe default falls back to matching pre-fix
  behavior at read time"). A guarded `ADD COLUMN` is low-conflict-risk by
  construction (append-only, and the other lane's nouns are not conversation
  fields), but it is still named here explicitly rather than folded silently
  into "own files."
- **A durable, per-message diagnostic field was declined.** `messages` has no
  `tags_json`, and this work item does not add one. The reasoning (from the
  same tradeoff conversation): a field that reads populated once (in the
  create-operation's own transient response) and `{}` forever after on every
  subsequent read is worse than a field that is honestly always `{}` — a
  console page reloaded after a failure would see a message with no
  explanation and read the failure as having "disappeared." The diagnostic is
  not lost — it lives durably on the parent run (`turn_runs.exit_reason`,
  already true since OBSERVABILITY-01) and transiently on the failed
  operation's own `error.detail` at the moment of the call. Guideline 4
  (minimise mutable state): this is exactly a value that would go stale
  relative to a fact already stored correctly elsewhere, so it is derived
  from the run at read time rather than duplicated onto the message.

**The operation-resource wall.**

Traced by reading `router.py:_handle` and `operations.py:_promote`/
`_on_done`/`_transition` directly, not assumed: a promoted `POST`'s
`resource` is computed exactly once, in `router.py`, from `route.id` —
`(noun.spec.plural, route.id) if route.id else None` — before `run_bounded`
is ever called, and a create route's `route.id` is `None` by the grammar
(`POST /v1/{plural}`, no id segment) on *every* noun, not just `messages`.
`_transition`'s success/failure branches (`operations.py:135-146`) write only
`state`/`error_json`; nothing in the completion path ever revisits
`resource_noun`/`resource_id`. So a promoted create's `resource` reads `null`
forever, on any noun, today — an inherited limitation of H19's own delivered
framework, not something introduced here.

Three ways to close it were weighed:

1. **A generic router.py change** — teach `router.py` to accept a resource
   hint from the noun (a `NounModule` protocol addition, or a value `create()`
   could return cheaply before the slow work). Rejected: `NounModule` is the
   shape *every* noun module across *both* lanes satisfies; changing its
   protocol from a lane whose snapshot of `main` predates the other lane's
   own noun modules is a conflict that merges silently and fails at dispatch
   (a noun that doesn't implement a new protocol member still imports fine).
   That is a worse failure mode than a textual conflict, and the fix belongs
   to a chain with one lane running against the framework itself, which is
   H30 — already the item that reopens `operations.py` for
   `resume_on_start`'s own successor.
2. **Have `messages.create` write directly into the `operations` table via
   raw SQL**, bypassing `operations.py`'s own functions. Rejected: `fn` (the
   noun's `create()`) is a zero-argument callable — it has no reference to
   its own operation's `id`, assigned entirely inside `run_bounded`'s closure
   after `fn` is already submitted. There is no id to `UPDATE` against
   without querying for "whichever running operation looks like mine," which
   is exactly the kind of race this project's per-conversation locking
   exists to avoid introducing elsewhere.
3. **Accept `resource: null`, and say so precisely** (adopted). The console's
   actual path to the created assistant message — `GET
   /v1/conversations/{id}/messages`, once the operation settles — is a call
   it already has to make for the message list itself. Documented in three
   places, kept in sync (`wire.md`'s own docstring already commits to this
   for §6, which is the model this follows): `docs/console/nouns/message.md`
   (the field-level fact), `docs/reference/console_fit_plan.md` §6 and
   `docs/console/wire.md` §6 (the same one-line addition to both, in this
   same commit — "What the console's prompts did not anticipate": *"A
   promoted `messages.create` operation carries `resource: null`; read the
   assistant reply from the conversation's messages once the operation
   settles. H30 adds a create-time resource hint to the router and fills this
   in."*). The golden-journey test (Requirement 34) asserts `operation
   ["resource"] is None` explicitly for the promoted path, not merely absence
   of a check — so H30 landing the hint is a test failure here that has to be
   reconciled, not a silent behaviour change.

The fast (<2s, unpromoted) path is unaffected: `messages.create()`'s own
return value — the user message's full row — becomes the direct `201` body
exactly as any other noun's create does, since no `operations` row is ever
created for a call that finishes inline (`router.py`'s `bounded.done`
branch never touches `operations` at all).

**Where the trace-id prefix stayed put.**

`ids.PREFIXES` already reserves `"trc"` for a trace (`ids.py:41`) and
`ledger.py`'s own `_NOUNS["traces"]` entry (prefix `"trc"`, empty `sources`)
carries a comment naming H20 as the item expected to fill it. Filling it
correctly means `plugin_runs.id` rows would need to be minted under `trc_`
instead of the `run_` prefix `observability.py`'s already-shipped
`_insert_plugin_run` (`observability.py:260`) mints them under today —
`ids.make_id("run")`, alongside `turn_runs`' own ids, because OBSERVABILITY-01
predates the console's noun split and modeled both as one thing. Re-minting
requires editing `observability.py`, which is outside this artifact's file
set (not one of the amendment's four, and not "own" — it is OBSERVABILITY-01's
file, already shipped and reviewed).

**Declined the fix, kept the live data.** `traces.py`'s `NounSpec.prefix` is
set to `"run"` — the prefix its actual ids really carry — rather than `"trc"`,
which would be documenting an aspiration as if it were a fact. Nothing in
`router.py`'s generic dispatch validates a noun's rendered ids against its own
declared `spec.prefix` (only `ledger.record_change`'s *writers* do that check,
and `traces.py` never writes to the ledger — it is read-only, no
`NounSpec.actions`), so this costs nothing functionally today. The residual,
honestly-named cost: `GET /v1/changes` already emits a `"runs"`-noun event for
every `plugin_runs` insert (`observability.py`'s own `_insert_plugin_run`
ledgers it that way, unchanged by this work item), and `harness.py`'s
`_search_doc_for` (`harness.py:89`) resolves that event's `search_doc` by
calling `ctx.nouns["runs"].get(ctx, principal, id)` — which, for a
`plugin_runs`-originated id, 404s, because `runs.py` (Requirement 22) reads
`turn_runs` only. The event still appears in the change feed (with its `id`,
`state`, `version` intact); only its `search_doc` enrichment is silently
omitted, via the same `isinstance(current, problems.Problem)` guard
`_search_doc_for` already has for exactly this "the noun doesn't recognise
this id" case. See § Concerns for the bound on this.

**Reusing H19's test scaffolding without editing it.**

`tests/contract/test_console_grammar.py`'s `door` fixture is module-scoped
around `nouns={"harness": harness, "widgets": WidgetsNoun(...)}` and is not
built to be extended from outside the file. Rather than editing it (a fifth
shared file with no need — the amendment already says "if a helper you need
is private, add your own"), each of this artifact's own test files builds its
own `DoorContext` — same `auth.generate_dev_keypair`/`Verifier`/
`stores.Connections` idiom, but wired with `{"harness": harness,
"conversations": conversations, "messages": messages, "runs": runs, "traces":
traces, "spans": spans, "artifacts": artifacts}`. `_validate`/`schema` (module-
level, already usable across files via a relative import) are reused;
`_token`/`_req`-shaped helpers are re-declared locally per the amendment's own
instruction, scoped to each account/scope combination this artifact's tests
need (`messages:write`, `conversations:write`, etc. — scopes this artifact's
own tests mint, not ones `test_console_grammar.py` already covers).

**The `messages.create`/`turn_client` split.**

`turn_client.py` is one function, `send()` (Requirement 10) — the entire
"calls `take_turn` and nothing deeper" surface. Everything else described in
Requirements 11-19 (the pre/post reads, the `run_id` backfill, the state
transition on failure) lives in `messages.py` itself, which is not similarly
restricted — it already does raw SQL against `conversation_store`'s tables,
same as every other noun module here does against its own.

## Interface

- **Inputs**: `NounModule.list/get/create/update/remove/act` per H19's
  `Protocol` (`door/nouns/__init__.py`), unchanged — this work item supplies
  six new implementers, no new protocol member.
- **Outputs**: JSON resources shaped per wire.md §1's standard fields, plus
  each noun's own fields (Requirements 1-31); `artifacts.act(..., "download",
  ...)` is the one verb that returns raw bytes rather than a JSON mapping —
  resolved inside `act()` itself by returning a small sentinel
  (`ArtifactDownload(mime, size_bytes, path)`, a frozen dataclass local to
  `artifacts.py`) that `router.py`'s existing `_handle` already treats
  correctly with **no router change**: `json_response`/`problem_response` are
  the only two response constructors `_handle` calls today, and a
  `Problem`-vs-anything-else check is already how it decides between them
  (`if isinstance(result, problems.Problem): ... else: ... json_response`) —
  so `artifacts.py`'s own `act()` opens the file and returns a
  `DoorResponse` **built directly by `artifacts.py`**, bypassing
  `json_response` by constructing `door.request.DoorResponse` itself (an
  already-public type, `door/request.py`), which `_handle`'s generic "else"
  branch passes straight through unmodified (`json_response(status, result,
  etag=etag)` is only called when `route.kind` isn't `"remove"`/`"list"`; an
  `act` result that is already a `DoorResponse` needs `_handle`'s own
  `else` arm to recognise it — see § Open questions, this is the one place
  this artifact is not yet certain a zero-router-change path exists and
  needs confirming against the literal code in `plan.md`, not asserted here).
- **Errors**: the closed 13-code list, unchanged; no new code introduced.

## Acceptance criteria

- [ ] `make verify` ends `VERIFY OK`.
- [ ] `docs/console/nouns/{conversation,message,run,trace,span,artifact}.md`
      exist, each filled per Requirements 1-31.
- [ ] `capabilities.DECLARED` gains exactly one line, `"artifacts.download"`.
- [ ] The H19 noun registry (`subcommands/door.py`) gains six lines, one per
      noun, alphabetical by plural.
- [ ] `conversation_store.py` gains exactly one guarded column:
      `conversations.name TEXT`.
- [ ] `tests/contract/nouns/test_{conversations,messages,runs,traces,spans,
      artifacts}.py` and `test_golden_journey.py` all pass under the
      `contract` marker.
- [ ] The golden journey: list (empty) → create → list (one, `message_count`
      0) → create message → operation `running` → wait → operation
      `succeeded`, `resource: null` (asserted explicitly) → messages list
      shows user and assistant in order, both `run_id`-tagged → run `done`
      with `message_id` = the user message → rename with a stale `If-Match`
      → `412` → with the right one → `200`, version+1, ledger row → archive →
      `409` on a second archive → another account's token → `404` on that
      conversation → a message on an archived conversation → `409` → the
      artifact guard's three refusals (`..`, absolute, resolved-outside) →
      `400 VALIDATION` each → a failed turn's assistant row carries `state:
      "failed"` and the operation's `error.detail` carries the diagnostic.

## Non-goals

- Live/streaming message or run state (`streaming`, `waiting`, `running` as
  observed states) — H21.
- `runs.stop` actually stopping anything — capability declared, not turned
  on, `501` until H21 or later.
- Re-minting `plugin_runs` ids under `trc_` — a future work item once
  `observability.py` is in scope for whoever owns it next.
- A create-time resource hint on promoted operations — H30.

## Open questions

- **Does `artifacts.act`'s raw-bytes return genuinely need zero changes to
  `router.py`'s `_handle`, or does `_handle`'s `else` branch need one
  `isinstance(result, DoorResponse)` check added before it calls
  `json_response`?** Traced as far as static reading supports; the literal
  answer depends on exactly how `_handle`'s final branch is written, which
  `build-skill`'s planning pass should confirm against the live file (not
  this document) before committing to either "no router change" or "one
  isinstance check, additive, in the same `else` arm every other noun's
  response already flows through." If it turns out to need the check, that
  check is generic (benefits any future noun that streams bytes) and single-
  line — flagged for the same review discipline as the `conversations.name`
  column, not silently added.

## Rejected alternatives

- **A generic router.py resource-hint protocol addition** — § Design "The
  operation-resource wall," option 1. Declined for cross-lane protocol-change
  risk, not for being architecturally wrong; it is the right fix, for H30.
- **`messages` posting into `operations` via raw SQL from inside `fn`** — same
  section, option 2. Declined: no id to address the row by from inside `fn`.
- **Re-minting `plugin_runs` under `trc_`** — § Design "Where the trace-id
  prefix stayed put." Declined: requires editing `observability.py`, outside
  this artifact's scope, for a purely cosmetic id-prefix correction with a
  narrow, already-bounded cost (one enrichment field, one event kind).
- **A `messages.tags_json` column carrying the diagnostic** — § Design "Where
  storage was declined." Declined per guideline 4: the same fact is already
  durable on the run; storing it twice is exactly the staleness guideline 4
  warns against (a message's `tags` would read correct once, and then simply
  wrong — not absent, wrong — forever after, since nothing would ever update
  it again).
- **A custom `created_at asc` default for `messages.list`** — Requirement 20.
  Declined: `grammar.parse_list_params` has one hardcoded default, shared by
  every noun; changing it is a framework-wide behaviour change for a
  chat-transcript convenience the console can get for free with one explicit
  query parameter it always sends anyway.

## Concerns

**The `run_id` backfill race (Requirement 15) is real, narrow, and bounded to
cosmetic misattribution.** Between `create()`'s "before" read
(`msg_count_before`/`turn_seq_before`) and `turn_client.send`'s own
acquisition of `stores.conversation_lock(conversation)`, a second concurrent
`messages.create` on the *same conversation* could interleave — this module
cannot hold that lock itself across the call without deadlocking (it is a
plain, non-reentrant `threading.Lock`, confirmed at `stores.py:185-204`, and
`take_turn` already acquires it for the call's whole duration). The
consequence, if it happens, is confined to the derived read-side fields this
artifact adds (`run_id` landing on the wrong turn's messages, `message_id`
pointing at an adjacent turn) — never data loss, never a wrong account
seeing another's conversation, never the turns themselves running out of
order or corrupting the transcript (H16's own lock still fully serializes the
actual turns). The clean fix is `client_surface.take_turn` returning the
`TurnKey`/`turn_seq` it used, which is a `client_surface.py` change outside
this artifact's file set and outside "calls `take_turn` and nothing deeper."
Accepted as a known, small-blast-radius gap rather than reason to touch a
fifth file for it.

**The `traces`-under-`"runs"`-ledger-noun overlap (§ Design, "Where the
trace-id prefix stayed put") is a real, inherited inconsistency, not one this
artifact introduces or can fully close.** `plugin_runs` rows are simultaneously
members of ledger noun `"runs"` (OBSERVABILITY-01's own design, unchanged) and
served by the door as `traces` (this work item's design, per the console's own
contract). The one observable symptom — a `plugin_runs`-creation event on `GET
/v1/changes` silently missing its `search_doc` — is bounded to that one
enrichment field on that one event kind; the event itself, its `id`, `state`
and `version`, all still arrive correctly.

**Where policies pulled against each other, guideline-2 ("reduce the count of
bets") and the parallel-lane discipline argued the same direction every time
this design hit a shared-file question** — decline the edit, document the gap
precisely, defer the fix to the chain step already positioned to make it
safely (H30 for the framework-level gaps, a future maintain-stage item for the
trace-id one). The one exception, `conversations.name`, was raised explicitly
rather than folded into that default, because unlike the others it has no
"read it from somewhere else instead" escape — a name a person actually typed
has nowhere else to live.

**Policy conformance.** `testing-conventions` is loaded and applied (test
placement, the `contract` marker, no direct pytest invocation — all per
Requirement 34 and the golden-journey acceptance criterion). No
`project-structure` or `reference-lookup` skill exists in this repository
today (checked: `.claude/skills/` lists only `audit-skill, build-skill,
deploy-skill, design-skill, plan-skill, testing-conventions`) — guideline 1's
reference-corpus consultation was done directly against
`../hermes-agent` and cited above, without that skill's help, since it isn't
present to load.
