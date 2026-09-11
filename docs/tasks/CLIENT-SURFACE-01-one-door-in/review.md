# Review: One door in (from plan.md 2026-09-11)

Reviewed: 9d6fcc9..working tree — 21 files, +1934/-501
Reviewer context: two cold subagent reviews with no context beyond the diff and
the three artifacts — one running Bugs + Security, one running Compliance. The
scope comparison in step 3 and the fixes below were done in the build session.
Second opinion: none — ran during build (self-check), not repeated here by design.

## Evidence

```
$ make verify
docs/tasks/CLIENT-SURFACE-01-one-door-in: all present artifacts valid
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
Success: no issues found in 41 source files
TYPES OK
▶ hermetic run: TZ=UTC LANG=C.UTF-8 PYTHONHASHSEED=0, empty env
........................................................................ [ 10%]
........................................................................ [ 20%]
........................................................................ [ 31%]
........................................................................ [ 41%]
........................................................................ [ 52%]
........................................................................ [ 62%]
........................................................................ [ 73%]
........................................................................ [ 83%]
........................................................................ [ 93%]
..........................................                               [100%]
690 passed in 20.50s
TESTS OK
VERIFY OK
```

Outside `make test`, which may not reach a network or bind a socket — two
standalone proofs of the rewired path, re-run after the last change:

```
$ .venv/bin/python scripts/prove_gateway_wait_e2e.py
=== POST 1: starts the job, the DAG pauses at the wait node ===
status=200 body={'ok': True, 'text': "okay, I'll wait for it."}
[ok] the webhook response is the model's own acknowledgement
[ok] real plugin_pauses row: plugin='plugin-d' node='await_answer'

=== POST 2: the external system's answer resumes the run, with no model call ===
status=200 body={'ok': True, 'text': 'the external job answered: 42'}
[ok] the resumed run reached its terminal node
[ok] the plugin_pauses row is gone
[ok] model_access.send was called exactly 2 times

ALL ASSERTIONS PASSED
[ok] gateway_daemon.run() returned 0 after a clean stop

$ .venv/bin/python scripts/prove_gateway_scheduling_e2e.py
[ok] tick fired 1 trigger(s)
[ok] no backlog — advanced from the real time of firing, one interval forward
ALL ASSERTIONS PASSED
[ok] gateway_daemon.run() returned 0 after a clean stop
```

## Findings

Two cold reviewers found the same Important behaviour change independently
(F1), which is the one thing in this diff a person has to decide rather than
me. Five findings were fixed in this branch during review and are listed with
their resolutions; they are not pending.

### Important

- **[Bugs] F1 — a configured wall-clock run budget no longer fires in `sadana
  chat`, and no artifact sanctions it.** `client_surface.take_turn` captures
  one `now` (`client_surface.py:334`), reloads the conversation with it
  (`conversation_store.load` rebuilds `deadline = now + wall_clock_remaining_s`,
  `conversation_store.py:314`) and saves with the same `now`, so
  `wall_clock_remaining_s` round-trips unchanged and never decreases. Before
  this change `_chat_loop` carried the `Conversation` across turns and the
  deadline was pinned at process start. Reproduced by the cold reviewer with
  `SADANA_CONVERSATION_RUN_BUDGET_SECONDS=1` and a 2s gap between turns: the
  new code completes the second turn, the old shape returned
  `[wall_clock_exhausted] wall clock budget exhausted` on stderr with exit code
  1. Latent by default (the budget is off unless that variable is set) and it
  converges the terminal onto the webhook's already-per-turn behaviour, so it
  may well be the behaviour we want — but `spec.md:39-44` claims byte-for-byte
  parity "with requirement 4 as the single exception" and this is a second one.
  **Not fixed, deliberately: it is a behaviour decision, not a defect to
  patch.** Two candidate fixes, both small: save with a fresh `time.monotonic()`
  so a turn's own elapsed time is deducted (restores a cumulative in-turn
  budget, still not idle time), or declare the budget per-turn on purpose and
  amend `spec.md` in the next work item.

- **[Bugs] F2 — `conn_lock` is held across an unbounded approval prompt, and
  the module docstring claimed otherwise.** A `call` node's gate is
  `plugin_manifest._default_approve`, which awaits `asyncio.to_thread(input, …)`
  with no timeout (`plugin_manifest.py:271-282`), and neither `client_surface`
  nor `gateway_dispatch` overrides it. The cold reviewer reproduced the lock
  being held with an approval outstanding while `scheduling.tick` failed to
  complete within 3s. In `cmd_gateway_run` one webhook message reaching a
  `call` node therefore stalls every other webhook thread and the whole
  scheduler tick loop until stdin answers, which in a service never happens.
  The liveness defect is inherited from the bridge this module replaced, not
  introduced — but the docstring is now the declared contract (spec.md
  requirement 13) and its "what a turn does not survive" list omitted this
  while asserting that a scheduler tick landing mid-turn is survivable.
  **Fixed in this branch:** `client_surface.py:74-96` now names the condition,
  its blast radius, and that it is unfixed. The fix itself — an `approve`
  parameter so a non-interactive client can fail closed — is a new work item,
  not this one.

