# Plan: Finding a past conversation from a terminal, for real (from intent.md 2026-09-08)

## Files that change

- `src/sadana/subcommands/__init__.py` (new) — empty, package marker.
- `src/sadana/subcommands/conversations.py` (new) — `build_conversations_parser()`,
  `format_conversation_line()`, `cmd_conversations()`.
- `src/sadana/cli.py` — `build_parser()` gains subparsers +
  `"conversations"`; `main()` loses its bespoke empty-argv check;
  docstring updated to match.
- `tests/unit/test_cli.py` — two new tests (existing ones unmodified;
  see § Risks for why they still hold).
- `tests/unit/test_subcommands_conversations.py` (new) — seven tests.
- `tests/conftest.py` and `tests/unit/test_conversation_store.py` — a
  build-time `/simplify` self-check (reuse-angle pass) found that this
  new test file's `_spec()`/`_conversation()` builders were a
  near-duplicate of `test_conversation_store.py`'s own, and that the
  original's 7-parameter signature already had defaults for all of
  them — so the "trimmed" version wasn't a different shape, just an
  unexercised subset of calls into the same one. Extracted both into
  `tests/conftest.py` (`tool_spec()`, `conversation()`), matching the
  existing `write_skill()` shared-helper precedent there; both test
  files now import instead of redefining.

## Order of work

1. **`src/sadana/subcommands/__init__.py`**: empty file.

2. **`src/sadana/subcommands/conversations.py`**:

   ```python
   """The `sadana conversations` subcommand.

   CLI-SHELL-03 of the CLI-SHELL block (`docs/reference/cli_shell_blueprint.md`
   §6, work item 3); its full contract is
   `docs/tasks/CLI-SHELL-03-conversations-subcommand/spec.md`.

   Owns both its parser and its handler in one file, from the first
   line — see spec.md's Design section for why.
   """

   from __future__ import annotations

   import argparse
   import time
   from contextlib import closing

   from sadana import conversation_store
   from sadana.conversation import Conversation


   def build_conversations_parser(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
       parser = subparsers.add_parser("conversations", help="find a saved conversation")
       parser.add_argument(
           "query",
           nargs="?",
           default="",
           help="text to search for (key, template name, or message content); omit to list every saved conversation",
       )
       parser.set_defaults(func=cmd_conversations)


   def format_conversation_line(conversation: Conversation) -> str:
       count = len(conversation.messages)
       noun = "message" if count == 1 else "messages"
       return f"{conversation.key} ({conversation.template_name}, {count} {noun})"


   def cmd_conversations(args: argparse.Namespace) -> int:
       path = conversation_store.store_path_from_config()
       with closing(conversation_store.open_store(path)) as conn:
           found = conversation_store.search_conversations(conn, args.query, now=time.monotonic())
       if not found:
           print("No conversations found.")
           return 0
       for conversation in found:
           print(format_conversation_line(conversation))
       return 0
   ```

3. **`src/sadana/cli.py`**, full replacement:

   ```python
   """sadana's command-line entry point.

   CLI-SHELL-02 (`docs/reference/cli_shell_blueprint.md` §6, work item 2)
   built the bare shell; CLI-SHELL-03 (work item 3) added its first real
   subcommand. `build_parser()` stays separate from `main()` so each
   later subcommand's own work item extends it the same way
   `build_conversations_parser` did, without needing structural changes
   to `main()` itself.
   """

   from __future__ import annotations

   import argparse
   import sys

   from sadana import __version__
   from sadana.subcommands.conversations import build_conversations_parser


   def build_parser() -> argparse.ArgumentParser:
       parser = argparse.ArgumentParser(prog="sadana")
       parser.add_argument("--version", action="version", version=f"sadana {__version__}")
       subparsers = parser.add_subparsers(dest="command", required=True)
       build_conversations_parser(subparsers)
       return parser


   def main(argv: list[str] | None = None) -> int:
       args = build_parser().parse_args(sys.argv[1:] if argv is None else argv)
       return args.func(args)


   if __name__ == "__main__":
       raise SystemExit(main())
   ```

4. **`tests/unit/test_cli.py`**: add, without touching any existing test:

   ```python
   from sadana.subcommands.conversations import cmd_conversations


   @pytest.mark.unit
   def test_no_args_names_conversations_as_a_valid_choice(capsys: pytest.CaptureFixture[str]) -> None:
       with pytest.raises(SystemExit):
           main([])
       assert "conversations" in capsys.readouterr().err


   @pytest.mark.unit
   def test_conversations_subcommand_reachable_via_main() -> None:
       assert main(["conversations"]) == 0
   ```

