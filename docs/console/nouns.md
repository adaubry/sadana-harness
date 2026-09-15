# Nouns

One section per console-owned-by-harness noun. Each is filled in by the work
item that actually serves it, with: fields, states, actions, filterable,
orderable, `search_doc`, and — where they differ — "what the console's
prompt says" beside "what the box returns". A noun with no work item listed
yet is not served by this box; asking for it is `404 NOT_FOUND` at the
routing step, never a stub response.

## harness

Served by: H19, H30. Singleton — `GET /v1/harness`, never a plural collection.

- **Fields:** `id`, `version` (`sadana.__version__`), `capabilities`,
  `tether` (live: `disconnected`/`connecting`/`connected`, from the
  tether's own connection state — never a hardcoded value), `connected_at`
  (present only while connected), `org`, `ledger_head`, `leaves_the_box`.
- **States:** none — `harness` carries no `state` field, and none of its
  three actions is state-gated (each declares `from_states=()`).
- **Actions:**
  - `upgrade` — scope `harness:upgrade`, capability `upgrade` (declared).
    Body: `{"version": "<tag>"}`. Validates the tag, promotes a `running`
    operation naming the target, launches `scripts/upgrade.sh` detached,
    and returns that operation as a real `202` — the box then checks out
    the tag, reinstalls, and restarts its own service; `door/operations
    .resume_on_start` resolves the operation to `succeeded`/`failed` on the
    next boot by comparing `sadana.__version__` against the recorded
    target.
  - `deregister` — scope `harness:deregister`, no capability (not in the
    closed sixteen — scope-gated only). Stops the tether, deletes the
    box's local identity and private key, tombstones this noun in the
    ledger. Every other table is untouched: the data is the customer's.
  - `purge-account` — scope `harness:purge`, no capability, consequential.
    Body: `{"account_key": "<key>"}`. Deletes every row `stores
    .PURGE_STEPS` names for that account (memory, persona selection,
    schedules, conversation ownership) — never the box's own identity, and
    never conversation/message content itself, which is not one account's
    own data.
- **Filterable / orderable:** neither — there is exactly one `harness` and
  no list endpoint for it.
- **`search_doc`:** `{"title": "Harness <id>"}` — present for protocol
  uniformity; nothing in H19 ever writes a `harness`-noun ledger row for it
  to render from (`deregister`'s own tombstone, H30, is the first).
- **Where it differs from the console's prompt:** the exact `upgrade`/
  `deregister`/`purge-account` body shapes above are this box's own fix,
  per `docs/console/wire.md`'s own note that where the console's prompt is
  silent on a field name, this box's own documentation settles it.

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

Served by: H27.

- **Fields:** `name`, `cron`, `timezone`, `interval_seconds` (read-only —
  never set through the door; carried over only from a migrated legacy
  `scheduled_triggers` row), `trigger_text`, `plugin_id?`, `conversation_id?`,
  `next_run_at`, `last_run_at?`, `last_state?`.
- **States:** `active` ⇄ `paused`.
- **Actions:** `pause` (`active`→`paused`, capability `schedules.write`);
  `resume` (`paused`→`active`, capability `schedules.write`) — recomputes
  `next_run_at` from the moment of resume, never from whatever it showed
  while paused.
- **Filterable / orderable:** `state`, `plugin_id`, `created_at` /
  `created_at`.
- **`search_doc`:** `{title: name, subtitle: cron ?? "every {interval_seconds}s",
  facets: {state, plugin}}`.
- **Where it differs from the console's prompt:** `create`/`update` accept
  only `cron` for recurrence — there is no way to create or convert a
  schedule to interval-based timing through the door; `interval_seconds`
  exists solely so a schedule migrated from the pre-H27
  `scheduled_triggers` table keeps firing exactly as it did before, and it
  stops being consulted the moment that row is given a `cron` through an
  `update`. `plugin_id`/`conversation_id` are not resolved ledger ids —
  no `plugins` noun exists yet to mint one — they are the plugin's own
  registered `name` and the conversation's own `key`, validated for
  existence and rendered back unchanged (the same convention
  `memory_entries`'s own `conversation_id` field already uses). `remove` is
  a real, permanent delete (ledger-tombstoned, not a third wire-visible
  state) and, like `create`/`update`, requires `schedules.write`. Scoped to
  the acting account on every verb: another account's schedule is `404`,
  never `403`.

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