- **[Compliance] F3 — six files are touched that `plan.md` § Files that change
  does not name, and none is in its § Announced deviations.**
  `src/sadana/conversation_store.py` (new public `exists()`),
  `tests/conftest.py` (the shared `make_runtime`, `tool_then_text`,
  `never_send` helpers) and four `scripts/prove_*.py`. Each arose from the
  self-check step the plan sanctioned in general terms, but the file list was
  never updated, so the artifact chain does not describe the code. **Not
  fixed:** editing `plan.md`'s file list now would be writing the plan the
  work turned out to need rather than the one that was approved.

- **[Compliance] F4 — two `plan.md` § Proof claims are false as written.**
  `plan.md:246` states "`git diff --stat` shows `conversation_store.py`
  untouched"; the file is touched (+10, a new `exists()`). The substantive
  half survives and was verified — the `schema` literal
  (`conversation_store.py:40-100`) is unchanged, so `spec.md:341`'s "no new
  table or column" criterion holds. `plan.md:239-241` claimed
  `test_subcommands_chat.py` proves the terminal's rendering; it contained no
  `capsys` at all. **Second half fixed in this branch:**
  `test_subcommands_chat.py:205-236` adds two stream tests, including the
  `BUDGET_EXHAUSTED` case proving the epilogue reaches stdout with only the
  exit code reporting failure.

- **[Compliance] F5 — the two-thread serialization test promised by
  `plan.md:219-221` and required by `spec.md:333` did not exist.** What shipped
  was a single-threaded check whose own docstring conceded it proved "the
  wiring, not real concurrency". `spec.md` § Concerns had singled this
  criterion out as the proxy for the document's weakest guarantee, which makes
  it the worst place to downgrade quietly. **Fixed in this branch:**
  `test_client_surface.py:249-300` runs two real threads released together by a
  `threading.Barrier` against one runtime, with a recording lock asserting
  `acquisitions == 2` and `max_held_at_once == 1` — an invariant that can only
  fail if an overlap genuinely happened, never on timing.

- **[Compliance] F6 — `spec.md:328-329`'s claim that
  `conversation.create_conversation` has exactly one call site in `src/` is
  false.** `eval_harness.py:124` is a second, verified by grep. Disclosed in
  `plan.md` § Announced deviations 4 and reconciled in `CLAUDE.md:61`, which as
  committed claims single-site only for `build_dispatch` and
  `take_turn_and_reconcile` — both verified true by both reviewers — and names
  `eval_harness.py` as the sanctioned exception, because an eval task is a
  hermetic single turn with no store, no account and a stubbed dispatch.
  **`spec.md` deliberately left unreconciled** so this stage reports it rather
  than the chain being edited to agree with the code.

- **[Bugs] F7 — running `scripts/prove_gateway_wait_e2e.py` wrote into tracked
  repository files.** `open_runtime()` calls
  `memory_store.ensure_plugin_seeded(plugins._plugins_root())`, and that script
  pointed `SADANA_PLUGINS_DIR` straight at the tracked
  `tests/fixtures/plugins`, so the proof seeded a `memory/` plugin directory
  into the repository it was proving — three files byte-identical to
  `src/sadana/builtin_plugins/memory/`. Introduced by this diff (the script did
  no seeding before), and invisible to `make verify`: the suite's plugin
  assertions are subset checks (`test_plugin_manifest.py:818`). **Fixed in this
  branch:** the script copies the fixtures into its own temp state dir
  (`prove_gateway_wait_e2e.py:43-51`), the same way
  `test_subcommands_chat.py:186-190` already solved the identical hazard; the
  staged artefacts and their `__pycache__` are removed and the script re-run
  clean.

### Nits

- **[Bugs]** A clean resume whose text is empty prints a bare newline to
  stderr (`subcommands/chat.py:71-74` with `client_surface.py:326`). New — the
  terminal had no resume path before. Not fixed: the obvious repair, testing
  `answer is not None`, would change what a turn with `final_text == ""` does
  versus HEAD, which requirement 6 forbids.
- **[Bugs]** `open_runtime` leaks the connection if `ensure_schema` or
  `make_recorder` raises after `open_store` succeeded
  (`client_surface.py:142-150`); `cmd_chat`'s `closing()` only wraps an
  already-built `Runtime`. A process failing to open the door is about to exit.
