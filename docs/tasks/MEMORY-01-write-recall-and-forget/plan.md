# Plan: You shouldn't have to reintroduce yourself every time (from intent.md 2026-09-10)

## Files that change

1. `src/sadana/memory.py` (new, pure) — `AccountKey`, `MemoryEntry`,
   `render_recall()`, `render_capture_guidance()`, `account_key_for()`,
   `default_rubric()` (reads `SADANA_MEMORY_DEFAULT_RUBRIC` via
   `config.env`).
2. `src/sadana/memory_store.py` (new, I/O — mirrors `observability.py`'s
   posture) — `DispatchContext(account_key, conn)`, `ensure_schema()`,
   `write_entry()` (UPSERT), `delete_entry()`, `list_entries()`,
   `get_rubric_override()`, `set_rubric_override()`, and
   `ensure_plugin_seeded(plugins_root)` (copies
   `src/sadana/builtin_plugins/memory/` into `plugins_root / "memory"` if
   not already present — the same "if missing, write the default" idiom
   `persona.py:load_or_seed_persona` already established for one file,
   extended to a directory tree via `shutil.copytree`).
3. `src/sadana/builtin_plugins/memory/plugin.toml` (new) — one entry
   `memory.remember`, one `call` node `write`.
4. `src/sadana/builtin_plugins/memory/schema/remember.json` (new) —
   `{entry_key: string, content: string}`, both required,
   `additionalProperties: false`.
5. `src/sadana/builtin_plugins/memory/init.py` (new) — `write_entry(value)`
   reading `value["_sadana_memory_ctx"]` (a `memory_store.DispatchContext`),
   `value["entry_key"]`, `value["content"]`.
6. `src/sadana/plugin_dispatch.py` (modified) — `build_dispatch()` gains one
   new **last** keyword-only parameter,
   `memory_context: memory_store.DispatchContext | None = None`; inside
   `dispatch()`, when set, calls `run_graph()` with
   `{**arguments, "_sadana_memory_ctx": memory_context}` (trusted value
   merged last, always wins over anything the model supplied under that
   key). No other line in this file changes.
7. `src/sadana/subcommands/memory.py` (new) — `sadana memory list
   <account>`, `forget <account> <entry_key>`, `set-rubric <account>
   <text>`. Same one-file-owns-parser-and-handler shape as
   `subcommands/runs.py`.
8. `src/sadana/cli.py` (modified) — import and call
   `build_memory_parser(subparsers)`, alongside the seven existing
   registrations.
9. `src/sadana/subcommands/chat.py` (modified) — new `--account` flag
   (default `config.env("SADANA_MEMORY_ACCOUNT", "local")`); at
   `cmd_chat`, before building the template: `memory_store.ensure_schema(conn)`,
   `memory_store.ensure_plugin_seeded(plugins._plugins_root())`; at the
   `create_conversation()` branch, fold `memory.render_recall(...)` +
   `memory.render_capture_guidance(...)` into `system_message` (was `""`);
   in `_chat_loop`, pass `memory_context=memory_store.DispatchContext(account_key, conn)`
   into `plugin_dispatch.build_dispatch(...)` — every turn, both the new-
   and resumed-conversation branches.
10. `src/sadana/gateway_dispatch.py` (modified) — same shape as (9), with
    `account_key = memory.account_key_for(event.platform, event.chat_id)`
    instead of a CLI flag.

Tests:

11. `tests/unit/test_memory.py` (new) — pure functions.
12. `tests/unit/test_memory_store.py` (new) — sqlite CRUD, natural-key
    upsert, cross-account isolation, `ensure_plugin_seeded` idempotency.
13. `tests/unit/test_builtin_plugin_memory.py` (new) — the shipped
    `plugin.toml` validates via `plugin_manifest.validate()`; `run_graph()`
    against it with a hand-built `DispatchContext` really inserts a row
    (via `memory_store.list_entries` afterward); a missing/malformed
    `_sadana_memory_ctx` yields a `failed_node` result, not a raised
    exception.
