# Plan: Actually talking to the agent (from intent.md 2026-09-08)

## Context

Every piece of CONVERSATION/PLUGINS/CONTEXT built so far has only ever
been exercised by a hand-run proof script or a unit test. This work
item (CLI-SHELL-04, item 4 of `docs/reference/cli_shell_blueprint.md`
§6) is where a person actually talks to the agent: `sadana chat`
creates or resumes a conversation, takes real turns through the
already-built plugin dispatch machinery, asks for approval before
anything reaches outside (reusing the existing real terminal prompt,
never overridden), and durably persists as it goes. Full design in
`spec.md`, including two things `spec.md`'s own design pass corrected
or discovered: `intent.md`'s promise of live persona reload was walked
back to match how the reference material's real code (not a stale
comment) actually behaves, and `bind_persist()` needs rebuilding fresh
every turn the same way `build_dispatch()` already documents needing to.

Two refinements found while turning `spec.md`'s sketch into exact code,
both improvements on the sketch, not deviations from its requirements:

1. `spec.md`'s Interface said `--resume`+`--key` together gets a
   hand-printed message from `cmd_chat`. Building the parser found a
   strictly better mechanism already available: `argparse`'s own
   `add_mutually_exclusive_group()` rejects the pair natively (same
   `SystemExit(2)` contract spec.md's own acceptance criterion asks
   for) — the exact "reuse argparse's own mechanism instead of
   hand-rolling a parallel one" lesson CLI-SHELL-03 already established.
   No code in `cmd_chat` needs to know about this case at all.
2. `spec.md`'s Design sketched catching `UnknownProvider`/
   `ProviderNotWired` reactively around the first turn call. Since
   `ProviderNotWired` is only ever raised from a manifest's own
   `request_fn is None` check, that same check can run proactively,
   once, before touching the store at all — failing faster, and
   avoiding needing to import/catch `ProviderNotWired` as an exception
   type at all.

## Files that change

- `src/sadana/subcommands/chat.py` (new) — `build_chat_parser()`,
  `persona_path_from_config()`, `load_or_seed_persona()`, `cmd_chat()`,
  and a small private `_chat_loop()` coroutine.
- `src/sadana/cli.py` — `build_parser()` gains one more
  `build_chat_parser(subparsers)` call.
- `tests/conftest.py` — adds `tool_call_response()`/`plain_response()`,
  extracted alongside the existing `tool_spec()`/`conversation()`
  helpers: `test_conversation_store.py` already has an inline
  `_tool_call_response`, and this work item's own test file needs the
  identical shape plus its "plain completion" sibling — the same
  "second real user" threshold CLI-SHELL-03's own self-check already
  crossed for the other two helpers.
- `tests/unit/test_conversation_store.py` — its inline
  `_tool_call_response` replaced with the conftest import; no behavior
  change.
- `tests/unit/test_subcommands_chat.py` (new) — eleven tests.

## Order of work

