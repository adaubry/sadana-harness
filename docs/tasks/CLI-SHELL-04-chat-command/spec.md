# Spec: Actually talking to the agent

Intent: docs/tasks/CLI-SHELL-04-chat-command/intent.md

## Requirements

1. `sadana chat` starts a real, live back-and-forth: type something, get
   a real reply, keep going, then stop. (Intent §Proposed outcome.)
2. What kind of assistant it is comes from one plain, editable
   description with a sensible built-in default. Editing it shapes
   conversations started after the edit; one already running keeps what
   it started with. (Intent §Proposed outcome, §Constraints, and the
   design-stage correction recorded in intent.md's own trace.)
3. Before anything reaches outside on its own initiative, it asks
   plainly and only proceeds on a clear yes. (Intent §Proposed outcome.)
4. Everything said, both directions, is durably recorded as it happens.
   (Intent §Proposed outcome.)
5. A conversation can be picked back up later by its exact name, or a
   fresh one started instead. (Intent §Proposed outcome, §Constraints.)
6. Which provider/model answers is read from a setting made elsewhere
   by default; a single run can override it. No automatic choice among
   several is invented. (Intent §Constraints.)
7. Reachable only by a person at a terminal directly — no messaging
   platform, no other entry point. (Intent §Constraints.)

## Design

**Reference corpus, audited rather than copied.** hermes's closest analogue is its interactive `chat` engine (`cli.py`'s
`HermesCLI`) plus `agent/system_prompt.py`'s prompt-building and
`hermes_cli/default_soul.py`'s seeded persona file. Two things were
checked in the actual code, not assumed from a comment, and one of them
overturned an assumption already written into `intent.md`:

- **What actually gets adopted**: a plain, built-in default persona
  string, seeded once into an editable file, is a real and reusable
  idea (`hermes_cli/default_soul.py`'s `DEFAULT_SOUL_MD`). The prompt
  text itself is hermes-brand-specific and is not copied; a new,
  sadana-appropriate default string is written for this item.
- **What was checked and found to contradict a comment**: a comment
  inside hermes's own SOUL.md template claims the file is "loaded fresh
  each message — no restart needed." The actual code that builds the
  live prompt, `agent/system_prompt.py`'s `build_system_prompt()`, says
  otherwise in its own docstring: *"Called once per session (cached...)
  and only rebuilt after context compression events. This ensures the
  system prompt is stable across all turns in a session, maximizing
  prefix cache hits."* `invalidate_system_prompt()` is the explicit,
  narrow trigger for a rebuild — not "every message." This is, in fact,
  the same shape sadana's own `conversation.py` already built
  (`prompt_epoch`/`prompt_sha256`/`rotate_prompt()`, today used only for
  context compression) — hermes's real behavior and sadana's existing
  architecture agree with each other; only a stale comment in hermes's
  own tree disagreed with both. `intent.md` is corrected to match (see
  its own "Changed during planning" trace): **the persona file is read
  once per conversation** (at `create` or at `--resume` load), never
  live-reloaded mid-conversation. This avoids inventing a second
  rotation reason, a `stable_prompt_len` recompute, and a new
  `PromptRotationReason` member for a behavior the reference material
  doesn't actually have either.
- **Approval needs nothing new at all**: `plugin_manifest._default_approve`
  (`src/sadana/plugin_manifest.py:257`) is already the real terminal
  prompt — its own docstring: *"the real behavior for this project's
  one interactive caller today: a person at a WSL terminal, asked
  synchronously and blocked on until they answer."* It asks by naming
  the plugin and node, blocks via `asyncio.to_thread(input, ...)`, and
  fails closed on anything but `y`/`yes`. It is already the default
  `approve=` value `plugin_dispatch.build_dispatch()` falls back to.
  **`sadana chat` simply never overrides it** — every existing caller
  that overrides it (the proof scripts) does so specifically to
  auto-approve for a non-interactive run; this is the one caller that
  should not. This also resolves intent.md's own open question (ask
  every time vs. remember a prior yes): the real function already
  chosen for reuse asks every time and remembers nothing — that is
  inherited, not decided fresh here.

