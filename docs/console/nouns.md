# Nouns

One section per console-owned-by-harness noun. Each is filled in by the work
item that actually serves it, with: fields, states, actions, filterable,
orderable, `search_doc`, and — where they differ — "what the console's
prompt says" beside "what the box returns". A noun with no work item listed
yet is not served by this box; asking for it is `404 NOT_FOUND` at the
routing step, never a stub response.

## harness

Served by: H19. Singleton — `GET /v1/harness`, never a plural collection.

- **Fields:** `id`, `version` (`sadana.__version__`), `capabilities`,
  `tether`, `org`, `ledger_head`, `leaves_the_box`.
- **States:** none — `harness` carries no `state` field.
- **Actions:** `upgrade` (declared, capability-gated off until H30);
  `deregister`, `purge-account` are not declared at all yet (H30 adds both,
  and mints their capability names inside the console's own frozen
  contract).
- **Filterable / orderable:** neither — there is exactly one `harness` and
  no list endpoint for it.
- **`search_doc`:** `{"title": "Harness <id>"}` — present for protocol
  uniformity; nothing in H19 ever writes a `harness`-noun ledger row for it
  to render from.
- **Where it differs from the console's prompt:** none known yet.

## operations

Served by: H19, as the fixed `GET /v1/operations/{id}` path only — never a
plural collection, and never created directly by a client (only
`run_bounded`'s own promotion path writes one).

- **Fields:** `id`, `state` (`running`/`succeeded`/`failed`), `resource`
  (`{noun, id}` or `null`), `error` (a `Problem` or `null`), `created_at`,
  `updated_at`.
- **States:** `running` → `succeeded` | `failed`. Terminal states do not
  transition further.
- **Actions:** none.
- **Filterable / orderable:** neither — no list endpoint.
- **`search_doc`:** not applicable — `operations` never appears as a
  `ctx.nouns` lookup target from `/v1/changes`' rendering.

## conversation

Served by: not yet (H20).

## message

Served by: not yet (H20).

## run

Served by: not yet (H20).

## trace

Served by: not yet (H20).

## span

Served by: not yet (H20).

## approval

Served by: not yet (H18).

## schedule

Served by: not yet (H27).

## agent

Served by: not yet (H26).

## agent_template

Served by: not yet (H26).

## memory_entry

Served by: not yet (H26).

## memory_policy

Served by: not yet (H26).

## artifact

Served by: not yet (H20).

## plugin

Served by: not yet (H24).

## workflow

Served by: not yet (H24).

## node

Served by: not yet (H24).

## tool

Served by: not yet (H24).

## inspection

Served by: not yet (H24).

## provider

Served by: not yet — no harness id assigned in `docs/reference/console_fit_plan.md` §3 yet.

## budget

Served by: not yet — no harness id assigned in `docs/reference/console_fit_plan.md` §3 yet.

## integration

Served by: not yet — no harness id assigned in `docs/reference/console_fit_plan.md` §3 yet.

## secret

Served by: not yet — no harness id assigned in `docs/reference/console_fit_plan.md` §3 yet (write-only per §6's credential-value rule, once it lands).
