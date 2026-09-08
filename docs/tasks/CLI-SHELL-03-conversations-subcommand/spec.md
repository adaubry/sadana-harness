# Spec: Finding a past conversation from a terminal, for real

Intent: docs/tasks/CLI-SHELL-03-conversations-subcommand/intent.md

## Requirements

1. `sadana conversations` with nothing after it shows every saved
   conversation, one short line each. (Intent §Proposed outcome.)
2. `sadana conversations <something>` narrows that to conversations
   matching what was typed. (Intent §Proposed outcome.)
3. Each line shows enough to recognize which conversation it is, not
   everything it contains. (Intent §Constraints.)
4. Never creates, changes, or deletes a saved conversation. (Intent
   §Constraints.)
5. No filtering beyond the one thing typed after the command. (Intent
   §Constraints.)

## Design

**Reference corpus.** Hermes's actual CLI-facing analogue is
`hermes_cli/main.py:14098`'s `sessions` sub-subparsers (~20 actions:
list/export/prune/repair/browse/…) backed by `hermes_state.py:10854`'s
`list_sessions_rich` (already read and largely declined for CLI-SHELL-01;
see that work item's `spec.md`). Adopting from it here: a subparser with
a query-shaped positional argument is the right CLI shape for "list or
search from one command." Declining the other ~18 actions outright —
nothing in this intent asks for export/prune/repair/etc., and
`cli_shell_blueprint.md` §7 open question 1 leaves that open for a real
future requirement, not this one.

**Where this lives — the first real subcommand module.**
`cli_shell_blueprint.md` §4.1/§5 already settled this: "a sadana
subcommand module owns both its parser and its handler from the first
line of code that exists for it. One file per subcommand." New package
`src/sadana/subcommands/` (empty `__init__.py`, matching hermes's own
directory name for the same concept — no reason to invent a different
one) with `src/sadana/subcommands/conversations.py`, exporting
`build_conversations_parser(subparsers)` and `cmd_conversations(args) ->
int` together, in one file, from this first commit — not something to
split later, since there is nothing to split yet.

**`cli.py` changes, and a rejected alternative gets revisited.**
`build_parser()` gains `subparsers = parser.add_subparsers(dest="command",
required=True)`, then `build_conversations_parser(subparsers)`. `main()`
simplifies to `args = build_parser().parse_args(...); return
args.func(args)` — CLI-SHELL-02's own bespoke `if not args:
parser.error(...)` check is deleted. That check existed specifically
because `spec.md` (CLI-SHELL-02) rejected `required=True` subparsers
*with zero registered choices* — the objection was the empty,
confusing `"choose from ()"` message that produces, not `required=True`
itself. Now that a real choice (`"conversations"`) exists,
`required=True` gives argparse's own ordinary "the following arguments
are required: command" / `"invalid choice: 'x' (choose from
'conversations')"` — exactly the two error shapes requirement 5 of
CLI-SHELL-02's own spec already wanted, now with no rough edge. This is
the item CLI-SHELL-02's own spec named as the one that would extend
`build_parser()` "without touching `main()`" for the *parser*-building
side; `main()` itself does need a small edit here, to retire a check
that no longer needs to exist — not a violation of that design, its
natural completion.

**`cmd_conversations`.** Opens the existing store
(`conversation_store.store_path_from_config()`,
`conversation_store.open_store()`), calls
`conversation_store.search_conversations(conn, args.query,
now=time.monotonic())` — **`time.monotonic()`, not `time.time()`**:
`conversation.py:311`'s docstring is explicit that `now` in this system
is always the monotonic clock, never wall-clock time; this is the first
place outside `conversation_store.py`'s own tests that supplies a live
`now`, so getting the clock right here matters even though this
command never reads the resulting `wall_clock_budget` itself — a future
caller of the same `Conversation` values should never have reason to
suspect this one path used a different clock. Prints one line per
result via a small pure helper, `format_conversation_line(c) ->
str` (key, template name, message count — pluralized), or one fixed
"no conversations found" line when the result is empty. Closes the
connection (`contextlib.closing`) and returns `0` — a store with zero
matches is not a failure (matches CLI-SHELL-01's own reasoning that an
empty search result is a normal outcome, never an error).

**No new error handling.** A store that can't be opened (permissions,
corruption) surfaces as whatever uncaught exception
`conversation_store.open_store()` already raises — matching
`create`/`save`/`load`'s own existing behavior of not catching or
translating that. Inventing a new error-handling layer here would be
new code with no requirement asking for it.

## Interface

```python
# src/sadana/subcommands/conversations.py
def build_conversations_parser(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None: ...
def format_conversation_line(conversation: Conversation) -> str: ...
def cmd_conversations(args: argparse.Namespace) -> int: ...
```

