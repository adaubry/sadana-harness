## approvals

Served by: H18. `docs/tasks/H18-parked-approvals/spec.md`. One row per
`wait` or `call` node a run parked at, written before anyone is asked —
never only in memory.

- **Fields:** the standard set (`id`, `created_at`, `updated_at`, `state`,
  `tags`, `harness_id`, `version`) plus `conversation_key`, `turn_seq`,
  `seq_in_turn`, `run_id`, `plugin`, `entry`, `node_id`, `kind`
  (`"wait"` | `"call"`), `question` (untrusted — plugin-authored text, never
  rendered as prompt or system text to a model), `requested_at`,
  `expires_at`, `answered_by`, `answer`, `decided_at`. `tags` is always
  `{}` — this noun carries no tag support yet.
- **States:** `waiting` → `approved` | `declined` | `answered` | `expired`.
  `waiting` is the only `from_states` any action accepts; every other
  transition is terminal.
- **Actions:** `approve` (`waiting`→`approved`, `call` only, no body) —
  runs the parked `call` node's body with the value it would have received
  live, then continues the walk. `decline` (`waiting`→`declined`, `call`
  only, body `{"reason"?: string}`) — ends the walk at that node without
  ever running its body. `answer` (`waiting`→`answered`, `wait` only, body
  `{"answer": string}`) — resumes the walk with the given text standing in
  for the wait node's own output. None declares a `capability`: existence
  of parked data at all is gated by `approvals.wait`/`approvals.call`
  (`docs/console/capabilities.md`), not the act of answering one a
  principal can already see.
- **Filterable:** `state`, `kind`, `run_id`.
- **Orderable:** `created_at`, `requested_at`. The inbox badge query is
  `GET /v1/approvals?filter=state = waiting&count=true`; a client that
  wants the oldest-waiting-first ordering the badge implies states
  `order_by=requested_at asc` explicitly — a bare list call sorts
  `created_at desc`, the same default every other noun gets (see
  `docs/tasks/H18-parked-approvals/spec.md` § Concerns for why this noun
  does not get its own default).
- **`search_doc`:** `{"title": question[:80], "facets": {"state", "kind",
  "run": run_id}}`.
- **Expiry:** a `waiting` row past its own `expires_at` (default 24h,
  `SADANA_APPROVALS_TTL_S`) becomes `expired` on the next scheduler tick,
  and the parked run is resumed as declined — never left waiting forever,
  never silently approved.
- **Where it differs from the console's prompt:** none known yet.
