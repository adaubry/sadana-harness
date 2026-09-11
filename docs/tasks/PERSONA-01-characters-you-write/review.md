# Review: Characters you write (from plan.md 2026-09-11)

Reviewed: 985ee13..working tree — 27 files, +1900/-103
Reviewer context: two fresh subagents with no session context — one running the
Bugs and Security passes, one running the Compliance pass — each given only the
diff and the artifacts. Their findings are reproduced below; this session
reconciled them, applied fixes, and wrote this file.
Second opinion: `/ponytail-review` + `/simplify` ran during Build (self-check),
not repeated here by design.

## Evidence

```
$ make verify
docs/tasks/PERSONA-01-characters-you-write: all present artifacts valid
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
Success: no issues found in 43 source files
TYPES OK
▶ hermetic run: TZ=UTC LANG=C.UTF-8 PYTHONHASHSEED=0, empty env
........................................................................ [  9%]
........................................................................ [ 19%]
........................................................................ [ 28%]
........................................................................ [ 38%]
........................................................................ [ 47%]
........................................................................ [ 57%]
........................................................................ [ 67%]
........................................................................ [ 76%]
........................................................................ [ 86%]
........................................................................ [ 95%]
................................                                         [100%]
752 passed in 22.67s
TESTS OK
VERIFY OK
```

`plan.md` § Proof's last item is a hand run of the fresh-instance proof, which
is outside the suite by design. Run after the fixes below:

```
$ .venv/bin/python scripts/prove_setup_fresh_instance.py
=== 2. a fresh `sadana setup` (no flags, no TTY) after the .env exists ===
exit=0
[ok] values were read back from the .env and left alone — nothing to fill, exit 0
=== 3. a fresh `sadana setup` (no flags, no TTY) with nothing stored ===
exit=1
[ok] reported both missing by name, wrote nothing, did not hang
=== 4. config.load_dotenv() reads the stored .env in a live process ===
[ok] both keys are in the live environment after load_dotenv()
=== 5. before setup: real send() needs a credential, no network ===
outcome=NeedsCredentialOrProviderChange(detail='missing env var(s): OPENROUTER_API_KEY')
[ok] real send() returned NeedsCredentialOrProviderChange (zero network)
=== 5b. after setup: a real `sadana chat` turn completes with the stored key ===
exit=0 chat-output='> echo: ping\n>'
[ok] `sadana chat` completed a real turn with the stored key in the environment
=== 6a. gateway_daemon.run() without a secret refuses to start ===
exit=1
[ok] run() refused to start without a secret
=== 6b. gateway_daemon.run() starts with the stored secret ===
[ok] gateway bound 127.0.0.1:18767 with the stored secret
http=200 body={"ok": true, "text": "echo: ping"}
exit=0
[ok] a real daemon ran with the stored secret and stopped cleanly
ALL ASSERTIONS PASSED
```

That run is also the evidence for acceptance criterion 11: a fresh instance
completes a real `sadana chat` turn and no persona file is created anywhere.

The signature change in this diff broke call sites `make verify` cannot see
(`scripts/` is outside `testpaths` and outside `mypy src`). After the fix, a
one-off AST sweep of every `build_dispatch(` call site in the repo:

```
$ python check_calls.py
ok   scripts/eval/tasks/plugin_dispatch.py:106
ok   scripts/prove_conversation_e2e.py:108,118,157,187,212
ok   scripts/prove_plugin_dispatch_e2e.py:135,160
ok   src/sadana/client_surface.py:368
ok   tests/unit/test_plugin_dispatch.py:151,239,257,343,368,390,416,442,488,517,540

20 call sites, 0 stale
```

`scripts/prove_conversation_e2e.py` and `scripts/prove_plugin_dispatch_e2e.py`
were **not** run: both require a live `OPENROUTER_API_KEY` and make paid
network calls, which is not something to spend without being asked. Their call
sites are fixed and statically verified above; a real run of both is the
honest close on that finding and is one command away on request.

## Findings

Five Important and five Nits, from three passes run cold by two subagents that
had the diff and the artifacts and nothing else. Six are fixed in this branch
with tests; four are open and two of those want a decision from a person, not
a patch. Each finding says which.

### Important

