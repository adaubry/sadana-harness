# Review: Somewhere to put what a plugin makes (from plan.md 2026-09-11)

Reviewed: dfd4552..working tree — 11 files, +559/-45 (8 tracked, 3 new)
Reviewer context: cold — delegated to a subagent with no context beyond the
diff and the three artifacts, per deploy-skill § 1. Its findings were then
fixed in the build session and `make verify` re-run.
Second opinion: a combined simplification/altitude pass ran during Build
(self-check); its seven findings are summarised under Findings, not repeated
here.

## Evidence

```
$ make verify
docs/tasks/ARTIFACT-STORE-01-somewhere-to-put-what-a-plugin-makes: all present artifacts valid
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
Success: no issues found in 42 source files
TYPES OK
▶ hermetic run: TZ=UTC LANG=C.UTF-8 PYTHONHASHSEED=0, empty env
........................................................................ [  9%]
........................................................................ [ 18%]
........................................................................ [ 27%]
........................................................................ [ 37%]
........................................................................ [ 46%]
........................................................................ [ 55%]
........................................................................ [ 64%]
........................................................................ [ 74%]
........................................................................ [ 83%]
........................................................................ [ 92%]
.........................................................                [100%]
777 passed in 17.38s
TESTS OK
VERIFY OK
```

## Findings

The cold review raised five Important findings and five nits. All ten were
acted on before this file was written, per deploy-skill § 9. Notably, **none
of the five Important findings was a defect in the shipped behaviour** — three
were tests that did not test what they claimed, and two were artifacts that
described code other than the code that landed. That distribution is itself
the finding worth reading: the risk in this work item was never the logic, it
was the proof.

### Important — fixed in this branch

- **A test claimed to cover the `plugin_pauses` migration and did not go near
  it.** `test_a_pause_row_written_before_these_columns_still_loads` opened a
  *fresh* store — whose `_SCHEMA` already declares both new columns — and
  inserted a row omitting them. That exercises the `NULL` read fallback and
  nothing else. Meanwhile every store already on disk has a `plugin_pauses`
  without those columns, and `load_pause`'s `SELECT` names them, so a wrong
  `ALTER` would have failed on the first `open_store` against a real store with
  nothing in the suite to say so. **Fixed:** the test now builds the
  pre-migration table by hand with `sqlite3` — the only way to reach that
  branch — opens it through `open_store`, asserts the legacy row still loads
  with both fields `None`, and reopens to prove idempotency.

- **The riskiest line in the diff had no test at all.** `seq_in_turn += 1`
  moved *below* the pause branch so that the directory a run was named from and
  the sequence number its pause records are the same value. That is the entire
  mechanism behind requirement 7. The only resume test fakes `run_graph` and
  proves `resume_paused_run` *reads* a row handed to it; nothing asserted what
  `dispatch` *writes*. Move that increment back where it was and every resumed
  run silently writes into the next run's directory, with `make verify` green.
  **Fixed:** `test_a_pause_records_the_same_sequence_number_its_directory_was
  _named_from` captures both the `output_dir` each dispatch is given and the
  `(TurnKey, seq_in_turn)` each pause records, and asserts they agree across
  two runs. Proved by reintroducing the reordering and watching it fail, then
  restoring.

- **The `test_plugin_dispatch` assertions were tautological.**
  `assert tmp_path in directory.parents` is true regardless of the test's own
  `SADANA_STATE_DIR` monkeypatch, because the autouse fixture already puts the
  state directory under `tmp_path`; `assert directory.parts[-3:-2] != ()` is
  true of any non-empty path. Neither the conversation-key encoding nor the
  turn number was asserted anywhere — both could have dropped out of
  `for_run`'s path with the test still green. **Fixed:** the test now asserts
  the directory equals `artifact_store.for_run("c1", 0, 0)` and that each path
  component is the value it should be.

- **`plan.md` § Risks and § Proof still asserted the behaviour the
  implementation deliberately changed.** `spec.md` was corrected during Build
  to say a `kind="file"` artifact is refused when a run has no output
  directory; `plan.md` went on claiming "the containment check cannot fire when
  there is nothing to be contained by" and listing a test for
  "`output_dir=None` behaving exactly as it does today" that cannot exist. The
  amendment section recorded two other deviations and was silent on this one.
  **Fixed:** both sentences corrected in place with the original wording
  quoted, and the omission itself recorded in the amendment.