**Where this lives.** `src/sadana/subcommands/chat.py` (new), following CLI-SHELL-03's
established shape: one file, owning both `build_chat_parser(subparsers)`
and `cmd_chat(args) -> int`. `cli.py`'s `build_parser()` gains one more
`build_chat_parser(subparsers)` call, same pattern as
`build_conversations_parser`.

**The persona file.** A new small pair of functions, `persona_path_from_config() -> Path` and
`load_or_seed_persona(path: Path) -> str`, in `chat.py` — this is the
one piece of new state this item introduces (a file on disk), so it
gets named plainly rather than buried inline:

- Path: `config.get_paths().config_dir / "persona.md"`, overridable via
  `config.env_path("SADANA_CHAT_PERSONA_PATH", default=...)` — same
  override convention `conversation_store.store_path_from_config()`
  already established for exactly this reason (CLAUDE.md: "know which
  config loader you are inside").
- Seeding: if the file doesn't exist, its parent directory is created
  and a plain, sadana-appropriate default string (written for this
  item, not copied from hermes's branded text) is written to it, then
  read back. If it exists, it's read as-is. One function, no branch the
  caller has to think about — "read the persona" always returns
  something, seeding is an implementation detail of the read.

**Provider and model.** `config.env("SADANA_MODEL_ACCESS_PROVIDER", default="openrouter")` and
`config.env("SADANA_MODEL_ACCESS_MODEL", default="deepseek/deepseek-v4-flash-0731")`
— the exact pair every existing proof script already hardcodes, and
(per `model_access.py:311`'s own comment) the *only* pair sadana's
context-window table currently resolves at all. `--provider`/`--model`
flags on the subcommand override these for a single run — plain
`argparse` string arguments, no new resolution logic, matching
`cli_shell_blueprint.md` §4.2's ruling that multi-provider
fallback/resolution stays MODEL-ACCESS's job, untouched here.

**Boundary validation, and why it's not "new error handling" this
block already declined elsewhere.** `model_access.get_provider(name)`
(`model_access.py:235`) already raises a named `UnknownProvider` for an
unregistered name — and nothing between it and `run_turn` catches that,
nor `ProviderNotWired` (`model_access.py:97,254`, raised when a
registered provider has no wired `request_fn`). Left alone, either
would surface as a raw traceback on the very first turn. CLI-SHELL-01/03
each declined adding error handling for a store that fails to open,
reasoning that a raw exception there is a rare, genuinely infrastructural
failure with no better story to tell. A mistyped `--provider` is a
different kind of failure: an ordinary, expected argv-shaped mistake —
squarely CLI-SHELL's own job per its own blueprint (§4.1's exit-code/
error-formatting concerns), and squarely "validate at a system boundary"
(CLAUDE.md's general engineering guidance), not "add error handling for
an internal failure." `cmd_chat` catches exactly
`(model_access.UnknownProvider, model_access.ProviderNotWired)` around
the first turn-taking call and prints a clear message with a nonzero
exit — reusing the two names `model_access.py` already defined for
exactly this, not inventing a new validation pass.

**The store, and a real integration gap this item closes.** `conversation_store.py` already has everything: `create()`, `load()`,
`bind_persist()`. But nothing before this item has ever called
`bind_persist()` across more than one turn, and reading its own
docstring closely against the same discipline
`docs/reference/dispatch_closure_state_bug.md` already established for
`build_dispatch()` surfaces the same shape of gap: `bind_persist()`
snapshots `conversation`'s *other* fields (budgets, `next_turn_seq`,
`context_state`) once, at bind time, stripped only of `messages`, which
is the one field it re-reads fresh on every call. That's exactly right
*within* one turn (CONV-08's own Non-goals explicitly scope
iteration-level, mid-turn budget durability out — only messages need to
land before a tool runs). It is not right *across* turns: reusing one
`bind_persist()` closure for a whole session would keep persisting the
*first* turn's budget/`next_turn_seq`/`context_state` forever, even as
messages correctly grow. The loop below therefore:

1. builds a fresh `persist = conversation_store.bind_persist(conn,
   conversation, now=now)` **before every turn** — the same "never
   reused across turns" discipline `build_dispatch()` already documents,
   applied to the sibling closure that has the identical shape of risk;
2. calls `conversation_store.save(conn, conversation, now=now)`
   **once more, explicitly, after** `take_turn_and_reconcile` returns —
   this is what actually durably captures that turn's final budget/
   `next_turn_seq`/`context_state`, which the in-turn `persist` calls
   never do. `save()`'s `INSERT OR IGNORE` on messages
   (`conversation_store.py:177-185`) makes this second write idempotent
   with whatever the in-turn calls already landed — no duplicate rows,
   a small amount of repeated I/O against a scale (one interactive
   human, one local sqlite file) where that cost is not worth avoiding.

**The conversation's own name.** CLAUDE.md: "anything long-lived a user returns to should be addressed
by a unique natural key." `--key NAME` names a new conversation
explicitly; if omitted, a timestamp-based default
(`f"chat-{int(time.time())}"`) is generated so a quick, throwaway chat
needs no ceremony, while anything meant to be found again can still be
given a real name. `create()`'s existing `ConversationAlreadyExists` is
left to surface as-is on a collision (rare for a single interactive
human; the existing exception is not translated into something new).
`--resume KEY` loads instead of creates, by the conversation's exact
already-saved key — `--key` and `--resume` together is a contradiction
(naming a *new* conversation while also resuming an old one) and is
rejected with a clear message and nonzero exit before anything else
runs.

**The loop itself.**

```
persona = load_or_seed_persona(persona_path_from_config())
plugin_set = plugin_dispatch.build_plugin_set(plugin_manifest.discover_plugins())
now = time.monotonic()

if args.resume:
    conversation = conversation_store.load(conn, args.resume, now=now)
else:
    template = ConversationTemplate(
        name="chat",
        recipe=TemplateRecipe(stable_prompt=persona, catalog=plugin_set.catalog, tool_specs=plugin_set.tool_specs),
    )
    conversation, _ = create_conversation(template, key, system_message="", iteration_budget=iteration_budget_from_config(), wall_clock_budget=wall_clock_budget_from_config(now))
    conversation_store.create(conn, conversation, now=now)

loop:
    user_input = input("> ")            # EOFError (Ctrl+D) ends the loop, exit 0
    now = time.monotonic()
    dispatch, tracker = plugin_dispatch.build_dispatch(conversation, plugin_set, stable_prompt=persona, provider=provider, model=model, now=now)
    persist = conversation_store.bind_persist(conn, conversation, now=now)
    result, conversation = await plugin_dispatch.take_turn_and_reconcile(conversation, dispatch, tracker, user_input=user_input, provider=provider, model=model, now=now, persist=persist)
    conversation_store.save(conn, conversation, now=now)
    print(result.final_text or f"[{result.exit_reason.value}] {result.detail or ''}")
    if result.exit_reason is not ExitReason.COMPLETED:
        return 1   # something ended the session other than the person choosing to
```

`cmd_chat` is a plain sync function (`-> int`, per the CLAUDE.md exit-
code rule) that wraps this async loop in one `asyncio.run(...)` call —
the same sync/async boundary `tests/unit/test_conversation_store.py`'s
own `_run_turn` helper already crosses the same way. `KeyboardInterrupt`
(Ctrl+C) is caught around the `asyncio.run()` call, prints a short
line, and returns `130` (the conventional shell exit code for a
SIGINT-terminated process) — a small, standard courtesy, not new
error-handling machinery.

Approval is not shown in the sketch above because nothing calls it
directly: it flows in as `build_dispatch()`'s own default `approve=`
parameter, simply never overridden (§Design, above).

## Interface

```python
# src/sadana/subcommands/chat.py
def build_chat_parser(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None: ...
def persona_path_from_config() -> Path: ...
def load_or_seed_persona(path: Path) -> str: ...
def cmd_chat(args: argparse.Namespace) -> int: ...
```

- `build_chat_parser`: registers `"chat"` with `--resume KEY`,
  `--key NAME`, `--provider NAME`, `--model NAME` (all optional,
  `default=None`), `set_defaults(func=cmd_chat)`.
- `persona_path_from_config`: `config.get_paths().config_dir /
  "persona.md"`, or `SADANA_CHAT_PERSONA_PATH` if set.
- `load_or_seed_persona`: returns the file's text, creating it (parents
  included) with a built-in default first if it doesn't exist yet.
  Never raises for "doesn't exist" — that is the normal first-run case.
- `cmd_chat`: `0` on a clean end (EOF); `1` if a turn ended for any
  reason other than the person choosing to stop; `130` on Ctrl+C; `2`
  (via a printed message, not `parser.error()`, since `cmd_chat` isn't
  handed the parser) for `--resume`+`--key` given together, or for an
  unknown/unwired provider caught at the boundary (§Design).

## Acceptance criteria

- [ ] A fresh `sadana chat` (no `--resume`) creates a new, named
      conversation (auto-named if `--key` omitted) and it is durably
      saved (readable via `sadana conversations <name>` afterward).
- [ ] One real exchange: `take_turn_and_reconcile` is called with real
      `provider`/`model` defaults resolved from config, and the reply
      is printed.
- [ ] Persona: a fresh run with no persona file seeds one with the
      built-in default and uses that text as the conversation's stable
      prompt; a pre-existing persona file's content is used verbatim,
      unmodified.
- [ ] A conversation started, then a persona-file edit, then a *second*
      `sadana chat` run against the *same* `--resume` key: the resumed
      conversation's own stored prompt is unaffected by the edit (it
      was fixed at creation) — proving requirement 2's "already running
      keeps what it started with" half.
