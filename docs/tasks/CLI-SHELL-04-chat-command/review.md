# Review: Actually talking to the agent (from plan.md 2026-09-08)

Reviewed: HEAD (working tree, uncommitted) — 6 files, +334/-9 at review
time; both Important findings below were then fixed in this same branch
(see § Findings for what changed) before a decision was requested.
Reviewer context: fresh session — no prior context on this work item beyond
intent.md, spec.md, plan.md and the diff itself, read cold for this review.
Second opinion: none — plan.md records a build-time `/ponytail-review` +
`/simplify` self-check; not repeated here by design (that is the build
session's own check, not an independent second review).

## Evidence

Original run, before the post-review fixes below:

```
$ make verify
...
377 passed in 4.25s
TESTS OK
VERIFY OK
```

Re-run after both Important findings were fixed (§ Findings):

```
$ make verify
docs/tasks/CLI-SHELL-04-chat-command: all present artifacts valid
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
Success: no issues found in 16 source files
TYPES OK
▶ hermetic run: TZ=UTC LANG=C.UTF-8 PYTHONHASHSEED=0, empty env
........................................................................ [ 19%]
........................................................................ [ 38%]
........................................................................ [ 57%]
........................................................................ [ 76%]
........................................................................ [ 95%]
..................                                                       [100%]
378 passed in 3.98s
TESTS OK
VERIFY OK
```

## Scope

`git diff --stat HEAD`: `CLAUDE.md`, `src/sadana/cli.py`,
`src/sadana/subcommands/chat.py` (new), `tests/conftest.py`,
`tests/unit/test_conversation_store.py`, `tests/unit/test_subcommands_chat.py`
(new) — exactly the six files plan.md § Files that change names, no more, no
less. `src/sadana/subcommands/chat.py` and
`tests/unit/test_subcommands_chat.py` were `git add -N`'d first so they show
in the diff (both new, untracked before that).

## Passes

**Bugs.** Traced `cmd_chat`/`_chat_loop` end to end against the three
scenarios called out for particular attention:

- `--resume <unknown key>`: `conversation_store.load(conn, args.resume,
  now=now)` at `src/sadana/subcommands/chat.py`'s resume branch is called
  with no `try`/`except` around it, inside the `with closing(...)` block,
  before `_chat_loop`/`asyncio.run` even starts. `ConversationNotFound`
  propagates unmodified. Confirmed by
  `test_cmd_chat_resume_unknown_key_raises_conversation_not_found`.
- A turn ending with `ExitReason` other than `COMPLETED`: `_chat_loop`
  checks `result.exit_reason != ExitReason.COMPLETED`, prints
  `f"[{result.exit_reason.value}] {result.detail or ''}"` to stderr, and
  returns `1` — it neither crashes nor loops again. See Important finding
  below for a real gap in *what* gets printed on one specific exit reason.
- Persona read-once: `persona = load_or_seed_persona(...)` is called
  exactly once in `cmd_chat`, before `_chat_loop` is entered, and is
  threaded through as a plain argument (`persona=persona`) that
  `_chat_loop`'s `while True:` body never re-reads or re-derives. Matches
  intent.md's corrected promise and spec.md § Design's citation of
  `build_system_prompt()`'s "once per session" behavior.
- `bind_persist()` discipline (CLAUDE.md's newest "Please do" rule,
  added by this item): `persist = conversation_store.bind_persist(conn,
  conversation, now=now)` sits inside `_chat_loop`'s `while True:` body,
  rebuilt every iteration from the just-reconciled `conversation`, never
  hoisted above the loop. Confirmed by reading the actual diff, not just
  the rule's own wording.
- Sync/async boundary: `cmd_chat` stays a plain `-> int` function; the one
  `asyncio.run(_chat_loop(...))` call is wrapped in
  `try/except KeyboardInterrupt: ... return 130`, matching spec.md's
  Interface. `conversation_store.save()`'s post-turn call is itself wrapped
  in `asyncio.to_thread` inside `_chat_loop` — not in plan.md's literal code
  sketch, but consistent with (and arguably a correction of) `open_store()`'s
  own documented threading contract (`check_same_thread=False` specifically
  because `bind_persist()`'s callback already runs off-thread); not flagged
  as a finding.

**Security.** No secrets, credentials, or PII newly logged or written to
disk. `persona_path_from_config()`/provider-model env overrides follow the
same `config.env`/`config.env_path` convention already used elsewhere for
non-secret, local, user-controlled settings. Approval prompts
(`_default_approve`, unmodified, reused as-is) print the plugin/node/value
to the terminal — pre-existing behavior, not introduced here. No new
subprocess, eval, or unparameterized SQL. Nothing to report.

**Compliance.**

- *Proof items in plan.md*: all four are discharged — the new test file's
  tests exist and pass (`VERIFY OK` above; see the ten-vs-nine count nit
  below), `test_cli.py`/`test_subcommands_conversations.py` ran unmodified
  inside the same `make verify` and are green, `test_conversation_store.py`
  ran after its one mechanical import change and is green, and `make
  verify` itself ends `VERIFY OK`.
- *spec.md § Acceptance criteria*: eight of the nine checkboxes have a
  direct, nameable test — fresh-creates-and-persists
  (`test_cmd_chat_creates_and_persists_a_new_conversation`), one real
  exchange (same test, asserts `model_access.send` was actually called via
  the monkeypatch and the reply is what's printed/persisted), persona
  seeding and verbatim read
  (`test_load_or_seed_persona_creates_default_file`,
  `test_load_or_seed_persona_reads_existing_file_verbatim`), `--resume`
  continuing (`test_cmd_chat_resume_continues_existing_conversation`),
  `--resume`+`--key` rejected at parse time
  (`test_build_chat_parser_rejects_resume_and_key_together`), unregistered
  `--provider` (`test_cmd_chat_unknown_provider_exits_two_before_touching_store`),
  approval prompted
  (`test_cmd_chat_declined_approval_stops_the_call_node_safely`), post-turn
  save reflecting `next_turn_seq`/budget
  (`test_cmd_chat_creates_and_persists_a_new_conversation`), Ctrl-D exit 0
  (`test_cmd_chat_immediate_eof_creates_conversation_with_no_turns`). The
  ninth — "a persona-file edit between conversation creation and a second
  `--resume` run leaves the resumed conversation's own stored prompt
  unaffected" — has no dedicated test in this diff. See Important finding
  below.
- *spec.md § Rejected alternatives*: checked each against the diff. No live
  persona reload (persona is a plain argument, read once). No `approve=`
  override anywhere in `chat.py` (`build_dispatch`'s own default,
  `plugin_manifest._default_approve`, is left alone —
  confirmed at `src/sadana/plugin_dispatch.py:132`). `bind_persist()`
  rebuilt every turn, not reused (see Bugs pass above). Conversation key is
  a human-readable default (`time.strftime("chat-%Y%m%d-%H%M%S")` in the
  actual code vs. spec.md's own sketch's `f"chat-{int(time.time())}"` —
  both timestamp-based and non-opaque, satisfying the actual rejected-
  alternative reasoning even though the exact format drifted from the
  sketch; not worth a finding). `UnknownProvider`/`ProviderNotWired` are
  caught at the boundary, not left to surface raw — no drift found.
- *Five design principles*: (1) Learn from the reference first — spec.md's
  own Design section documents checking hermes's actual
  `build_system_prompt()` code (not a stale comment) and correcting
  intent.md as a result; satisfied. (2) Reduce the number of bets — no new
  abstractions beyond the one persona file and one subcommand; no registry,
  no override point invented for a caller that doesn't need one (spec.md's
  own Rejected alternatives explicitly declined an `approve=` override for
  this reason). (3) More plugins, not more core — `chat.py` is a CLI-SHELL
  subcommand calling already-built `plugin_dispatch`/`plugin_manifest`
  unmodified; nothing added to core. (4) Catch the scenario at the least
  step-cost — the provider-validity check runs first, before persona
  seeding or any store access, confirmed by
  `test_cmd_chat_unknown_provider_exits_two_before_touching_store`
  asserting zero rows afterward. (5) Minimise mutable state — `conversation`
  is rebound each loop iteration (not mutated in place); `dispatch`/
  `tracker`/`persist` are rebuilt fresh per turn rather than accumulated.
  No violation found in any of the five.
- *CLAUDE.md's two accumulated rules*: the CLI-SHELL-02 exit-code rule
  (`cmd_chat -> int`, argparse's own `SystemExit(2)` for the mutually
  exclusive group left untouched and unwrapped) and this item's own
  `bind_persist()`-rebuilt-every-turn rule are both followed, per the
  Bugs-pass trace above.

## Findings

Two Important findings and one Nit, from the Bugs and Compliance passes
above; one further observation recorded as Raised, not a finding.

### Important

- [Bugs/Compliance] `_chat_loop` (`src/sadana/subcommands/chat.py`) drops
  `result.final_text` whenever `result.exit_reason != ExitReason.COMPLETED`,
  printing only `f"[{result.exit_reason.value}] {result.detail or ''}"`
  and returning without ever inspecting `final_text` in that branch. This
  matters for exactly one real path: `run_turn`'s own `BUDGET_EXHAUSTED`
  epilogue (`src/sadana/conversation.py:912-931`) exists specifically to
  produce a best-effort `final_text` summary when the iteration budget
  runs out, while leaving `exit_reason` at `BUDGET_EXHAUSTED` (not
  `COMPLETED`). `_chat_loop` threw that summary away and showed the user
  only the generic budget-exhausted message. `spec.md`'s own Design
  section (`spec.md:196`) wrote the loop sketch as
  `print(result.final_text or f"[{result.exit_reason.value}] {result.detail or ''}")`
  — printing `final_text` when present, falling back to the reason only
  when it's absent — precisely to avoid this.
  **Outcome: fixed.** `_chat_loop` now prints `final_text` whenever it's
  present (regardless of exit reason) and only falls back to the
  bracketed reason line when it isn't; the loop still ends (`return 1`)
  for any non-`COMPLETED` exit reason either way. Verified via
  `conversation.py:912-931`'s actual epilogue behavior before fixing, not
  assumed.
- [Compliance] spec.md § Acceptance criteria's persona-immutability-on-
  resume item ("a conversation started, then a persona-file edit, then a
  second `sadana chat` run against the same `--resume` key: the resumed
  conversation's own stored prompt is unaffected") had no dedicated test
  in this diff. The ten tests in `tests/unit/test_subcommands_chat.py`
  covered persona seeding/verbatim-read and, separately, resuming and
  growing an existing conversation, but never both together. It was
  satisfied only indirectly, by combining the pre-existing, unmodified
  `test_create_then_load_round_trips_every_field`
  (`tests/unit/test_conversation_store.py:110-117`) with a code-reading
  argument that `cmd_chat`'s `--resume` branch never feeds the freshly
  re-read `persona` string into anything that touches
  `conversation.system_prompt`.
  **Outcome: fixed.** Added
  `test_cmd_chat_resume_keeps_original_persona_after_a_later_edit`:
  creates a conversation, edits the persona file on disk, resumes the
  same conversation, and asserts its `system_prompt` is byte-identical
  to what it was before the edit. The file now has eleven tests.

### Nits

- [Compliance] plan.md § Proof said "`tests/unit/test_subcommands_chat.py`'s
  nine tests cover..." — the file actually had ten
  `@pytest.mark.unit`-marked tests at review time (confirmed by count),
  now eleven after the second Important finding's fix.
  **Outcome: fixed** — plan.md's count corrected to eleven, and its own
  "Post-review fixes" section now records both this and the count being
  wrong twice, not just once.

### Raised, not findings

- `build_dispatch`'s `stable_prompt` argument (fed from `cmd_chat`'s local
  `persona`) is also what `run_child` uses to compose a spawned child
  skill's own system prompt fresh at spawn time
  (`src/sadana/conversation.py:1266-1281`, by design, documented there as
  "the same posture `create_conversation`'s `system_message` already
  has"). Within one `sadana chat` invocation this is harmless (persona is
  read once and held constant for the whole run). Across two separate
  `--resume` runs of the same long-lived conversation with a persona edit
  in between, the top-level turns keep the old, frozen
  `conversation.system_prompt`, but any child skill spawned during the
  *second* run would pick up the newly-edited persona text as its own
  `stable_prompt` — a real asymmetry, but one that's inherent to
  `run_child`'s already-shipped design (not introduced by this diff), and
  spec.md's Acceptance criteria never scoped child-spawn prompts in.
  Worth knowing about if a future work item ever tightens the persona-
  freeze guarantee.

## Decision

Approved by Adam Aubry, 2026-09-08, with both Important findings fixed
in this branch (see § Findings for what changed) before approval.
