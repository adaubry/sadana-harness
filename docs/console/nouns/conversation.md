## conversation

Served by: H20.

- **Fields:** `name`, `agent_id`, `last_message_preview` (first 200 chars of
  the last message), `message_count`. `message_count`/`last_message_preview`
  are derived at read time, never stored — nothing here changes
  independently of the `messages` table itself.
- **States:** `active`, `archived`.
- **Actions:** `archive` (`active → archived`), `unarchive` (`archived →
  active`), `rename` (`{name}`, from either state — also reachable as `PATCH
  {name}`).
- **Filterable / orderable:** `state`, `agent_id`, `created_at`,
  `updated_at` filterable; `created_at`, `updated_at`, `name` orderable.
- **`create`:** `{name?, agent_id?}` → `201` with the row. Mints its own
  `conv_<uuid7>` and calls `client_surface.open_conversation(...,
  conversation=<that id>, id=<that id>)` — a console-created conversation's
  `key` and `id` are the same string (spec.md's own "Decisions already
  made"). `agent_id`, if given, is resolved to a character name through the
  `agents` index (a name, never a pointer) before the conversation is
  created; an unresolvable `agent_id` is `404 NOT_FOUND`. With no
  `agent_id`, the account's own current character selection is used, exactly
  as the terminal already does.
- **`remove`:** always `501 HARNESS_CAPABILITY_MISSING` — nothing deletes a
  conversation.
- **`search_doc`:** `{"title": name, "subtitle": agent_id, "body":
  last_message_preview, "facets": {"state", "agent_id", "harness"}}`.
- **Visibility:** only the acting account's own conversations —
  `conversation_accounts` scopes every read. Another account's id reads
  `404 NOT_FOUND`, never `403`.
- **Where it differs from the console's prompt:** none known yet.