- [ ] `--resume <key>` loads and continues an existing conversation;
      `--resume <unknown key>` surfaces `ConversationNotFound` (existing
      behavior, not newly caught).
- [ ] `--resume` and `--key` together exit `2` with a clear message,
      before any store access happens.
- [ ] An unregistered `--provider` name exits cleanly with a message
      naming the problem, not a raw traceback.
- [ ] A plugin's `call` node during the session prompts for approval
      (proven by a fake plugin fixture + monkeypatched `input`, the same
      technique `plugin_manifest`'s own tests already use for
      `_default_approve` — not a real network call).
- [ ] After a turn, `conversation_store.load()` on the same key reflects
      that turn's updated `next_turn_seq`/`iteration_budget.used`, not
      just its messages — proving the post-turn explicit `save()` closes
      the gap described in §Design.
- [ ] Ctrl-D (`EOFError`) ends the loop with exit `0`.
- [ ] `make verify` ends `VERIFY OK`.

## Non-goals

- Any reachability beyond a person at a terminal directly (no
  messaging platform, no gateway) — `cli_shell_blueprint.md` §4.2/§6
  items 5-6, unscheduled.
- Multi-provider selection, fallback, or credential-pool logic —
  MODEL-ACCESS's job, not built here or anywhere yet.
- Finding a conversation by anything other than an exact `--resume`
  key — `sadana conversations <query>` (CLI-SHELL-03) already exists
  for that; composing the two is the user's job, not this command's.
