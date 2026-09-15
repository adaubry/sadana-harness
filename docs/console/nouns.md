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

Served by: H24.

- **Fields:** `name`, `version` (the wire-standard integer version — see
  "Where it differs"), `source_repo?`, `source_tag?`, `description`,
  `declared_settings[{key, type: string|number|boolean|secret, required,
  description}]`, `steps_count`, `needs_code_count`, `layout` (`get` only,
  read-only: `nodes[{id, kind, x, y, w, h, label, needs_code,
  external_refs[]}]`, `edges[{from, to}]`).
- **States:** `installed` | `disabled` | `error`.
- **Actions:** `disable` (`installed`→`disabled`, capability
  `plugins.write`); `enable` (`disabled`→`installed`, capability
  `plugins.write`); `install-from-git` (`{repo_url, tag}`, capability
  `plugins.install`, consequential — re-targets an already-known plugin at a
  new tag); `save` (`{manifest}`, capability `plugins.write`, consequential
  — validation errors come back as `Problem.errors[]` with `field` shaped
  `nodes.<name>.<field>` when the bad manifest text names a step and a known
  field, `detail` alone otherwise); `set-settings` (H14; answers
  `HARNESS_CAPABILITY_MISSING` naming `settings.write` until that lands).
- **Filterable / orderable:** `state`, `name`, `source_repo` /
  `created_at`.
- **`search_doc`:** `{title: name, subtitle: description, facets: {state,
  source_repo}}`.
