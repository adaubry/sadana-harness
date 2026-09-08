# Review: Finding a past conversation from a terminal, for real (from plan.md 2026-09-08)

Reviewed: HEAD (working tree, uncommitted) — 7 files, +218/-57
Reviewer context: fresh session
Second opinion: none — ran during build (self-check via `/ponytail-review` +
`/simplify`), not repeated here by design.

## Evidence

```
$ make verify
docs/tasks/CLI-SHELL-03-conversations-subcommand: all present artifacts valid
CHAIN OK
trim trailing whitespace.................................................Passed
fix end of files.........................................................Passed
mixed line ending........................................................Passed
check for case conflicts.................................................Passed
check yaml...............................................................Passed
check toml...............................................................Passed
check json...............................................................Passed
check for merge conflicts................................................Passed
check for added large files..............................................Passed
check that scripts with shebangs are executable..........................Passed
check that executables have shebangs.....................................Passed
detect private key.......................................................Passed
ruff.....................................................................Passed
ruff-format..............................................................Passed
shellcheck...............................................................Passed
Detect secrets...........................................................Passed
docs/reference/ citations resolve to tracked files.......................Passed
LINT OK
Success: no issues found in 15 source files
TYPES OK
▶ hermetic run: TZ=UTC LANG=C.UTF-8 PYTHONHASHSEED=0, empty env
........................................................................ [ 19%]
........................................................................ [ 39%]
........................................................................ [ 58%]
........................................................................ [ 78%]
........................................................................ [ 98%]
.......                                                                  [100%]
367 passed in 3.17s
TESTS OK
VERIFY OK
```

(`git add -N src/sadana/subcommands tests/unit/test_subcommands_conversations.py`
run first, read-only staging, so the new files show in `git diff HEAD`.)

## Findings

Ran all three passes (Bugs, Security, Compliance) against `git diff HEAD` for
all seven changed files, and reconciled the diff against `plan.md` § Files
that change, `plan.md` § Proof, `spec.md` § Acceptance criteria, `spec.md` §
Rejected alternatives, the five design principles, and the CLAUDE.md rule on
CLI handler exit codes. Detail below. No Important findings.

### Scope check

`git diff --stat HEAD` shows exactly the seven files `plan.md` § Files that
change names — `src/sadana/cli.py`, `src/sadana/subcommands/__init__.py`
(new), `src/sadana/subcommands/conversations.py` (new), `tests/conftest.py`,
`tests/unit/test_cli.py`, `tests/unit/test_conversation_store.py`,
`tests/unit/test_subcommands_conversations.py` (new). No file touched that
the plan didn't name, and no file named that the diff doesn't touch.

`src/sadana/cli.py` and `src/sadana/subcommands/conversations.py` match
`plan.md`'s own proposed code blocks verbatim, checked line by line.

### Compliance pass detail

**Proof items (`plan.md` § Proof):**
- "message-count pluralization" — discharged,
  `tests/unit/test_subcommands_conversations.py:27-32`
  (`test_format_conversation_line_pluralizes_message_count`, asserts the
  exact strings `"k1 (t1, 1 message)"` / `"k2 (t1, 0 messages)"`).
- "listing everything with no query" — discharged, `:35-44`
  (`test_cmd_conversations_lists_everything_with_no_query`).
- "narrowing by query" — discharged, `:47-56`
  (`test_cmd_conversations_narrows_by_query`).
- "an empty-store message" — discharged, `:59-62`
  (`test_cmd_conversations_empty_store_prints_a_clear_message`).
- "read-only behavior (row count unchanged)" — discharged, `:65-75`
  (`test_cmd_conversations_does_not_modify_the_store`), see Nit below on how
  literally this matches the corresponding acceptance criterion's wording.
- "the parser's own registration" — discharged, `:78-89`
  (`test_build_conversations_parser_registers_conversations_with_optional_query`).
- `tests/unit/test_cli.py`'s two new tests — discharged, `:57-66`
  (`test_no_args_names_conversations_as_a_valid_choice`,
  `test_conversations_subcommand_reachable_via_main`); manually reran both
  scenarios outside pytest (`main([])` → `SystemExit(2)`,
  `"choose from 'conversations'"` absent but `"command"` required message
  present; `main(["bogus-command"])` → `SystemExit(2)`, stderr says
  `"invalid choice: 'bogus-command' (choose from 'conversations')"` — matches
  acceptance criteria 4 and 5 exactly).
- "Every pre-existing `test_cli.py` test still passes, unmodified." —
  discharged; `git diff HEAD -- tests/unit/test_cli.py` shows only additions
  after the last existing test, no existing test body touched.
