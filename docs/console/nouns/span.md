## span

Served by: H20. Child of `trace`.

- **Fields:** `node_id`, `kind`, `status` (`ok`/`error`/`skipped`),
  `started_at`, `ended_at?`, `input_preview?`, `output_preview?`, `error?`.
- **States:** `ok`, `error`, `skipped`.
- **Actions:** none.
- **Filterable / orderable:** `status`, `kind`, `node_id` filterable;
  `created_at` orderable (present so the framework's own default `order_by`
  doesn't reject a bare `GET /v1/spans` — no row is ever produced to sort by
  it).
- **`search_doc`:** `{"title": id}` — present for protocol uniformity;
  nothing ever writes a `span`-noun ledger row for it to render from.
- **Where it differs from the console's prompt:** `list` always returns an
  empty page and `get` always answers `404 NOT_FOUND` — the underlying table
  doesn't exist until H21. This is a deliberate, honest gap, not an
  implementation shortcut: the console's own prompt lists a five-value `kind`
  enum; this runtime's node kinds are seven (`compute`, `ask`, `route`,
  `stop`, `call`, `each`, `wait` — `docs/console/wire.md` §6), which will
  matter once H21 starts producing real rows.