- **[Security]** `open_runtime` is a second place `.env` enters the process
  (`client_surface.py:138`); before, only `cli.main()` did. Contained —
  `load_dotenv` never overrides a live variable (`config.py:53`) and conftest
  redirects the state dir — but it is now a library-level side effect rather
  than an entrypoint one.
- **[Compliance]** Two of `spec.md`'s thirteen acceptance criteria read against
  the name `Surface`, which `plan.md` deviation 1 renamed to `Runtime`. Tick
  them by intent, not by text.
- **[Compliance]** `test_gateway_dispatch.py:40` renames
  `test_handle_inbound_records_the_turn_when_given_a_recorder` and rewrites its
  docstring, where `spec.md:337-338` says that file passes "unchanged except
  for construction". Every assertion and the `turn_runs` SELECT are
  byte-identical; the recorder is now a `Runtime` field rather than a
  parameter, so the old name asserted a condition that no longer exists.

### Clean, and what was checked to say so

Both reviewers verified: `client_surface.py` imports none of `gateway`,
`channel_webhook`, `scheduling`, `editor_server` or `subcommands.*` (only
docstring prose mentions them); every one of `spec.md`'s six rejected
alternatives held, including `gateway.py` being wholly absent from the diff;
history is mutated only via `conversation.append` plus `dataclasses.replace`,
never a direct list or tuple mutation; `bind_persist` is rebuilt inside every
turn and never held on the `Runtime`; `_create` is lock-free with both call
sites inside `with conn_lock`; `scheduling.tick` still calls the bridge
*outside* both of its own lock blocks, with `test_scheduling.py:131`'s
`acquisitions == 2` guarding it; no registry or seam was invented;
`client_surface.py` prints nothing and takes no printer; every caller-supplied
value reaches SQL only as a bound parameter, including the new `exists()`;
nothing in the diff turns a name into a filesystem path or a CLI positional; no
test reads source; and the names-not-pointers rule is strictly better than
before, since `_chat_loop` held a live `Conversation` across turns and now
holds a key.

## Raised, not findings

- **Renaming a plugin in the editor makes every outstanding pause for it
  unresumable.** `resume_paused_run` rebuilds the directory as
  `_plugins_root() / pause.plugin`, where `pause.plugin` is `manifest.name`,
  which `validate()` never checks against the directory name, while
  `editor_server._write_manifest` writes a renamed manifest into the unchanged
  directory. The pause row is then deleted and the person told the plugin no
  longer resolves — a paused run silently lost. Predates this diff; this is the
  first change to expose that path to `sadana chat`. Worth a work item.
- **`make typecheck` runs `mypy src` only** (`pyproject.toml:34`,
  `Makefile:30`) and `scripts/` has no test coverage, so the project's mandated
  Deploy-evidence scripts are the one thing nothing checks even imports. That
  is why both my own `_tick` signature mismatch and a pre-existing breakage sat
  there unseen.
- **`prove_gateway_webhook_e2e.py` and `prove_setup_fresh_instance.py` call
  `gateway_daemon.run(host=…, port=…, on_message=…)`**, a signature that
  stopped existing in an earlier work item. Pre-existing rot, found by running
  them; a maintain-stage finding, not this item's to fix.
- **Three more scripts point `SADANA_PLUGINS_DIR` at the tracked fixtures
  root** (`scripts/eval/tasks/plugin_dispatch.py:92`,
  `prove_conversation_e2e.py:63`, `prove_plugin_dispatch_e2e.py:103`) — safe
  only while none of them calls `open_runtime()`. Same hazard as F7.
- **The terminal now re-reads its conversation from SQLite every turn**, where
  `_chat_loop` used to hold it in memory, so a long session is O(N²) in reads.
  Deliberate per `spec.md`'s state inventory and what makes two clients on one
  store safe; worth knowing before someone benchmarks a 200-turn session.
- **`load_pause` parses its JSON twice per resume** and the trailing `save`
  re-writes the whole history. Both pre-existing; both now single-sited, which
  is the cheap moment to fix them once for every client.

## Decision

Approved by adam aubry, 2026-09-11.

F1 is accepted as shipped rather than patched: for `sadana chat` a configured
wall-clock run budget now behaves per-turn instead of per-session, which is the
behaviour the webhook path already had, so the two doors agree — which is what
this work item existed to achieve. Neither candidate fix was applied. The
consequence is recorded rather than hidden: `spec.md:39-44`'s byte-for-byte
claim now has two exceptions, not one, and amending it belongs to a follow-up
work item, not to this closed chain.

The two unreconciled chain gaps (F3, F4) stand as findings. `plan.md` was not
edited to name the six extra files or to correct its two false Proof claims,
because the approved plan is the record of what was decided, not of what the
work turned out to need.