- "`make verify` ends `VERIFY OK`." — discharged, see Evidence (367 passed,
  up from CLI-SHELL-02's 359).

One count in `plan.md` itself is off (see Nit below); every named Proof item
is genuinely present and passing regardless.

**Acceptance criteria (`spec.md` § Acceptance criteria):**
- Two saved conversations → two lines, each naming key/template/count —
  format proven exactly by
  `test_format_conversation_line_pluralizes_message_count`; end-to-end
  listing proven by `test_cmd_conversations_lists_everything_with_no_query`.
- Substring query narrows to what `search_conversations` would return —
  `test_cmd_conversations_narrows_by_query`; `search_conversations` itself
  (case-insensitivity, key/template/content matching) is untouched by this
  diff and already proven in `test_conversation_store.py`.
- Zero matches (including empty store) → one clear stdout message, exit 0 —
  `test_cmd_conversations_empty_store_prints_a_clear_message`; confirmed
  `open_store()` (`src/sadana/conversation_store.py:101-121`) creates the
  schema on a fresh path, so an empty store is a real, exercised case, not
  just an empty-list shortcut.
- `sadana` with nothing after it exits 2, stderr names `"conversations"` —
  `test_no_args_names_conversations_as_a_valid_choice`; reran manually,
  confirmed.
- `sadana bogus-command` exits 2 via argparse's own invalid-choice error —
  the pre-existing, unmodified `test_unrecognized_command_exits_two`; reran
  manually, confirmed the message names `"conversations"` as the only valid
  choice.
- Running the command doesn't change the store — `cmd_conversations`
  (`src/sadana/subcommands/conversations.py:38-47`) contains no write
  statement: it calls only `search_conversations`, which itself issues only
  a `SELECT` (`src/sadana/conversation_store.py:273-295`, unmodified by this
  diff). `test_cmd_conversations_does_not_modify_the_store` checks this via
  `COUNT(*)` before/after rather than the criterion's literal
  "`search_conversations`/`load` return identical results" — see Nit below.
- `make verify` ends `VERIFY OK` — see Evidence.
All satisfied.

**Rejected alternatives (`spec.md` § Rejected alternatives) — drift check:**
- `--query` flag vs. bare positional — implemented as a bare positional
  (`src/sadana/subcommands/conversations.py:22-28`, `nargs="?",
  default=""`). No drift.
- Two subcommands (`list`/`search`) vs. one — implemented as one
  (`"conversations"` with an optional `query`). No drift.
- `"sessions"` vs. `"conversations"` — the word `"conversations"` is used
  throughout the new module and its parser name. No drift.
- Keeping CLI-SHELL-02's bespoke empty-argv check alongside `required=True`
  — the check is deleted, not kept (`git diff HEAD -- src/sadana/cli.py`
  removes the `if not args: parser.error(...)` branch entirely). No drift.
- A new error-handling layer for a store that fails to open — `cmd_conversations`
  has no `try`/`except` anywhere. No drift.

**Five design principles:**
1. Learn from the reference first — `spec.md` § Design cites hermes's
   `hermes_cli/main.py:14098` `sessions` sub-subparsers and
   `hermes_state.py:10854`'s `list_sessions_rich`, adopts the "subparser
   with a query-shaped positional" shape, and explicitly declines the other
   ~18 hermes actions (export/prune/repair/…) with a named reason
   (`cli_shell_blueprint.md` §7 open question 1, not this item's job).
2. Reduce the number of bets — the diff is close to the smallest thing that
   could satisfy the intent: one subcommand, one optional positional, one
   pure formatting helper, no new error-handling layer, no context object.
3. More plugins, not more core — not engaged; this is CLI-SHELL wiring onto
   an existing store function, not new core logic. `cmd_conversations` adds
   no logic `conversation_store.py` doesn't already own.
4. Catch the scenario at the least step-cost — `search_conversations(conn,
   "")` already doubles as "list everything" (CLI-SHELL-01's own design);
   the handler adds no branch to special-case an empty query.
5. Minimise mutable state — `cmd_conversations` opens a connection, reads,
   closes it (`contextlib.closing`); no module-level or cross-call state.

**CLAUDE.md rule — "a handler returns an int … argparse's own exits are
never wrapped or re-raised":** `cmd_conversations` returns `int` on every
path (`src/sadana/subcommands/conversations.py:47`, both the empty-result
`return 0` and the loop's trailing `return 0`). `main()`
(`src/sadana/cli.py:28-30`) calls `build_parser().parse_args(...)` with no
surrounding `try`/`except` — argparse's own `--version`/`--help`/
invalid-choice/missing-required-argument exits propagate as `SystemExit`
completely untouched — then does `return args.func(args)`, which returns
`cmd_conversations`'s own `int`. Compliant.

### Nits

- [Compliance] `plan.md` § Files that change and § Proof both say the new
  test file has "seven tests"; it has six
  (`tests/unit/test_subcommands_conversations.py`), matching `plan.md`'s own
  code sample in § Order of work step 5. Every item the Proof section names
  is genuinely discharged — the "seven" is just a miscount in `plan.md`
  itself, not a gap in coverage.
- [Compliance] `spec.md`'s "the store doesn't change" acceptance criterion is
  worded as "`search_conversations`/`load` before and after an invocation
  return identical results," but
  `test_cmd_conversations_does_not_modify_the_store` checks a coarser proxy
  (`SELECT COUNT(*) FROM conversations` before/after) rather than re-running
  `search_conversations`/`load` and comparing. Low risk in practice —
  `cmd_conversations` has no write statement in it at all — but the test as
  written doesn't literally match the criterion's own wording.

## Decision

Approved by Adam Aubry, 2026-09-08. The two nits (a "seven tests" miscount
in `plan.md`, and the read-only test checking a row count rather than
re-running `search_conversations`/`load`) are accepted as-is — low risk,
not code defects.