1. **`tests/conftest.py`**: add, next to `tool_spec`/`conversation`:

   ```python
   def tool_call_response(*names: str) -> model_access.Response:
       return model_access.Response(
           content=None,
           tool_calls=tuple({"function": {"name": n, "arguments": "{}"}} for n in names),
           finish_reason="tool_calls",
           usage=model_access.Usage(),
       )


   def plain_response(content: str) -> model_access.Response:
       return model_access.Response(content=content, tool_calls=(), finish_reason="stop", usage=model_access.Usage())
   ```

   (`from sadana import model_access` added to conftest's imports.)

2. **`tests/unit/test_conversation_store.py`**: replace its local
   `_tool_call_response` definition with
   `from conftest import tool_call_response as _tool_call_response`;
   delete the now-redundant local function. No call site changes
   (same name via the `as` alias).

3. **`src/sadana/subcommands/chat.py`**:

   ```python
   """The `sadana chat` subcommand.

   CLI-SHELL-04 of the CLI-SHELL block (`docs/reference/cli_shell_blueprint.md`
   §6, work item 4); its full contract is
   `docs/tasks/CLI-SHELL-04-chat-command/spec.md`.

   Owns both its parser and its handler in one file, from the first
   line — see spec.md's Design section for why.
   """

   from __future__ import annotations

   import argparse
   import asyncio
   import sqlite3
   import sys
   import time
   from pathlib import Path

   from sadana import config, conversation_store, model_access, plugin_dispatch, plugin_manifest
   from sadana.conversation import (
       Conversation,
       ConversationTemplate,
       ExitReason,
       TemplateRecipe,
       create_conversation,
       iteration_budget_from_config,
       wall_clock_budget_from_config,
   )

   _DEFAULT_PERSONA = (
       "You are sadana, a plainly-spoken assistant. Answer directly, say "
       "when you're not sure, and only act on something after it has "
       "actually been agreed to.\n"
   )


   def build_chat_parser(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
       parser = subparsers.add_parser("chat", help="have a live conversation with the agent")
       target = parser.add_mutually_exclusive_group()
       target.add_argument("--resume", metavar="KEY", help="continue a previously saved conversation by its exact key")
       target.add_argument("--key", metavar="NAME", help="name a new conversation; a name is generated if omitted")
       parser.add_argument("--provider", help="override the configured default provider for this run")
       parser.add_argument("--model", help="override the configured default model for this run")
       parser.set_defaults(func=cmd_chat)


   def persona_path_from_config() -> Path:
       return config.env_path("SADANA_CHAT_PERSONA_PATH", default=config.get_paths().config_dir / "persona.md")


   def load_or_seed_persona(path: Path) -> str:
       if not path.exists():
           path.parent.mkdir(parents=True, exist_ok=True)
           path.write_text(_DEFAULT_PERSONA, encoding="utf-8")
       return path.read_text(encoding="utf-8")


   async def _chat_loop(
       conn: sqlite3.Connection,
       conversation: Conversation,
       *,
       plugin_set: plugin_dispatch.PluginSet,
       persona: str,
       provider: str,
       model: str,
   ) -> int:
       while True:
           try:
               user_input = await asyncio.to_thread(input, "> ")
           except EOFError:
               print()
               return 0

           now = time.monotonic()
           dispatch, tracker = plugin_dispatch.build_dispatch(
               conversation, plugin_set, stable_prompt=persona, provider=provider, model=model, now=now
           )
           persist = conversation_store.bind_persist(conn, conversation, now=now)
           result, conversation = await plugin_dispatch.take_turn_and_reconcile(
               conversation,
               dispatch,
               tracker,
               user_input=user_input,
               provider=provider,
               model=model,
               now=now,
               persist=persist,
           )
           conversation_store.save(conn, conversation, now=now)

           if result.exit_reason != ExitReason.COMPLETED:
               print(f"[{result.exit_reason.value}] {result.detail or ''}", file=sys.stderr)
               return 1
           print(result.final_text)


   def cmd_chat(args: argparse.Namespace) -> int:
       provider = args.provider or config.env("SADANA_MODEL_ACCESS_PROVIDER", "openrouter")
       model = args.model or config.env("SADANA_MODEL_ACCESS_MODEL", "deepseek/deepseek-v4-flash-0731")

       try:
           manifest = model_access.get_provider(provider)
       except model_access.UnknownProvider:
           print(f"sadana chat: unknown provider {provider!r}", file=sys.stderr)
           return 2
       if manifest.request_fn is None:
           print(f"sadana chat: provider {provider!r} has no request function configured", file=sys.stderr)
           return 2

       persona = load_or_seed_persona(persona_path_from_config())
       plugin_set = plugin_dispatch.build_plugin_set(plugin_manifest.discover_plugins())
       conn = conversation_store.open_store(conversation_store.store_path_from_config())
       try:
           now = time.monotonic()
           if args.resume:
               conversation = conversation_store.load(conn, args.resume, now=now)
           else:
               key = args.key or time.strftime("chat-%Y%m%d-%H%M%S")
               template = ConversationTemplate(
                   name="chat",
                   recipe=TemplateRecipe(
                       stable_prompt=persona, catalog=plugin_set.catalog, tool_specs=plugin_set.tool_specs
                   ),
               )
               conversation, _template = create_conversation(
                   template,
                   key,
                   system_message="",
                   iteration_budget=iteration_budget_from_config(),
                   wall_clock_budget=wall_clock_budget_from_config(now),
               )
               conversation_store.create(conn, conversation, now=now)

           try:
               return asyncio.run(
                   _chat_loop(conn, conversation, plugin_set=plugin_set, persona=persona, provider=provider, model=model)
               )
           except KeyboardInterrupt:
               print()
               return 130
       finally:
           conn.close()


   if __name__ == "__main__":
       pass
   ```

4. **`src/sadana/cli.py`**: add the import and one call, same pattern as
   `build_conversations_parser`:

   ```python
   from sadana.subcommands.chat import build_chat_parser
   from sadana.subcommands.conversations import build_conversations_parser


   def build_parser() -> argparse.ArgumentParser:
       parser = argparse.ArgumentParser(prog="sadana")
       parser.add_argument("--version", action="version", version=f"sadana {__version__}")
       subparsers = parser.add_subparsers(dest="command", required=True)
       build_conversations_parser(subparsers)
       build_chat_parser(subparsers)
       return parser
   ```

5. **`tests/unit/test_subcommands_chat.py`** (new):

   ```python
   """Tests for sadana.subcommands.chat."""

   from __future__ import annotations

   import argparse

   import pytest
   from conftest import plain_response, tool_call_response

   from sadana import conversation_store, model_access
   from sadana.conversation_store import ConversationNotFound, create, load, open_store, store_path_from_config
   from sadana.subcommands.chat import (
       build_chat_parser,
       cmd_chat,
       load_or_seed_persona,
       persona_path_from_config,
   )


   def _args(**overrides: object) -> argparse.Namespace:
       return argparse.Namespace(resume=None, key=None, provider=None, model=None, **overrides)


   def _feed(monkeypatch: pytest.MonkeyPatch, *inputs: str) -> None:
       """`input()` returns each of `inputs` in order, then raises EOFError."""
       remaining = iter(inputs)

       def fake_input(_prompt: str = "") -> str:
           try:
               return next(remaining)
           except StopIteration:
               raise EOFError from None

       monkeypatch.setattr("builtins.input", fake_input)


   # ── build_chat_parser ────────────────────────────────────────────────────


   @pytest.mark.unit
   def test_build_chat_parser_defaults_and_wiring() -> None:
       parser = argparse.ArgumentParser()
       subparsers = parser.add_subparsers(dest="command", required=True)
       build_chat_parser(subparsers)

       args = parser.parse_args(["chat"])
       assert (args.resume, args.key, args.provider, args.model) == (None, None, None, None)
       assert args.func is cmd_chat


   @pytest.mark.unit
   def test_build_chat_parser_rejects_resume_and_key_together() -> None:
       parser = argparse.ArgumentParser()
       subparsers = parser.add_subparsers(dest="command", required=True)
       build_chat_parser(subparsers)

       with pytest.raises(SystemExit) as exc:
           parser.parse_args(["chat", "--resume", "x", "--key", "y"])
       assert exc.value.code == 2


   # ── persona ──────────────────────────────────────────────────────────────


   @pytest.mark.unit
   def test_load_or_seed_persona_creates_default_file(tmp_path) -> None:
       path = tmp_path / "persona.md"
       text = load_or_seed_persona(path)
       assert path.read_text(encoding="utf-8") == text
       assert "sadana" in text


   @pytest.mark.unit
   def test_load_or_seed_persona_reads_existing_file_verbatim(tmp_path) -> None:
       path = tmp_path / "persona.md"
       path.write_text("You are a pirate.\n", encoding="utf-8")
       assert load_or_seed_persona(path) == "You are a pirate.\n"


   # ── cmd_chat: provider validation, before anything else runs ────────────


   @pytest.mark.unit
   def test_cmd_chat_unknown_provider_exits_two_before_touching_store(monkeypatch: pytest.MonkeyPatch) -> None:
       assert cmd_chat(_args(provider="not-a-real-provider")) == 2
       conn = open_store(store_path_from_config())
       try:
           count = conn.execute("SELECT COUNT(*) FROM conversations").fetchone()[0]
       finally:
           conn.close()
       assert count == 0


   # ── cmd_chat: a real turn, creation, and the post-turn save ─────────────


   @pytest.mark.unit
   def test_cmd_chat_creates_and_persists_a_new_conversation(monkeypatch: pytest.MonkeyPatch) -> None:
       responses = iter([plain_response("hi there")])
       monkeypatch.setattr(model_access, "send", lambda request: next(responses))
       _feed(monkeypatch, "hello")

       assert cmd_chat(_args(key="test-convo")) == 0

       loaded = load(open_store(store_path_from_config()), "test-convo", now=0.0)
       assert loaded.next_turn_seq == 1
       assert loaded.iteration_budget.used > 0
       assert any(m.role == "user" and m.content == "hello" for m in loaded.messages)


   @pytest.mark.unit
   def test_cmd_chat_immediate_eof_creates_conversation_with_no_turns(monkeypatch: pytest.MonkeyPatch) -> None:
       _feed(monkeypatch)  # EOF on the very first prompt

       assert cmd_chat(_args(key="empty-convo")) == 0

       loaded = load(open_store(store_path_from_config()), "empty-convo", now=0.0)
       assert loaded.next_turn_seq == 0
       assert loaded.messages == ()


   # ── cmd_chat: resume ─────────────────────────────────────────────────────


   @pytest.mark.unit
   def test_cmd_chat_resume_continues_existing_conversation(monkeypatch: pytest.MonkeyPatch) -> None:
       from conftest import conversation as _build_conversation

       conn = open_store(store_path_from_config())
       try:
           create(conn, _build_conversation(key="resume-me"), now=0.0)
       finally:
           conn.close()

       responses = iter([plain_response("continuing")])
       monkeypatch.setattr(model_access, "send", lambda request: next(responses))
       _feed(monkeypatch, "still there?")

       assert cmd_chat(_args(resume="resume-me")) == 0

       loaded = load(open_store(store_path_from_config()), "resume-me", now=0.0)
       assert loaded.next_turn_seq == 1


   @pytest.mark.unit
   def test_cmd_chat_resume_unknown_key_raises_conversation_not_found(monkeypatch: pytest.MonkeyPatch) -> None:
       with pytest.raises(ConversationNotFound):
           cmd_chat(_args(resume="never-saved"))


   # ── cmd_chat: approval is asked, using the real prompt, never overridden ─


   @pytest.mark.unit
   def test_cmd_chat_declined_approval_stops_the_call_node_safely(
       monkeypatch: pytest.MonkeyPatch, tmp_path
   ) -> None:
       monkeypatch.setenv("SADANA_PLUGINS_DIR", "tests/fixtures/plugins")
       responses = iter([tool_call_response("plugin_a_entry"), plain_response("ok, noted")])
       monkeypatch.setattr(model_access, "send", lambda request: next(responses))
       _feed(monkeypatch, "please call plugin_a_entry", "n")  # 2nd input() is the approval prompt

       assert cmd_chat(_args(key="approval-test")) == 0

       loaded = load(open_store(store_path_from_config()), "approval-test", now=0.0)
       assert loaded.next_turn_seq == 1  # the turn still completed; the call node just didn't run
   ```

   `test_cmd_chat_declined_approval_stops_the_call_node_safely` never
   reaches `plugin-a`'s `fetch_webhook` body (a real `execution.run_http`
   call to `https://example.com`) — `plugin_manifest.run_graph`'s own
   contract is that a declined `call` node never reaches body
   resolution at all (`plugin_manifest.py:300-306`), so this proves the
   real approval prompt is wired in without needing a real network call
   in the unit suite (testing-conventions' own network ban).

6. Run `scripts/run_tests.sh tests/unit/test_cli.py
   tests/unit/test_conversation_store.py
   tests/unit/test_subcommands_conversations.py
   tests/unit/test_subcommands_chat.py`, then `/ponytail-review` and
   `/simplify` against the diff, then `make verify`.

## Risks

- **What could this break?** `conversation.py`, `conversation_store.py`,
  `plugin_dispatch.py`, `plugin_manifest.py` are all read, never
  modified — this item only adds a new caller. The one edit to existing
  test code (`test_conversation_store.py`'s `_tool_call_response`
  becoming an import) is a pure rename with an `as` alias — no call
  site changes, and that file's own suite is run in step 6 to confirm.
  `cli.py`'s edit is additive (one more `build_*_parser` call);
  CLI-SHELL-03's own tests (`test_cli.py`,
  `test_subcommands_conversations.py`) are run in step 6 to confirm
  nothing about the existing `conversations` subcommand or the
  top-level shell's `--version`/`--help`/error paths regressed.
