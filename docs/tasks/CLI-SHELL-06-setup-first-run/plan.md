# Plan: setup one command turns a fresh instance into a working one (from intent.md 2026-09-08)

## Files that change

- `src/sadana/config.py` — add `load_dotenv()` (reads `state_dir/.env`, never overrides live env).
- `src/sadana/cli.py` — call `config.load_dotenv()` first in `main()`; wire `build_setup_parser`.
- `src/sadana/subcommands/setup.py` (new) — `sadana setup` parser + handler, owns its `.env` I/O.
- `tests/unit/test_config.py` — `load_dotenv()` cases.
- `tests/unit/test_subcommands_setup.py` (new) — subcommand cases.
- `tests/unit/test_cli.py` — `setup` parser wiring + load_dotenv-on-main.
- `scripts/prove_setup_fresh_instance.py` (new) — real-round-trip evidence script.

## Order of work

1. `config.load_dotenv()` in `src/sadana/config.py` + tests in `test_config.py` (pure, no callers yet).
2. Wire into `cli.py:main()` + `test_cli.py` asserts it runs before dispatch and never overrides live env.
3. `src/sadana/subcommands/setup.py`: `_VALUE_KINDS` table, masked `getpass` prompt, `.env` upsert writer, missing-values report, exit codes. Tests in `test_subcommands_setup.py`.
4. Wire `build_setup_parser` into `cli.py` + `test_cli.py` parser-wiring assertion.
5. `scripts/prove_setup_fresh_instance.py` — run it by hand, capture output.
6. Self-check: `/ponytail-review` + `/simplify` on the whole diff; triage; apply worth-taking-now.
7. `make verify`; report both self-check and verify output.

## Risks

- `load_dotenv()` runs for every subcommand once in `main()` — a `.env` becomes visible to `chat`/`conversations`/`gateway`. Mitigation: never overrides a live var; conftest redirects `SADANA_STATE_DIR` to tmp.
- Step 3 is the risky one: TTY detection + masked prompt + "write nothing on failure" must be exactly right or `sadana setup` hangs in a provisioning workflow. Landed after the proven format contract (step 1); tests cover both TTY states.
- Guard against drifting to spec's Rejected alternatives: no keyring/vault, no config.json store, no 39-provider picker, no `--force`/`-y` flag — fixed `_VALUE_KINDS` tuple, flags for values, env for persistence.

## Proof

- `test_config.py`: loads `.env`, doesn't override live var, missing file tolerated, malformed lines skipped, strips quotes.
- `test_subcommands_setup.py`: scripted exit 0 writes exactly the two keys; no-TTY-missing exits 1 writes nothing; already-in-env leaves alone; upsert replaces only the key line; never echoes a secret; masked-prompt branch via monkeypatched `getpass`.
- `test_cli.py`: `setup` in `--help`; `main(["setup", ...])` runs handler; load_dotenv runs before dispatch.
- `make verify` green (chain/lint/typecheck/test sentinel-OK).
- `scripts/prove_setup_fresh_instance.py` pasted output → `review.md` `## Evidence` at Deploy.
