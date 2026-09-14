## message

Served by: H20, streaming and live rows by H21. Child of `conversation` —
`/v1/conversations/{id}/messages[/{message_id}]`.

- **Fields:** `role` (`user`/`assistant`/`tool`), `content`, `run_id?`,
  `turn_seq`. `turn_seq` is derived at read time (`run_id → turn_runs.
  turn_seq`), not stored — `messages` has no `turn_seq` column. It renders
  `null` when `run_id` is `null`, which happens for a resumed pause's reply
  (no `turn_runs` row is ever written for a resume) and for any legacy
  pre-H20 row — a real, honest gap, not the brief's originally-assumed
  always-present field.
- **States:** `streaming`, `sent`, `failed`. The assistant row exists from
  the very start of every turn, always (H21) — inserted `streaming`, empty
  content, the moment the turn begins, and finalized once it ends: `sent`
  with the turn's own final text, or `failed` (empty content) if the turn
  never produced one. A live console watching `text_delta` frames sees the
  same row fill in as the reply streams; nothing durable records a
  fragment that arrived while nobody was watching (a delta is never
  ledgered).
- **Actions:** none.
- **`create({content})` is the turn.** `src/sadana/door/turn_client.py`
  calls `client_surface.take_turn` and nothing deeper, forwarding one more
  argument through unchanged: the door's own `TurnObserver`
  (`door/nouns/messages.py`'s own `_DoorTurnObserver`), which is what
  writes the user's row and the assistant's provisional row the moment the
  turn starts, streams `message.delta` ephemeral frames while it runs, and
  finalizes the assistant row once it ends. `create()` itself reads the
  result back by the turn's own `run_id` — never a `msg_seq` range
  computed before and after the call — and backfills `run_id` onto
  whatever else the turn produced (intermediate tool rows; the real final
  reply, if a tool call meant it landed somewhere other than the
  placeholder's own first guess). On failure, the assistant row already
  carries `state: "failed"` by the time `create()` returns; the diagnostic
  itself is never duplicated onto the message (see the run's own
  `exit_reason`/`detail`, and the failed operation's own `error.detail` at
  the moment of the call).
- **A conversation with an outstanding pause** takes `take_turn`'s existing
  resume path — no `_DoorTurnObserver` ever runs for one (`client_surface.
  take_turn`'s resume branch never calls `run_turn`), the model is never
  called, and the resumed reply is appended with no `run_id` at all;
  `create()` falls back to its pre-H21 before/after read for this one path,
  and to the pre-H21 "find the last assistant row, mark it failed" behavior
  when a resume itself fails.
- **Filterable / orderable:** `role`, `created_at`, `run_id` filterable;
  `created_at` orderable. Default order is `created_at desc` (the
  framework's own default — `grammar.parse_list_params` has no per-noun
  override), not the chronological ascending order a transcript wants by
  default; pass `order_by=created_at asc` explicitly for that.
- **Visibility:** scoped through the parent conversation exactly as
  `conversation`'s own account check; a message on a conversation this
  account cannot see is unreachable the same way.
- **`search_doc`:** `{"title": first 80 chars of content, "facets": {"role",
  "conversation"}}`.
- **Where it differs from the console's prompt: the operation-resource
  gap.** A promoted (`202`) `messages.create` operation carries `resource:
  null` — H19's `router.py`/`operations.py` compute a create's `resource`
  once, from the URL's own id segment, before the slow call ever runs, and
  never revisit it once the call finishes. Read the assistant reply from
  `GET /v1/conversations/{id}/messages` once the operation settles
  (`succeeded` or `failed`) — the same call the message list already
  serves. H30 adds a create-time resource hint to the router and fills this
  in; see `docs/reference/console_fit_plan.md` §6 and `docs/console/wire.md`
  §6 for the same note, kept in sync.
