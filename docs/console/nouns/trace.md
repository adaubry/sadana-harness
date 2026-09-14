## trace

Served by: H20. Child of `run` — `/v1/runs/{run_id}/traces[/{trace_id}]`,
never a bare `/v1/traces`.

- **Fields:** `root_node` (the entry node a DAG execution begins at — the
  same value `entry` holds; nothing else is derived or stored for it),
  `node_count`, `started_at`, `ended_at?`, `plugin`, `entry`, `failed_node?`.
- **States:** `open`, `closed`, `failed`. Every row this work item can
  produce is `closed`/`failed` — `open` arrives with H21, when a trace is
  observed live.
- **Actions:** none.
- **Filterable / orderable:** `state`, `started_at` filterable;
  `created_at` orderable.
- **`id` prefix.** Reads `run_<uuid7>`, not the reserved `trc_` prefix:
  `plugin_runs.id` (the row a trace renders) is already minted under `run_`
  by OBSERVABILITY-01, and re-minting it is outside this artifact's file
  set. `docs/reference/console_fit_plan.md`/`wire.md` don't carry this note
  — it's a data-shape fact rather than a console-anticipation gap, recorded
  here and in `docs/tasks/H20-door-nouns-turn-side/spec.md` § Concerns
  instead.
- **`search_doc`:** `{"title": entry, "facets": {"plugin"}}`.
- **Where it differs from the console's prompt:** a `plugin_runs`-creation
  event on `GET /v1/changes` renders under ledger noun `"runs"` (unchanged,
  pre-existing OBSERVABILITY-01 behaviour), not `"traces"` — so that one
  event's `search_doc` enrichment comes back empty (the event's `id`,
  `state` and `version` still arrive correctly). Bounded, documented, not
  fixed by this work item.