- **Where it differs from the console's prompt:** `create` **is**
  `install-from-git` — a plugin being installed has no id yet, so there is
  no action route to reach it through; the action form is also accepted on
  an existing plugin's own `/v1/plugins/{id}/actions/install-from-git`, to
  re-target it at a new tag. `version` is the standard wire optimistic-lock
  integer, not the plugin's own declared semantic-version string
  (`plugin.toml`'s `[plugin] version`), which this noun does not separately
  expose. `declared_settings`/`steps_count`/`needs_code_count`/`layout` are
  read through `editor_server.assess()`'s `check_bodies=False` posture, not
  `plugin_manifest.discover_plugins()` — found while building this: the
  latter excludes a plugin *entirely* the moment any one step still needs
  code, which is exactly the state this surface exists to show. A promoted
  `create`/`install-from-git` operation carries `resource: null`, the same
  documented gap `docs/reference/console_fit_plan.md` §6 already names for
  `messages.create` — poll the operation, then find the new plugin's id
  through `/v1/changes`.

## workflow

Served by: H24.

- **Fields:** `name` (= `entry.tool`), `node_count`.
- **States:** none.
- **Actions:** none — `create`/`update`/`remove`/every action answer
  `HARNESS_CAPABILITY_MISSING`.
- **Filterable / orderable:** `name` / `created_at`.
- **`search_doc`:** `{title: name, subtitle: "<node_count> steps"}`.
- **Where it differs from the console's prompt:** not a row — its id is a
  pure function of `(plugin directory name, entry.tool)`
  (`"wfl_" + sha256(...)[:32]`), the same documented exception
  `agent_template`'s own id already takes above. `node_count` counts the
  plugin's *whole* declared graph, not the subset reachable from this one
  entry — no console screen this design answers needs the narrower count,
  and computing it would be a strictly heavier step than this design
  already pays for elsewhere (`editor_layout`'s own reachability walk).

## node

Served by: H24.

- **Fields:** `kind`, `needs_code`, `external_refs[]`, `body_preview?` — the
  standard `name` field doubles as the step's own label; nothing separate
  is rendered.
- **States:** none.
- **Actions:** none. `update({label})` — not an action — renames the step
  (`plugins.rename_node`, following every arrow, route port and entry that
  named it), capability `plugins.write`.
- **Filterable / orderable:** `kind`, `needs_code` / `created_at`.
- **`search_doc`:** `{title: name, subtitle: kind, facets: {kind}}`.
- **Where it differs from the console's prompt:** nested under a
  `workflow`, but the nodes listed under one are the *owning plugin's whole
  graph*, not a subset reachable from that one entry — the same reading
  `workflow.node_count` already takes, for the same reason. `needs_code`
  here is `editor_server.waiting()`'s own signal — a `body`/`skill`
  reference that is *named* but does not resolve on disk — a different,
  stronger question than `inspection.declared_shape.nodes[].needs_code`'s
  purely structural check (which never touches disk, since an inspected
  tag's clone is already discarded by the time this runtime ever forms an
  answer).

## tool

Served by: H24. Read-only.

- **Fields:** `name`, `description`, `plugin_id`.
- **States / actions:** none — every write verb and action answer
  `HARNESS_CAPABILITY_MISSING`.
- **Filterable / orderable:** `name`, `plugin_id` / `created_at`.
- **`search_doc`:** `{title: name, subtitle: description, facets: {plugin:
  plugin_id}}`.
- **Where it differs from the console's prompt:** over this harness's
  currently *enabled* tool surface (`client_surface.enabled_plugin_set()`)
  — a disabled plugin's tools never appear here, matching the real turn
  path's own tool surface exactly. `plugin_id` is the owning plugin's
  *directory* name (its stable key in `plugin_state`), not a separately
  minted resource id.

## inspection

Served by: H24. How the console's own plugin registry asks this harness to
look at a git tag before anyone installs it — never runs anything from it.

- **Fields:** `repo_url`, `tag`, `revision`, `declared_shape: {name,
  version, description, entries[], nodes[{id, kind, needs_code,
  external_refs[]}], settings[], permissions_requested[]}` (`null` when
  `state` is `failed` and nothing parsed), `checksum` (sha256 of the
  canonical `declared_shape` JSON; `null` when `declared_shape` is), `error?`.
- **States:** `ok` | `failed`.
- **Actions:** none — `create({repo_url, tag})` (an operation; `plugins.inspect`)
  is the only write, and `get` reads the settled row back. No `update`/
  `remove`/action.
- **Filterable / orderable:** `state`, `repo_url`, `tag` / `created_at`.
- **`search_doc`:** `{title: "<repo_url>@<tag>", subtitle: state, facets:
  {state, repo_url}}`.
- **Where it differs from the console's prompt:** rows older than 7 days
  are deleted on the first write of each new day (`door/idempotency.py`'s
  own hour-grained sweep, one grain coarser here) — not a background job,
  and not guaranteed exact to the hour. `declared_shape.permissions_requested`
  is always `[]`: nothing in this harness's manifest model
  (`plugins.Manifest`) captures a requested permission yet: the field is
  still rendered, never omitted, so a console reading this contract never
  has to special-case its absence. `declared_shape.nodes[].needs_code` is
  computed structurally from the manifest alone — does the node's own
  declared fields even name what its kind requires (`body` for
  `compute`/`call`/`route`, `skill` for `ask`) — never by reopening the
  clone `marketplace.inspect_tag()` already discarded; a body or skill that
  is named but does not actually resolve is caught only once the tag is
  installed, through the `plugin` noun's own `layout`.

## provider

Served by: H14. One row per name this box's own model-provider registry
declares wired (a real `request_fn`) — never a row a person creates.

- **Fields:** `name` (read-only, from the registry), `model`, `base_url?`,
  `credential_ref?`.
- **States:** none — a provider either exists in the registry or it does
  not; there is no in-between state to render.
- **Actions:** none. `create`/`remove` are both declined
  (`HARNESS_CAPABILITY_MISSING`): a provider's existence is a fact about
  the box's own installed code, not something a console call can add or
  take away.
- **Filterable / orderable:** `name` / `created_at`.
- **`search_doc`:** `{title: name, facets: {model}}`.
- **Where it differs from the console's prompt:** `model`/`base_url`/
  `credential_ref` live in `config.toml` under `[providers.<name>]`, read
  fresh on every call — a `PATCH` takes effect on the next turn, no
  restart. `credential_ref` is validated against the `secret` noun's own
  existence check (a name, never an id) before being accepted; naming one
  that resolves nowhere is `400 VALIDATION`. `id`/`created_at`/`version`
  are minted the first time this box is ever asked about a given provider
  — a floor, not a fact, the same posture `console_fit_plan.md` §5(b)
  already documents for a legacy row filled in at first open.

## budget

Served by: H14. The box's own conversation budget — one row, box-wide, not
one per account or per conversation.

- **Fields:** `iterations_max`, `wall_clock_seconds`, `runs_per_day`
  (read-only; the console's own plan quota — this box has no opinion about
  it and always reports `null`).
- **States:** none.
- **Actions:** none. `create`/`remove` are declined: there is nothing to
  create or delete, only a value to set, the same posture
  `memory_policies` (H26) already takes for an identical shape.
- **Filterable / orderable:** none / `created_at`.
- **`search_doc`:** `{title: "Budget", facets: {iterations_max}}`.
- **Where it differs from the console's prompt:** `list`/`get` render a
  synthetic row (`id = "bdg_default"`, `version = 0`) until the first
  `PATCH`, which mints a real row — `If-Match` against the synthetic
  version (`"0"`) is what a first-ever write presents. A `PATCH` naming
  `runs_per_day` is `400 VALIDATION`, never silently ignored.

## integration

Served by: H14. The box's own inbound webhook — one row, box-wide.

- **Fields:** `webhook_url` (read-only — where a plugin's external answer
  is posted; the box's inbound webhook address when bound),
  `webhook_secret_ref`, `gateway_state` (read-only).
- **States:** none.
- **Actions:** none — `create`/`remove` declined, the same reason
  `budget` declines them.
- **Filterable / orderable:** none / `created_at`.
- **`search_doc`:** `{title: "Integration", facets: {gateway_state}}`.
- **Where it differs from the console's prompt:** `webhook_url` and
  `gateway_state` are computed at read time — `webhook_url` from
  `gateway.webhook_bind`/`.webhook_port`, `gateway_state` (`"running"` |
  `"stopped"`) from a non-blocking probe of the same lock file
  `gateway_daemon.run()` itself takes — never stored, so neither can go
  stale against what is actually true. `webhook_secret_ref` is validated
  against the `secret` noun's own existence check, exactly like
  `provider.credential_ref`. The same synthetic-until-first-write row shape
  as `budget` applies here too.

## secret

Served by: H14. Write-only, referenced by name everywhere else in this API.

- **Fields:** `name`, `kind`. Never `value` — on any verb, on any state.
- **States:** none.
- **Actions:** none.
- **Filterable / orderable:** `name` / `created_at`.
- **`search_doc`:** `{title: name, facets: {kind}}`.
- **Where it differs from the console's prompt:** the standard grammar is
  used as-is — `POST /v1/secrets {name, value, kind?}` creates (a real
  minted id, `name` immutable after), `PATCH /v1/secrets/{id} {value?,
  kind?}` rotates the value and/or `kind`. Not the shape a first sketch of
  this noun proposed (`PUT /v1/secrets/{name}`), which has no counterpart
  in this door's actual six-verb, id-addressed grammar and would have
  reopened that shared framework for one noun. `list`/`get` add a
  `fingerprint` (last four characters of the value, a middle dot, the
  first eight hex characters of its SHA-256) computed live from
  `state_dir/.env` on every call — never stored, never stale. `remove`
  hard-deletes the row and the `.env` line; the tombstone this project's
  own removal pattern leaves is the ledger's own permanent `deleted` row,
  not a lingering `state` kept here. A name a `credential_ref`/
  `secret_ref` elsewhere in this API names need never have been created
  through this noun at all — a real, already-exported environment variable
  satisfies the reference exactly as well, since the environment always
  wins.