14. `tests/unit/test_plugin_dispatch.py` (modified) — a model-supplied
    `_sadana_memory_ctx` in `arguments` is overwritten by the trusted one
    passed via `memory_context=`; `memory_context=None` (the default)
    leaves `arguments` byte-for-byte as today.
15. `tests/unit/test_subcommands_memory.py` (new) — list/forget/set-rubric,
    including the "nothing stored" message.
16. `tests/unit/test_subcommands_chat.py` (modified) — a new conversation
    for an account with existing entries carries them in its
    `system_prompt`; a different account's does not; check no existing
    test in this file hardcodes an exact `system_prompt`/hash that this
    change would silently invalidate.
17. `tests/unit/test_gateway_dispatch.py` (modified) — two different
    `thread_id`s under the same `chat_id` (same account) share recall;
    a different `chat_id` (different account) does not.

## Order of work

1. `memory.py` + `test_memory.py`. No dependencies; fully testable alone.
2. `memory_store.py`'s sqlite half (`ensure_schema`, `write_entry`,
   `delete_entry`, `list_entries`, `get_rubric_override`,
   `set_rubric_override`) + the sqlite parts of `test_memory_store.py`.
   Depends on (1) for `MemoryEntry`/`AccountKey`; uses `conftest.py`'s
   `open_conn()`.
3. `builtin_plugins/memory/*` + `memory_store.ensure_plugin_seeded()` +
   the seeding half of `test_memory_store.py` + `test_builtin_plugin_memory.py`.
   Depends on (2) for `write_entry`. Proves the plugin works via
   `run_graph()` directly, with a hand-built `DispatchContext` — before
   anything in `plugin_dispatch.py` changes, so a bug here is isolated to
   the plugin itself.
4. `plugin_dispatch.py`'s `memory_context` parameter + additions to
   `test_plugin_dispatch.py`. Depends on (2) and (3). **Most risky step**
   (see Risks) — lands only once (3) has already proven the plugin correct
   in isolation, so this step's own tests are specifically about the merge-
   last security property, not plugin logic.
5. `subcommands/memory.py` + `cli.py` registration + `test_subcommands_memory.py`.
   Depends only on (2). Low risk; unblocks manual end-to-end poking
   (`sadana memory list <account>`) before the harder integration below.
6. `chat.py` wiring + `test_subcommands_chat.py` additions. Depends on
   everything above.
7. `gateway_dispatch.py` wiring + `test_gateway_dispatch.py` additions.
   Same shape as (6), lands after (6) proves the pattern once.
8. Self-check: `/ponytail-review` and `/simplify` against the whole diff,
   then `make verify`.

## Risks

One fact surfaced only at this stage, not addressed in spec.md:
`plugins._plugins_root()` defaults to a **runtime state directory**
(`state_dir/plugins`), populated only by `plugin install` or by hand —
there is no existing notion of a plugin shipped inside the repo itself and
auto-discovered. Every design decision in spec.md (the one-node plugin, the
identity channel, the two stores) still holds; step 3 above only adds *how
the plugin's own files get onto disk*, via `ensure_plugin_seeded()`.

**What could this change break?**

- `plugin_dispatch.build_dispatch()`'s signature. Existing callers
  (`chat.py`, `gateway_dispatch.py`, `eval_harness.py`, existing
  `test_plugin_dispatch.py` tests) must keep working with no change.
  Mitigation: `memory_context` is added as the **last** keyword-only
  parameter, defaulting to `None`; nothing existing is reordered or made
  positional-sensitive.
- `create_conversation()`'s `system_message=""` call sites. Once non-empty,
  every **newly created** conversation's `system_prompt`/`prompt_sha256`
  changes — this never touches an already-stored conversation (loaded
  verbatim via `conversation_store.load()`, hash never recomputed), but any
  existing test in `test_subcommands_chat.py`/`test_gateway_dispatch.py`
  that asserts an exact system prompt or hash for a **new** conversation
  needs updating. Checked explicitly in steps 6 and 7.