- [Bugs, FIXED] Removing `build_dispatch()`'s `stable_prompt` parameter broke
  four call sites in `scripts/` that `plan.md` had recorded, in writing, as
  not existing — `scripts/eval/tasks/plugin_dispatch.py:106`,
  `scripts/prove_conversation_e2e.py:108`, and
  `scripts/prove_plugin_dispatch_e2e.py:135` and `:160`. Each would have
  raised `TypeError: build_dispatch() got an unexpected keyword argument` on
  its first call, which for the eval task means EVAL-02's only real-plugin
  eval could not run at all. The suite stayed green throughout, because
  `pyproject.toml` sets `testpaths = ["tests"]` and the Makefile runs
  `mypy src`. Fixed in this branch (the four call sites now pass nothing, and
  the conversation they already hold supplies the value), and the AST sweep
  above is the evidence. `plan.md`'s wrong paragraph is corrected in place
  with a note saying it was wrong.

- [Bugs, OPEN — needs a decision] The webhook and schedule surfaces now speak
  in the neutral voice, and nothing a person is likely to type will change
  that. `client_surface._create()` resolves the voice from `account`, but the
  account keys reaching it on those two surfaces are machine-made:
  `gateway_dispatch.py:55` produces `webhook:<chat_id>` and `scheduling.py:73`
  produces `schedule:<trigger_name>`. `sadana persona use` takes a literal
  account string with no default and there is no command that lists account
  keys, so in practice both surfaces are stuck neutral, where before this diff
  they used the operator's single `persona.md`. `intent.md` promises the
  opposite in as many words: "in a reply it sends back through a webhook, in
  something a schedule started while they were asleep". Renaming a scheduled
  trigger also silently changes its account key and drops its voice. Every new
  test uses `account="a1"`, which is why this is green. Two candidate
  resolutions, neither taken here because both are design decisions this
  work item's spec never made: accept it as the honest meaning of per-account
  (a webhook conversation belongs to its chat, not to the operator, and
  `persona use webhook:<chat_id> <name>` does work), or add a fallback
  character for accounts with no selection of their own — which is a new
  concept and belongs in its own intent.

- [Bugs, OPEN — inherent, documented] On a conversation row written before C12
  added `stable_prompt_len`, `Conversation.stable_prompt`
  (`conversation.py:1053`) returns the entire system prompt — the voice *and*
  the context tier — because `conversation_store.py:335` defaults the column
  to `len(system_prompt)`. Live consequence: an `ask` node inside such a
  conversation now builds its child from the parent's memories and plugin
  catalog as well as the voice, where before it used the process-wide persona.
  The property's docstring claim of "byte-exact" is false for exactly this row
  shape. Latent consequence: the new `rotate_prompt` guard would reject any
  compressed rotation of such a row with `PromptDriftError`, turning a
  silently over-wide prefix into a dead turn — unreachable today only because
  `context.py:229` never returns a `new_system_prompt`. There is no way to
  recover the real split for a legacy row, so this is not fixable, only
  chosen: `plan.md` § Risks discloses it, and a regression test now pins the
  half that must hold — such a row still answers
  (`test_client_surface.py::test_a_conversation_row_written_before_stable_prompt_len_still_answers`).

- [Compliance, OPEN] The build changed a public contract in a module the spec
  did not scope. `spec.md` § Design describes "two new modules and one
  rewritten one, a new table, and two lines of `client_surface.py`", and
  § Concerns asked only for "an assertion at the slice". `rotate_prompt`
  (`conversation.py:1129`) now raises `PromptDriftError`, which is a new
  failure mode on `compact()`'s path in the CONVERSATION block. The reasoning
  is recorded in `plan.md` § Order of work step 4 (an assertion at the slice
  is vacuous, since `s[:n]` is a prefix of `s` by construction) and it is
  sound, but it never went back through Design. Keeping it is defensible;
  what is not defensible is leaving it unremarked, so it is a finding.

- [Compliance, FIXED] `persona use` reported success for an account nothing
  reads. `sadana chat` resolves its account as `args.account or
  config.env("SADANA_MEMORY_ACCOUNT", "local")` (`subcommands/chat.py:85`), a
  default `persona use` did not share, so `persona use adam working` printed a
  confirmation and then changed nothing a person would hear. Not fixed in
  code — see the Decision request below; it is the same question as the
  webhook/schedule finding and should be answered once, not twice. Recorded
  here rather than silently patched.

### Nits

- [Bugs, FIXED] A character file checked out with CRLF line endings failed as
  "missing its frontmatter delimiter" and, through `resolve_voice`'s total
  contract, degraded silently to the neutral voice — for files the intent
  expects people to keep in version control and move between machines.
  `persona._split_frontmatter` now normalizes line endings, with a regression
  test (`test_persona.py::test_parse_accepts_a_crlf_checkout_of_the_same_file`).
