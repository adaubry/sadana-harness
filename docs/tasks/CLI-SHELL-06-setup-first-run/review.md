# Review: setup first run (from plan.md 2026-09-08)

Reviewed: aac171c vs uncommitted working tree — 4 modified files (+118/−2) plus 3 new files
(`src/sadana/subcommands/setup.py`, `tests/unit/test_subcommands_setup.py`,
`scripts/prove_setup_fresh_instance.py`).
Reviewer context: cold review delegated to a subagent given only the diff and the three
artifacts; the compliance pass was run in the build session (that limitation is noted —
the Findings review itself was fresh).
Second opinion: none — ran during build (self-check), not repeated here by design.

## Evidence

```
$ make verify
no active task — nothing to check
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
Success: no issues found in 25 source files
TYPES OK
▶ hermetic run: TZ=UTC LANG=C.UTF-8 PYTHONHASHSEED=0, empty env
........................................................................ [ 16%]
........................................................................ [ 33%]
........................................................................ [ 50%]
........................................................................ [ 67%]
........................................................................ [ 84%]
....................................................................     [100%]
428 passed in 4.24s
TESTS OK
VERIFY OK
```

Evidence of the proof script's run (Test-stage deliverable) was captured at build time: all
seven scenarios of `scripts/prove_setup_fresh_instance.py` passed, including the real
`model_access.send()` → `NeedsCredentialOrProviderChange` before setup, a completed
`sadana chat` turn after, and the gateway refusing without / serving with the stored
secret. The `make verify` above is the fresh run this stage re-ran.

## Findings

The review found two security defects in the value-serialization half of the
`.env` format, one compliance naming gap, and five nits; every spec acceptance
criterion and plan Proof item is otherwise discharged. Details below.

### Important