- The plugin catalog/tool surface gains one more entry
  (`memory.remember`) once the plugin is seeded and discovered. Checked in
  steps 6/7 for any existing test asserting an exact tool count or exact
  tool set for chat.py/gateway_dispatch.py's default template
  (testing-conventions' change-detector warning).

**Most risky step, and why:** step 4, the identity-channel merge. A bug
here is a security bug (cross-account leakage: requirement 6), not merely a
functional one — the trusted `_sadana_memory_ctx` must always win over
anything the model supplied under that key. It is ordered after step 3
specifically so its own tests isolate the merge behavior from whether the
plugin's own logic is correct, which step 3 already proved independently.

**Drift check against spec.md's Rejected alternatives:** re-read before
writing this plan. Not drifting toward: the judge-then-write DAG (still one
node), a kernel `Recorder`-style hook for capture (still a plugin), a fixed
memory-type taxonomy (still one `entry_key`+`content` shape), or folding the
account's rubric override into the plugin's own shipped file (still a
separate DB row via `set_rubric_override`). The `builtin_plugins/` seeding
mechanism is new ground spec.md simply didn't address (an omission, not a
rejected alternative) — it doesn't change any requirement, interface, or
rejected-alternative decision, only how the already-designed plugin's files
reach disk.

## Proof

- `test_memory.py`: `render_recall(())` == `""`; non-empty entries render
  one line each; `render_capture_guidance` includes the default always and
  the override only when non-empty, override never replacing the default
  text; `account_key_for` drops any thread component and is stable for the
  same `(platform, chat_id)`; `default_rubric()` honors
  `SADANA_MEMORY_DEFAULT_RUBRIC` and falls back otherwise.
- `test_memory_store.py`: a second `write_entry` with the same
  `(account_key, entry_key)` updates the row (row count unchanged, content
  changed) — proves the primary key, not application logic, enforces
  uniqueness; `list_entries` for account B never includes account A's rows;
  `delete_entry` removes a row and a following `list_entries` omits it;
  `get_rubric_override` is `""` before any `set_rubric_override` call and
  reflects it after; `ensure_plugin_seeded` against a tmp `plugins_root`
  materializes `plugin.toml`/`schema/remember.json`/`init.py`, and calling
  it twice does not error or duplicate anything.
- `test_builtin_plugin_memory.py`: `plugin_manifest.validate(builtin_dir)`
  is `Valid`; `run_graph(builtin_dir, manifest, entry, {"entry_key": ...,
  "content": ..., "_sadana_memory_ctx": DispatchContext(...)}, ask=stub,
  approve=lambda *_: True)` results in exactly one row in `memory_entries`
  for that account, verified via `memory_store.list_entries`; the same call
  with `_sadana_memory_ctx` missing produces a `DagResult` with
  `failed_node` set, not a raised exception out of `run_graph`.
- `test_plugin_dispatch.py`: a fake plugin whose one node's body returns
  `value` verbatim, dispatched with `memory_context=DispatchContext(...)`
  and model arguments that themselves contain a `_sadana_memory_ctx` key —
  asserts the value the body actually saw is the trusted context object,
  not the model-supplied one; a second test with `memory_context=None`
  asserts `arguments` reaches the body completely unchanged from current
  behavior.
- `test_subcommands_memory.py`: `list` on an account with two entries
  prints both; `forget` removes one and a following `list` shows only the
  other; `set-rubric` persists and is reflected by a direct
  `get_rubric_override` call; an account with nothing stored prints a
  "nothing" message (mirrors `cmd_runs`'s "No recorded runs.").
- `test_subcommands_chat.py`: a new conversation created for an account
  with pre-existing entries has them present in `system_prompt`; the same
  setup for a different account does not.
- `test_gateway_dispatch.py`: two inbound events with the same `chat_id`
  but different `thread_id`s (two conversations, one account) both recall
  the same stored entry; an event with a different `chat_id` does not.
- `make verify` run at the end, full output pasted, ending "VERIFY OK".
