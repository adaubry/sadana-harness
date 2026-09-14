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

Served by: H26.

- **Fields:** `name`, `description`, `template_id?`, `system_prompt`,
  `rendered_prompt` (read-only), `is_default` (read-only).
- **States:** `draft` → `active`. A character found by the ordinary
  directory scan (not created through the door) is `active` on sight —
  `draft` exists only for a row the door itself created and hasn't
  activated yet.
- **Actions:** `activate` (`draft`→`active`); `set-default` (from `active`,
  consequential) — `persona_store.set_selection` for the acting account.
- **Filterable / orderable:** `state`, `template_id`, `created_at` /
  `created_at`.
- **`search_doc`:** `{title: name, subtitle: description, facets: {state,
  template}}`.
- **Where it differs from the console's prompt:** `remove` answers
  `HARNESS_CAPABILITY_MISSING` — deleting a voice through the door isn't
  built yet. `system_prompt` is the character file's raw text, which the
  door wraps in `---`-delimited frontmatter at write time so a plain,
  unformatted prompt still parses as a character — a `create`/`update`
  round trip therefore does not return byte-identical text to what was
  submitted. `rendered_prompt` is the account's actual resolved voice only
  for the agent that is its current selection; every other agent renders
  the same text as `system_prompt`, not a counterfactual render. Neither
  `system_prompt` nor `rendered_prompt` is stored — both are computed at
  request time, and a conversation already created keeps whatever prompt it
  started with regardless of a later edit here.

## agent_template

Served by: H26. Read-only.

- **Fields:** `name`, `description`, `system_prompt`.
- **States / actions:** none — `create`/`update`/`remove`/every action
  answer `HARNESS_CAPABILITY_MISSING`.
- **Filterable / orderable:** none / `created_at`.
- **The documented exception:** a template is not a row. Exactly one exists
  today (`persona_store.BUILTIN_TEMPLATES["default"]`), and its id is
  `"tmpl_" + sha256(name)[:32]` — a pure function of the name, never
  `ids.make_id` (which mints a fresh id on every call) and never stored.
  `created_at`/`updated_at` are a fixed epoch-0 timestamp, not a real
  moment: documented here as a floor rather than a fact, the same posture
  §5(b) already takes for a legacy row's filled-in timestamp. A second
  built-in template, if one is ever wanted, is a second entry in this same
  dict, not a registry.

## memory_entry

Served by: H26.

- **Fields:** `content` (untrusted — written by the model, never
  interpreted as an instruction by anything reading it), `kind`
  (`decision`/`preference`/`fact`), `source`, `conversation_id?`.
- **States:** `kept` → `forgotten`.
- **Actions:** `forget` (`kept`→`forgotten`, consequential) —
  `memory_store.forget_entry`; the row survives, it just stops being
  recalled.
- **Filterable / orderable:** `state`, `kind`, `created_at` / `created_at`.
- **`search_doc`:** `{title: content[:80], facets: {kind, state}}`.
- **Where it differs from the console's prompt:** scoped to the acting
  account on every read and write — another account's entry is `404`,
  never `403` (wire.md's visibility rule). `create` answers
  `HARNESS_CAPABILITY_MISSING`: the one real write path today is the
  model's own `memory.remember` tool call, mid-conversation, tagged
  `source = "conversation"` — there is no second, human-authored write path
  to reserve a `"user"` source for. `kind` is a cosmetic label on what the
  model already wrote, chosen by the model itself as an optional tool
  argument (defaulting to `fact`); it does not change what gets captured —
  the account's own rubric, in prose, still decides that. A legacy row
  (written before H26) renders `kind = "fact"`, `source = "conversation"` at
  read time — never backfilled.

## memory_policy

Served by: H26. One row per account, always.

- **Fields:** `rubric`, `updated_by`.
- **States / actions:** none — only `update` is available; `create`/
  `remove`/every action answer `HARNESS_CAPABILITY_MISSING`.
- **Filterable / orderable:** none / `created_at`.
- **The documented exception:** an account with no rubric override yet has
  no row to read `id`/`version` from, and `GET`/`update` still need one to
  answer with. `get`/`list` render a synthetic default in that case: `id =
  "rub_default"`, `version = 0`, `updated_by = "system"`, `updated_at` = the
  moment of the request — not a fact (nothing happened at a fixed moment
  for an account with no override), documented as the floor-not-fact
  §5(b) already names for a different case. The moment an account's first
  `update` lands, `get`/`list` switch to the real row's own id/version —
  minted by `memory_store.set_rubric_override` — and `rub_default` 404s.

## artifact

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