- Multiple personas, moods, or any customization beyond one plain
  editable file.
- Live, mid-conversation persona reload (see §Design's correction).
- Streaming output, rich terminal formatting, or anything beyond plain
  `print()`.

## Rejected alternatives

- **Live persona reload every turn, via a new `PromptRotationReason`
  member and an extended `rotate_prompt()` that also recomputes
  `stable_prompt_len`** — this is what `intent.md` originally asked
  for. Rejected once hermes's own actual prompt-building code (not a
  comment describing it) was checked and found to do the opposite:
  build once per session, invalidate only on a narrow, named trigger.
  Building live reload here would mean inventing behavior neither the
  reference nor sadana's own existing architecture actually has,
  for a promise this document no longer makes (see intent.md's own
  trace).
- **A caller-supplied or overridable `approve` callback for
  `sadana chat`** — rejected; the existing default already is the
  correct interactive behavior (§Design), and adding an override point
  with no second real caller wanting a different one is exactly the
  "registry before a second member exists" mistake CLAUDE.md's own
  "Please do" list warns against.
- **Reusing one `bind_persist()` closure for the whole session** —
  rejected once its snapshot-at-bind-time shape was checked against
  cross-turn reuse; see §Design's "real integration gap."
- **A UUID or opaque generated conversation key** — rejected;
  CLAUDE.md's own naming rule wants a natural key for anything
  long-lived a user returns to. A timestamp-based default is still
  auto-generated (no ceremony for a throwaway chat) but stays a
  human-readable name, not an opaque pointer.