- [Security] **Value serialization drops the reference's sanitization, so a newline (or crafted
  quote balance) in a stored value splits one logical value across two `.env` lines** —
  `_quote_env_value` (`src/sadana/subcommands/setup.py:94-96`) writes `f'"{value}"'` with no
  sanitization or escaping. A value like `abc"\nexport OPENROUTER_API_KEY="PWNED` lands as two
  well-formed lines; `load_dotenv()` then imports the second one, and any `source`/systemd
  `EnvironmentFile` consumer executes it — the injection is not gated by an odd input
  generator. The reference this adapts guards exactly this: hermes `_quote_env_value`
  (`hermes_cli/config.py:4494-4512`) escapes `"`/`\` and quotes conditionally, and
  `save_env_value` (`config.py:4563`) strips `\n`/`\r` before writing. Both were dropped in
  the port. `spec.md:183-184` published the unsafe form, so code matches the spec — but both
  diverge from the audited reference. **Fix:** strip `\n`/`\r` before storing (hermes's line);
  optionally escape embedded quotes for shell-source consumers. — FIXED: `_quote_env_value`
  now strips `\n`/`\r` before wrapping, with a regression test
  (`test_quote_env_value_strips_newlines`).
- [Security] **`.env` is world-readable during the write window.** `_upsert_key` creates it
  with `Path.write_text` (`setup.py:77, 91`) — 0644 under umask 022, 0666 under 000 — and
  `os.chmod(path, 0o600)` (`setup.py:152`) runs only after both secrets are already on disk.
  `spec.md:430` promises "the file gets 0600 via `os.open` + mode in the write path"; the code
  does not do that. The proof script checks 0600 only after the subprocess exits
  (`prove_setup_fresh_instance.py:143`), so the window is invisible to it. **Fix:** open with
  mode `0o600` (or the reference's `mkstemp` + atomic replace pattern). — FIXED: writes now go
  through a mode-0600 `os.open` (`_path_put`), with a regression test
  (`test_cmd_setup_creates_dotenv_0600_at_creation`).
- [Compliance] **The proof's and spec's seam name is wrong — the artifact names a seam the
  codebase never shipped.** `spec.md` §Evidence item 5 (and the plan's "no-network seam")
  name `model_access._post`; no `_post` exists anywhere in `src/`. The proof stubs
  `model_access.send` (`scripts/prove_setup_fresh_instance.py:239-251`), which is the real
  seam. The intent is satisfied in substance (a no-network stub before setup, a completed
  round trip after); this is a documentation-level gap — the spec/plan wording should name
  `model_access.send` so a later reader can reproduce the proof from the artifact. — FIXED:
  `spec.md` §Evidence item 5 now names `model_access.send`.

### Nits

- `if flag_value:` (`setup.py:112`) treated an explicitly empty flag (`--openrouter-key ""`) as
  "not given," masking a live env value; made exact with `is not None` — an explicit empty now
  blanks the value. FIXED (`test_cmd_setup_empty_flag_removes_key`).
- "Other lines preserved byte-for-byte" was not absolute: `_upsert_key` normalized CRLF→LF
  and always ended the file with `\n` (`setup.py:91`), so byte-identity held for line content,
  not file bytes, on a CRLF input file. NOT FIXED — byte-identity holds for line content,
  which is what the acceptance criterion asserts; full CRLF preservation is a follow-up.
- `cmd_setup` prints "sadana setup complete. The instance is configured to run."
  (`setup.py:153`) even on a run that wrote nothing; slightly misleading on an already-
  configured instance. NOT FIXED — cosmetic; a "configured to run" summary is accurate.
- A hand-edited spaced line (`KEY = value`) was parsed by `load_dotenv` (`config.py:50` strips
  key whitespace) but not matched by `_upsert_key`'s `startswith(f"{key}=")` (`setup.py:83`),
  so setup appended a duplicate; if the spaced line was later removed the stale duplicate
  resurrected — the same bug family hermes's `_env_line_defines_key` was written to prevent.
  FIXED — `_env_line_key` matches the normalized key on every line
  (`test_upsert_replaces_spaced_key_line`).
- No test covered the value-with-newline / value-with-quote case on either the writer or the
  reader. FIXED — `test_quote_env_value_strips_newlines` covers the writer.

### Compliance pass

- **Acceptance criteria** — all seven satisfied in substance, each by a named test or a named
  line of the proof script:
  1. *Fresh, scripted* — `test_cmd_setup_scripted_writes_both_keys`,
     `test_cmd_setup_filled_env_is_ready_for_load_dotenv`,
     `test_cmd_setup_leaves_alone_values_already_in_env`,
     `prove_setup_fresh_instance.py:135-148` (exit 0; `.env` has exactly both keys), plus
     `scenario_fresh_process_setup_is_idempotent` (second run, no flags, exit 0).
  2. *Fresh, interactive* — `test_resolve_values_prompts_on_tty_and_raises_on_interrupt`;
     empty reply / Ctrl-C path — `test_cmd_setup_interrupt_prints_cancelled_and_returns_one`.
  3. *Partly set up* — `test_cmd_setup_leaves_alone_values_already_in_env` (env value wins,
     prompts only for the missing one) + `test_cmd_setup_upsert_replaces_only_owned_key`
     (owned line replaced, others untouched).
  4. *No TTY, nothing supplied* — `test_cmd_setup_no_tty_nothing_supplied_reports_missing`
     (exit 1, both names in stderr, nothing written).
  5. *Never printed back* — `test_cmd_setup_never_echoes_a_secret` + the flag-supplied
     subprocess checks in `prove_setup_fresh_instance.py:144-147`.
  6. *Idempotence/leave-alone* — `test_cmd_setup_upsert_replaces_only_owned_key` (asserts
     `OTHER_SECRET` survives; byte-identity of line content holds — see nit 2 for the CRLF
     edge).
  7. *Real round trip* — the proof script's scenarios 5a/5b (real `send()` missing-credential
     → real `chat` turn after setup) and 6a/6b (gateway refuses / starts with stored secret).
- **plan.md § Proof** — each bullet discharged by the tests/evidence named above; the final
  bullet ("pasted output → review.md Evidence") is now discharged by this document's
  `## Evidence` plus the build-time proof capture.
- **spec.md § Rejected alternatives** — no drift. `_VALUE_KINDS` stays a fixed two-entry
  tuple (openrouter + webhook); no config.json store, no keyring/vault, no `--force`/`-y`,
  no 39-provider picker, no write to `/etc/sadana/gateway.env`. The one drift found is
  internal to the *value encoding* (Important 1), not a rejected design.
- **plan.md § Files that change** — matches the diff exactly: no file touched that the plan
  didn't name; no named file untouched.

## Decision

Approved by Adam, 2026-09-08. The two security findings and the seam-name
compliance gap were accepted as Important and are being fixed in this
branch before merge (deploy-skill §9: "If changes are requested, fix them
in this branch"). Of the five nits recorded above, three were applied as part of the
same fix (the empty flag-value, the spaced-line matching, and the
newline-value test); the CRLF byte-identity and the "setup complete"
message nits were deliberately not fixed, for the reasons recorded in
their entries.