- [Bugs, FIXED] `persona new` on a read-only or full disk escaped as a
  traceback while every sibling verb printed `error:` and returned 1. Now
  catches `OSError` too, with a test.
- [Compliance, FIXED] The guard's message and the new CLAUDE.md rule both said
  "bytes `[0:stable_prompt_len]`", but that value is a character count.
  Reworded to `system_prompt[:stable_prompt_len]` in both places.
- [Compliance, OPEN] `persona_selections.updated_at` is written and never read.
  It mirrors `memory_rubric_overrides`' existing shape, so it is consistent
  with the repo rather than novel, but it is stored state with no reader.
- [Bugs, OPEN] A character file that fails to parse is skipped by
  `persona list` with no hint it exists, so a typo in frontmatter presents as
  the character having vanished. `persona show <name>` explains it; nothing
  points the person there. The skipping itself is specified (requirement:
  one malformed file must not hide the others).

### What was checked and found clean

- **Security pass**: both gates on the caller-supplied name hold.
  `persona.valid_name` is a `fullmatch` allowlist (no `/`, no `.`, no NUL, no
  leading `-`, length-capped) run before the name touches a path, and
  `is_relative_to` on the *resolved* path runs after. The cold reviewer tried
  four bypasses — a symlink to `/etc/passwd`, a directory symlink, a symlinked
  characters root, an internal symlink — and all behave correctly. A hostile
  `persona_selections` row is routed back through the same two gates. No
  f-string SQL, no secret in source, and the account key is only ever a bound
  parameter. The one residual is a TOCTOU between `resolve()` and the
  following open, which requires write access to the characters directory —
  i.e. the ability to edit the character anyway.
- **File list**: every file in the diff appears in `plan.md` § Files that
  change and vice versa, after two backfills during Build and one after the
  cold review.
- **Proof**: every item in `plan.md` § Proof is discharged by a named test,
  listed by the compliance reviewer one at a time; the last (the hand-run
  proof script) is in § Evidence above.
- **Acceptance criteria**: all twelve satisfied, each traced to a file and a
  test function. Criterion 10 was half-covered at first — a legacy row was
  shown to load but not to answer — and a test for the second half was added.
- **Rejected alternatives**: all seven re-read against the diff. No drift.
  In particular no character text is stored anywhere but the file, the
  `persona_selections` table holds only a name, `conversation_store.py` is
  untouched, and `plugins.py` gained no caller.
- **Design principles**: prior art faithfully cited and priced
  (`hermes_cli/personality.py:37` and `:84-93` adopted, the config-as-store
  half declined with reasons); no registry, Protocol or seam invented; the
  per-turn path got *lighter*, not heavier; and the only new stored state is
  one name per account.

### Raised, not findings

- An `ask` node reached with the raw tool-call arguments cannot run through
  `client_surface` at all: `build_dispatch` merges a live `DispatchContext`
  under `_sadana_memory_ctx` and `plugin_manifest._coerce_text` JSON-dumps the
  arguments dict, so the node dies as "did not complete" with the real
  `TypeError` swallowed by `run_graph`'s catch-all. Found while writing this
  work item's child-turn test, which routes around it with a `compute` node
  and says so in a comment. It is a PLUGINS defect, not this item's, and it
  wants its own `intent.md` — the comment should not be the only record.
- `scripts/` has no verification at all: not in `testpaths`, not in
  `mypy src`, imported by no test. That is what let this diff break four call
  sites silently, and it is the second time that gap has been noticed. A work
  item that puts `scripts/` under `mypy` would have caught this one at
  `make typecheck`.

## Decision

Approved by Adam, 2026-09-11.

The approval was given without answering the question this review asked, so it
is recorded as approval of the change as it stands, not as an answer: the
webhook and schedule surfaces ship speaking the neutral voice, and `persona
use` ships taking a literal account string with no default. Both remain open
findings, and the question they share — what a surface whose account key
nobody chose should speak in — is unresolved rather than settled. It wants its
own `intent.md`, as does the `ask`-node defect under "Raised, not findings".

The six fixed findings are fixed in this branch with tests. The legacy-row
finding is accepted as inherent and disclosed in `plan.md` § Risks. The
compliance finding about `rotate_prompt`'s contract change is accepted with
its reasoning recorded in `plan.md` § Order of work step 4.