- `build_conversations_parser(subparsers)`: registers `"conversations"`
  on the passed-in subparsers object with one optional positional,
  `query` (`nargs="?"`, `default=""`), and `set_defaults(func=cmd_conversations)`.
  Mutates its argument; returns nothing — matches argparse's own
  `add_parser`/`add_argument` convention throughout this codebase's one
  other subcommand-shaped call site (there are none yet besides this;
  matches hermes's own `build_*_parser(subparsers, ...)` shape cited in
  `cli_shell_blueprint.md` §4.1).
- `format_conversation_line(conversation)`: pure, no I/O — `"<key>
  (<template_name>, <N> message(s))"`. Testable without a store.
- `cmd_conversations(args)`: reads `args.query` (a `str`, possibly
  `""`), returns `0` always — there is no failure mode this function
  itself defines (see §Design's "no new error handling"). Prints to
  `stdout` only.

`src/sadana/cli.py`'s `build_parser()`/`main()` change as described in
§Design; no new public interface there beyond what CLI-SHELL-02 already
declared.

## Acceptance criteria

- [ ] `sadana conversations` with two saved conversations prints two
      lines, one per conversation, each naming its key, template name,
      and message count.
- [ ] `sadana conversations <substring>` prints only the conversations
      `search_conversations` would return for that substring — same
      case-insensitivity, same key/template/content matching already
      proven in CLI-SHELL-01's own tests.
- [ ] Zero matches (including an empty store) prints one clear message
      to stdout and exits `0`, not an error.
- [ ] `sadana` with nothing after it exits `2`; stderr names
      `"conversations"` as a valid choice (proves `required=True` now
      has something real to require, not the empty-choice case
      CLI-SHELL-02 declined).
- [ ] `sadana bogus-command` exits `2` via argparse's own invalid-choice
      error, same mechanism as the case above.
- [ ] Running the command does not change what's in the store —
      `search_conversations`/`load` before and after an invocation
      return identical results.
- [ ] `make verify` ends `VERIFY OK`.

## Non-goals

- Anything beyond list/search: export, prune, repair, or any of
  hermes's other ~18 `sessions` actions (`cli_shell_blueprint.md` §7
  open question 1 — still open, still not this item's job).
- Printing a conversation's full detail (messages, budgets). A
  follow-on "show me everything about conversation X" command, if one
  is ever wanted, is a separate work item with its own intent.
- New error handling for a store that can't be opened.
- Filtering flags (by date, by template specifically, etc.) beyond the
  one free-text term `search_conversations` already supports.

## Rejected alternatives

- **A `--query` flag instead of a bare positional** — declined; a
  positional matches "type what you remember after the command," the
  natural idiom the interview confirmed, and needs no flag name to
  remember.
- **Two subcommands (`list`/`search`)** — declined during planning;
  `search_conversations(conn, "")` already *is* the list operation
  (CLI-SHELL-01's own design), so one subcommand with an optional
  argument mirrors the underlying function instead of inventing a
  distinction it doesn't have.
- **The word "sessions"** — declined during planning; this codebase's
  own vocabulary is "conversation" everywhere else.
- **Keeping CLI-SHELL-02's bespoke empty-argv check** now that a real
  subcommand exists — declined; see §Design. `required=True` with an
  actual choice supersedes it cleanly.
- **A new error-handling layer for a store that fails to open** —
  declined; nothing asks for it, and it would be the first place in
  this codebase inventing translation for a failure `create`/`save`/
  `load` already leave uncaught.

## Concerns

- **`cli_shell_blueprint.md` §7 open question 2** ("does a handler ever
  need shared state a fresh import can't give it?") is answered by this
  item: no. `cmd_conversations` imports `conversation_store` directly
  and opens its own connection; no context object, no module-level
  state, matching `CLAUDE.md`'s existing "no context object" rule for
  CLI-SHELL handlers.
- **The `time.monotonic()` vs `time.time()` distinction** is easy to
  get backwards from outside `conversation.py`, since nothing about the
  parameter name `now` signals which clock it means. Called out
  explicitly in §Design rather than left for a reviewer to catch by
  chance.
- **`testing-conventions` is applied**: the new test file is
  `tests/unit/test_subcommands_conversations.py` (module
  `sadana/subcommands/conversations.py`, flattened per this repo's own
  convention — `tests/unit/` has no subdirectories mirroring `src/sadana`'s
  packages; confirmed against `tests/unit/test_model_providers_openrouter.py`
  for `src/sadana/model_providers/`). Uses `tmp_path`-backed real sqlite,
  the same pattern `test_conversation_store.py` already established — no
  mocking of the store itself, only `capsys` for output assertions.
- No policy conflict found; this item is additive to both `cli.py` and
  the new `subcommands/` package, and doesn't touch anything
  `conversation_store.py`'s own tests depend on.
