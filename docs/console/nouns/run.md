## run

Served by: H20, live states and `stop` by H21.

- **Fields:** `conversation_id`, `message_id` (the user message that opened
  the turn), `agent_id`, `plugin_id?`, `exit_reason?`, `duration_ms`,
  `iterations` (= `model_calls`). Every field is derived at read time from
  `turn_runs` (OBSERVABILITY-01) plus one join each into `conversations`,
  `messages` and `agents` — nothing new is stored. `plugin_id` is always
  `null` in this work item: nothing records which `plugin_runs` row, if any,
  spawned this turn as a child conversation, and deriving it needs a change
  outside this artifact's file set — a known, named gap, not a guess.
- **States:** `running`, `waiting`, `done`, `failed`, `stopped`. A run's own
  row starts `running` (or `waiting`, for a turn with an outstanding pause)
  the moment its turn begins, via `observability.record_turn_started` —
  H21 turns this from a five-word closed set with only two live members
  into one where every word is real. `stopped` is `interrupted`'s own
  render, whether a person asked for it (`runs.stop`) or the process itself
  cancelled the turn.
- **Actions:** `stop` (`running`/`waiting → stopped`, capability
  `runs.stop`) — cooperative, not forced: it signals the turn's own
  `threading.Event` (`door/run_control.py`) and returns immediately with
  the run's current rendering; the run settles to `stopped` only once the
  turn itself notices, at its next safe point. `409 CONFLICT` when this
  process is not holding the run right now — already finished, or the
  process restarted since the run started.
- **Filterable / orderable:** `state`, `exit_reason`, `agent_id`,
  `conversation_id`, `created_at` filterable; `created_at`, `duration_ms`
  orderable.
- **`exit_reason` mapping** (`docs/console/wire.md` §6, the same table):
  `completed → completed`; `budget_exhausted`/`wall_clock_exhausted →
  budget_exhausted`; `interrupted → stopped`; `persistence_failed` /
  `provider_failed` / `context_overflow_unhandled` / `invalid_tool_calls →
  error`; and `completed` degrades further to `failed_node` when any
  `plugin_runs` row under the same turn recorded one, even though the turn
  itself finished.
- **`search_doc`:** `{"title": "Run " + id[-6:], "subtitle": exit_reason ??
  state, "body": agent_id, "facets": {"state", "exit_reason",
  "conversation", "harness"}}`.
- **Where it differs from the console's prompt:** none known yet, beyond the
  `plugin_id` gap above.