5. **`tests/unit/test_subcommands_conversations.py`** (new) — reuses the
   `_conversation()`-builder pattern already established in
   `tests/unit/test_conversation_store.py`, scaled down to just the
   fields this module's tests need:

   ```python
   """Tests for sadana.subcommands.conversations."""

   from __future__ import annotations

   import argparse

   import pytest

   from sadana.context import ContextState
   from sadana.conversation import (
       Conversation,
       IterationBudget,
       Message,
       ToolSpec,
       build_surface,
       turn_prompt_hash,
   )
   from sadana.conversation_store import create, open_store, store_path_from_config
   from sadana.subcommands.conversations import (
       build_conversations_parser,
       cmd_conversations,
       format_conversation_line,
   )

   _SYSTEM_PROMPT = "You are a helpful assistant."


   def _spec() -> ToolSpec:
       return ToolSpec(
           key="noop", name="noop", parameters={"type": "object", "properties": {}}, describe=lambda _r: "x"
       )


   def _conversation(
       *, key: str, template_name: str = "t1", messages: tuple[Message, ...] = ()
   ) -> Conversation:
       surface = build_surface([_spec()])
       return Conversation(
           key=key,
           template_name=template_name,
           system_prompt=_SYSTEM_PROMPT,
           prompt_sha256=turn_prompt_hash(_SYSTEM_PROMPT, surface),
           prompt_epoch=0,
           tool_surface=surface,
           messages=messages,
           next_turn_seq=0,
           iteration_budget=IterationBudget(max_total=10, used=0),
           wall_clock_budget=None,
           stable_prompt_len=len(_SYSTEM_PROMPT),
           context_state=ContextState(),
           next_child_seq=0,
       )


   def _save(conversation: Conversation) -> None:
       conn = open_store(store_path_from_config())
       try:
           create(conn, conversation, now=0.0)
       finally:
           conn.close()


   @pytest.mark.unit
   def test_format_conversation_line_pluralizes_message_count() -> None:
       one = _conversation(key="k1", messages=(Message(role="user", content="hi"),))
       none = _conversation(key="k2", messages=())
       assert format_conversation_line(one) == "k1 (t1, 1 message)"
       assert format_conversation_line(none) == "k2 (t1, 0 messages)"


   @pytest.mark.unit
   def test_cmd_conversations_lists_everything_with_no_query(capsys: pytest.CaptureFixture[str]) -> None:
       _save(_conversation(key="alpha"))
       _save(_conversation(key="beta"))

       assert cmd_conversations(argparse.Namespace(query="")) == 0

       out = capsys.readouterr().out
       assert "alpha" in out
       assert "beta" in out


   @pytest.mark.unit
   def test_cmd_conversations_narrows_by_query(capsys: pytest.CaptureFixture[str]) -> None:
       _save(_conversation(key="debugging-session"))
       _save(_conversation(key="unrelated"))

       assert cmd_conversations(argparse.Namespace(query="debug")) == 0

       out = capsys.readouterr().out
       assert "debugging-session" in out
       assert "unrelated" not in out


   @pytest.mark.unit
   def test_cmd_conversations_empty_store_prints_a_clear_message(capsys: pytest.CaptureFixture[str]) -> None:
       assert cmd_conversations(argparse.Namespace(query="")) == 0
       assert capsys.readouterr().out.strip() != ""


   @pytest.mark.unit
   def test_cmd_conversations_does_not_modify_the_store() -> None:
       _save(_conversation(key="alpha"))
       cmd_conversations(argparse.Namespace(query=""))

       conn = open_store(store_path_from_config())
       try:
           count = conn.execute("SELECT COUNT(*) FROM conversations").fetchone()[0]
       finally:
           conn.close()
       assert count == 1


   @pytest.mark.unit
   def test_build_conversations_parser_registers_conversations_with_optional_query() -> None:
       parser = argparse.ArgumentParser()
       subparsers = parser.add_subparsers(dest="command", required=True)
       build_conversations_parser(subparsers)

       args = parser.parse_args(["conversations"])
       assert args.query == ""
       assert args.func is cmd_conversations

       args = parser.parse_args(["conversations", "foo"])
       assert args.query == "foo"
   ```

6. Run `scripts/run_tests.sh tests/unit/test_cli.py
   tests/unit/test_subcommands_conversations.py
   tests/unit/test_conversation_store.py`, then `/ponytail-review` and
   `/simplify` against the diff, then `make verify`.

## Risks

- **What could this break?** `conversation_store.py` itself is
  untouched — its own tests are run in step 6 as a direct check, not
  just an assumption. Every existing `test_cli.py` test
  (`--version`, `--help`/`-h`, `argv=None`, `build_parser()` freshness,
  and both invalid-invocation tests) is expected to keep passing
  unmodified: `--version`/`--help` fire before argparse ever reaches the
  subparsers-required check (unchanged mechanism); the two
  invalid-invocation tests only assert `SystemExit` code `2` and
  non-empty stderr, which `required=True` with a real choice still
  produces (via a different internal argparse message than before, but
  the same observable contract) — run, not assumed, in step 6.
- **Riskiest step**: the `cli.py` rewrite (step 3) is the one place a
  mistake would silently break every existing behavior at once (it's
  the whole dispatch surface). Mitigated by running the *existing*
  `test_cli.py` suite immediately after, unmodified, before adding the
  two new tests conceptually — if something in steps 1-3 broke
  `--version`/`--help`/etc., that surfaces immediately rather than being
  masked by new tests that only cover the new command.
- **Drift check against spec.md's Rejected alternatives**: positional
  `query`, not a `--query` flag — followed. One subcommand, not two —
  followed. `"conversations"`, not `"sessions"` — followed. Bespoke
  empty-argv check deleted, not kept alongside the new mechanism —
  followed (spec.md's Design section explicitly calls for its removal,
  not just an addition next to it). No new error handling for a store
  that fails to open — followed, `cmd_conversations` has no `try`/`except`.

## Proof

- `tests/unit/test_subcommands_conversations.py`'s seven tests cover:
  message-count pluralization, listing everything with no query,
  narrowing by query, an empty-store message, read-only behavior (row
  count unchanged), and the parser's own registration (`query` default,
  `func` wiring).
- `tests/unit/test_cli.py`'s two new tests cover: the no-args error
  message actually naming `"conversations"` as a choice, and
  `sadana conversations` being reachable end-to-end through `main()`.
- Every pre-existing `test_cli.py` test still passes, unmodified.
- `make verify` ends `VERIFY OK`.