- **`plan.md` understated the new module's surface.** It named six functions;
  `for_run` and `contains_active` also shipped, and both are load-bearing —
  `for_run` is what both `plugin_dispatch` call sites use, and
  `contains_active`, not `contains`, is what `run_graph` calls. **Fixed** in
  the file list and the amendment.

### Nits — all five fixed

- `contains()` raised `ValueError` rather than answering for a string
  `pathlib` rejects (`Path("a\0b").resolve()`), which a `bool` predicate must
  not do. Now caught alongside `OSError`, with a test.
- `contains()` answered true for the directory itself, so a body could record
  a folder as a file artifact. Now strictly inside, with a test.
- `dispatch` passed `conversation.key` where `turn_key.conversation` — the
  same value, already in hand — was the key the run had minted. Now the
  latter, which is the habit this whole work item is built on.
- `_noop_persist_pause` had widened to `*_args`, erasing the callback's shape
  from the one place a reader looks for it. Named parameters restored.
- A conversation key with no ASCII-lowercase characters collapses to a bare
  digest, so a non-Latin conversation name loses the readability the encoding
  exists for. **Accepted, not fixed** — see below.

### Important — accepted, not fixed

- **A non-Latin conversation key produces an unreadable directory name.**
  `run_dir_name("ключ")` is `"1de36a32"`. The encoding is ASCII-only because
  the output is a path component and widening the allowlist is how path bugs
  start. The cost is real and falls on exactly the users least likely to be
  asked about it. Recorded rather than patched, because widening the allowlist
  is a decision about what a path may contain, not a nit.

### Raised, not findings

- **`output_dir()` is reachable from a `compute` body, not only a `call`
  body.** The activation wraps the whole walk, so a `compute` node — which
  `G2-artifacts` leans on being "defined as pure" — can now create a directory
  and write a file, synchronously, on the event loop. The artifact *emission*
  restriction is untouched: the `isinstance` arm is still inside the `call`
  branch only, so no non-goal is violated. But the purity invariant G2 cites is
  weaker than that spec implies, and nothing in either document says so. Worth
  an `intent.md` if `compute` purity is meant to be enforced rather than
  assumed.
- **`intent.md` and `spec.md` word one promise differently.** The intent says
  the directory is created "only when something actually writes to it";
  `spec.md` requirement 2 says "created on the first ask, not before", and the
  code creates on the ask. So a body that calls `output_dir()` and then raises
  leaves an empty directory. It is a decided trade, not drift — noted because
  the intent's wording is what a later reader will quote.
- **TOCTOU between the containment check and the recorded ref.** `run_graph`
  checks the *resolved* path and records the *unresolved* string, so a symlink
  swapped in between would make the recorded ref point elsewhere. Inert today:
  nothing consumes `DagResult.artifacts`, and the body doing the swapping
  already runs in-process, which `spec.md` § Concerns puts out of scope. Worth
  remembering when a consumer lands.
- **Nothing re-checks that the computed run directory resolves inside
  `state_dir`.** `contains()` checks a candidate ref against the run directory,
  which is a different check. Defence-in-depth only — the reviewer probed
  `a/b`, `../escape`, `-leading`, `..`, `/`, `""`, `"."`, `"\0"`, a 200-char
  key and a 48-char key ending in `.`, and could not construct an escape, since
  every output is one component drawn from `[a-z0-9._-]`, never `.` or `..`,
  never leading `-`, always digest-suffixed.
- **The disk only grows.** No expiry, no cap, no cleanup — `intent.md`'s open
  question, and the next blueprint item is image generation, which is the first
  thing that will fill it.

## Decision

Approved by adam, 2026-09-11, with every Important finding and all five nits
already fixed in this branch before the decision was given. Two items were
accepted as read rather than fixed: a non-Latin conversation key producing an
unreadable directory name, and the fact that nothing deletes anything. The
`compute`-body purity observation under "Raised, not findings" is left as a
candidate `intent.md`, not folded into this item.
