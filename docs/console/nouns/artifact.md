## artifact

Served by: H20.

- **Fields:** `run_id`, `filename`, `mime`, `size_bytes`, `kind`
  (`file`/`link`/`markdown`), `url?`. `run_id` is derived, not stored — the
  `plugin_runs` row filed under the same `(conversation_key, turn_seq,
  seq_in_turn)` composite key `observability._insert_artifacts` already uses.
  `url` is the row's own `ref` when `kind == "link"`, `null` otherwise.
- **States:** `ready`, `expired`. Nothing in this work item ever writes
  `expired` — RETENTION-01 is the item that would.
- **Actions:** `download` (`ready → ready`, capability `artifacts.download`,
  not consequential). The index row's `ref` is checked with
  `artifact_store.contains(run_dir, str(run_dir / ref))` before any file is
  opened; a `..`, an absolute path, or a path resolving outside the run
  directory is `400 VALIDATION`, detail `"artifact path escapes its run
  directory"`. On success the response streams the file directly — the row's
  `mime`/`size_bytes` as `Content-Type`/`Content-Length` — via a one-line
  addition to `router.py`'s own dispatch (a `DoorResponse` an `act()` returns
  is passed straight through instead of JSON-wrapped; no other noun's
  behaviour changes).
- **Filterable / orderable:** `run_id`, `kind`, `mime`, `created_at`
  filterable; `created_at` orderable.
- **`search_doc`:** `{"title": filename, "facets": {"kind", "run": run_id}}`.
- **Where it differs from the console's prompt:** `create` is always `501
  HARNESS_CAPABILITY_MISSING` — nothing creates an artifact through the door,
  only a plugin run does, and that path is entirely internal.