- **Riskiest step**: the approval-flow test (step 5's last test) is the
  one place three real, already-built mechanisms
  (`plugin_manifest.run_graph`'s call-node gate, `_default_approve`'s
  real `input()` prompt, and `run_turn`'s own tool-result round-trip)
  compose for the first time in this project's test suite, rather than
  a hand-run proof script. Mitigated by tracing `run_graph`'s exact
  declined-call-node behavior in its own docstring/code first (§Order
  of work step 5's note) rather than assuming it, and by the fact that
  a wrong assumption here fails loudly (a real HTTP call attempted in
  the unit suite, or a hung `input()` call) rather than silently.
- **Second-riskiest**: `bind_persist()`/`build_dispatch()` rebuilt fresh
  every turn, plus an explicit post-turn `save()` — this is new
  integration territory (spec.md's own Concerns says so plainly). Two
  tests target it directly:
  `test_cmd_chat_creates_and_persists_a_new_conversation` (asserts
  `next_turn_seq`/`iteration_budget.used`/message content all land, not
  just messages) and `test_cmd_chat_resume_continues_existing_conversation`
  (asserts the same growth from a pre-existing row, not just a fresh
  one).
- **Drift check against spec.md's Rejected alternatives**: no live
  persona reload, no new `PromptRotationReason` member — followed (the
  persona is read once, in `cmd_chat`, before the loop; `_chat_loop`
  never re-reads it). No `approve=` override passed anywhere — followed
  (`build_dispatch`'s own default is left alone). `bind_persist()`
  rebuilt every turn, not reused — followed. A human-readable
  timestamp-based default key, not a UUID — followed
  (`time.strftime(...)`, not `uuid4()`). No new error handling for a
  store that fails to open — followed (`open_store`/`create`/`load` are
  called with no `try`/`except` around them beyond the one thing that
  *is* new: the proactive provider check, which spec.md's own Rejected
  alternatives explains is a different kind of failure than the ones
  those two prior items declined to handle).

## Proof

- `tests/unit/test_subcommands_chat.py`'s eleven tests cover: parser
  defaults and wiring, the native `--resume`/`--key` conflict, persona
  seeding and verbatim reads, provider validation before any store
  access, a full real turn with persistence of messages/turn-seq/
  budget, an immediate-EOF session creating an empty but real
  conversation, resuming an existing conversation and growing it,
  `ConversationNotFound` propagating unmodified for an unknown
  `--resume` key, and a declined approval proving the real prompt is
  wired in without a network call.
- `tests/unit/test_cli.py` and `tests/unit/test_subcommands_conversations.py`
  (existing, run unmodified) confirm nothing about CLI-SHELL-02/03's
  own behavior regressed.
- `tests/unit/test_conversation_store.py` (existing, run after its one
  mechanical import change) confirms the `_tool_call_response`
  extraction didn't change anything.
- `make verify` ends `VERIFY OK`.

## Post-review fixes

The deploy stage's cold review (see `review.md`) found two Important
issues in what this plan's own code sample above shows, both fixed in
this same branch before a decision:

1. `_chat_loop` dropped `result.final_text` whenever `exit_reason !=
   COMPLETED`, discarding `run_turn`'s own best-effort summary for
   `BUDGET_EXHAUSTED` (`conversation.py:912-931`) — a real bug, not a
   style preference. Fixed: `final_text` is printed whenever present,
   regardless of exit reason; the loop still ends (`return 1`) for
   anything other than `COMPLETED`.
2. `spec.md`'s "persona stays frozen across `--resume`" acceptance
   criterion had no dedicated test — only an indirect, code-reading
   argument. Fixed: `test_cmd_chat_resume_keeps_original_persona_after_a_later_edit`
   added, bringing the file to eleven tests (this plan's own count was
   corrected from nine to eleven, per the review's nit — the file
   already had ten before this addition; the plan's count was wrong
   twice, not just once).
