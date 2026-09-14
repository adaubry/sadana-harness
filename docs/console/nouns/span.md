## span

Served by: H20 (schema), real rows and reads by H21. Child of `trace` —
reached as `/v1/traces/{trace_id}/spans[/{span_id}]`; a bare `/v1/spans`
(no parent) answers an empty list / `404`, the same posture every other
child noun in this block already takes for an unparented call.

- **Fields:** `node_id`, `kind`, `status` (`ok`/`error`/`skipped`),
  `started_at`, `ended_at?`, `input_preview?`, `output_preview?`, `error?`.
  One row per node a plugin run actually visits, written live as the walk
  reaches each one — never edited afterward, unlike `run`/`trace`'s own
  started-then-completed rows.
- **States:** `ok`, `error`, `skipped` (`skipped` declared for schema
  uniformity with a future `each` node kind; nothing produces it yet).
- **Actions:** none.
- **Filterable / orderable:** `status`, `kind`, `node_id` filterable;
  `created_at` orderable.
- **`search_doc`:** `{"title": node_id, "facets": {"kind", "status"}}`.
- **Where it differs from the console's prompt:** the console's own prompt
  lists a five-value `kind` enum; this runtime's node kinds are seven
  (`compute`, `ask`, `route`, `stop`, `call`, `each`, `wait` —
  `docs/console/wire.md` §6).