- **Silently letting `UnknownProvider`/`ProviderNotWired` surface
  raw** — considered, to stay consistent with CLI-SHELL-01/03's "no new
  error handling for a store that fails to open." Rejected because a
  bad provider name is an ordinary argv-shaped user mistake this block
  owns catching, not an internal-infrastructure failure with no better
  story — see §Design's boundary-validation paragraph for the
  distinction actually being drawn.

## Concerns

- **This is the first work item to call `bind_persist()` across more
  than one turn**, and the first to call `take_turn_and_reconcile()`
  in a real, unbounded loop rather than a fixed proof-script sequence.
  Both integration points were audited by re-reading their existing
  docstrings against this new usage pattern rather than assumed safe by
  analogy — see §Design for what that surfaced (the post-turn `save()`
  requirement). Flagging plainly: this is new integration territory,
  not a thin wrapper the way items 1-3 were.
- **The context-window table (`model_access._CONTEXT_WINDOWS`,
  `model_access.py:296-303`) has exactly one entry** — the same
  provider/model pair this item defaults to. Nothing on today's live
  `take_turn` path actually calls `context_window()` (confirmed by
  grep — it's defined but has no caller yet), so an overridden
  `--provider`/`--model` pair outside that one entry does not crash on
  that account today. It would, the moment some other future work item
  wires `context_window()` into the live path. Not this item's problem
  to solve, but worth a future reader knowing why "any provider/model"
  isn't actually free of risk yet.
- **`testing-conventions` is applied**: `tests/unit/test_subcommands_chat.py`
  touches no real network or model API (a fake `dispatch`/monkeypatched
  `model_access.send`, the same technique
  `tests/unit/test_conversation_store.py`'s `bind_persist` tests and
  `scripts/prove_conversation_e2e.py` both already use) and a
  `tmp_path`-backed real sqlite store, matching CLI-SHELL-01/03. The
  one genuinely interactive piece (`input()` for both the chat prompt
  and `_default_approve`'s own prompt) is exercised via
  `monkeypatch.setattr("builtins.input", ...)`, not a real terminal.
- **A build-time `/simplify` self-check found two more things, applied
  differently.** (1) The post-turn `save()` re-attempts every message
  in the conversation every turn (`start_seq` defaults to `0`), even
  though `bind_persist()` already flushed all-but-the-newest
  incrementally during the same turn — an `O(n²)` shape across a long
  session. At this item's actual scale (one interactive human, one
  local sqlite file) `INSERT OR IGNORE` against an indexed primary key
  makes this negligible in practice; fixing it properly means exposing
  `bind_persist()`'s internal flushed-count to its caller, a signature
  change to an already-shipped CONV-08 function this item has no
  reason to touch. Flagged, not fixed — the same "first thing to
  revisit if this store ever holds enough rows" posture CLI-SHELL-01's
  own spec.md already took for a different N+1 shape. (2) The proactive
  `manifest.request_fn is None` check (§Design, §Interface) duplicates
  a predicate `model_access.send()` already owns, and the deeper gap it
  works around — `UnknownProvider`/`ProviderNotWired` aren't translated
  into the same failure-outcome contract `conversation.complete()`
  already gives every other model-access failure — affects every
  caller of `take_turn` (confirmed: `eval_harness.py`'s own calls have
  the identical unguarded exposure), not just this command. The deeper
  fix belongs in `conversation.py`/`model_access.py`, is a real design
  question of its own (which `ExitReason`, or a new one), and is
  exactly the kind of cross-cutting change that shouldn't ride in on a
  CLI subcommand's diff. Recommended as a maintain-stage finding (a new
  `intent.md`), not attempted here; this item's own proactive check
  stays as a correct, working CLI-level guard in the meantime.
- **The `bind_persist()` cross-turn risk found above is pinned to
  `CLAUDE.md`**, with the maintainer's approval, since it binds any
  future caller of `bind_persist()`, not just this one — the same
  reasoning `docs/reference/dispatch_closure_state_bug.md` already
  applied to `build_dispatch()`.
- **No policy conflict found** between `testing-conventions` and this
  design; the one real tension this item surfaced — a written promise
  in `intent.md` that turned out not to match either the reference
  material's real behavior or sadana's own existing architecture — was
  resolved by correcting `intent.md` itself (recorded in its own trace)
  rather than building the promise anyway or quietly building something
  narrower without saying so.
