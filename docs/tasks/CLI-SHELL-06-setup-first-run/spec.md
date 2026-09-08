# Spec: one command turns a fresh instance into a working one

Intent: docs/tasks/CLI-SHELL-06-setup-first-run/intent.md

## Requirements

1. `sadana setup` runs on an instance that already exists and, without a
   person at the keyboard, can drive a fresh instance to ready: after it
   exits, a live exchange can happen and a message can be received. (Intent
   §Proposed outcome.) "Ready" concretely means two things, on a box
   configured to use the default provider:
   - `sadana chat` can actually talk to a model (`OPENROUTER_API_KEY` set),
   - the gateway daemon can start (`SADANA_GATEWAY_WEBHOOK_SECRET` set).
2. Running `sadana setup` on an instance that is partly set up fills only
   what is missing and leaves everything already in place alone. It never
   re-prompts for a value that is already set. (Intent §Proposed outcome:
   "fills only what is missing".)
3. The command takes the values as input when no one is at a keyboard:
   every secret is settable from command-line flags
   (`--openrouter-key`, `--webhook-secret`). Interactive prompting happens
   only for values *not* already set and *not* given by a flag; a run with
   no TTY and every value missing reports what is missing and fails
   non-zero rather than hanging on a prompt. (Intent §Constraints: "must
   work equally well with a person at the keyboard and with no one there".)
4. Secrets are stored, never printed back or shown. Setup never echoes a
   secret it collects, and never prints one it already has. (Intent
   §Constraints: "Secrets are stored, never printed back or shown".)
5. Setup stores values through the instance's existing configuration path
   — the environment — so that whatever reads them today
   (`model_access.send()` for `OPENROUTER_API_KEY`, the gateway daemon
   and systemd `EnvironmentFile` for `SADANA_GATEWAY_WEBHOOK_SECRET`) keeps
   reading from exactly where it already does. Setup does not invent a
   second configuration store. (Intent §Constraints: "the values setup
   stores go through the one configuration path the rest of the system
   already reads".)
6. `sadana setup` is idempotent: running it repeatedly on a fully
   configured instance changes nothing and reports nothing missing.
   (Intent §Proposed outcome: "the same command can be run again".)
7. Setup does not create a box, register it with any service, manage
   accounts, or touch a person's identity. Nothing in it provisions a
   machine. (Intent §Affected users and systems, §Constraints.)
8. Whatever already refuses to work when a secret is missing keeps refusing
   exactly as it does today. Setup adds no second completeness check of its
   own; it only fills the gap the existing system already names. (Intent
   §Constraints.)

## Design

**The one decision the intent left open, and how this spec settles it.**

Intent's §Open questions: "Where the stored credentials land, and how the
running instance reads them back... is decided at design time."

**Settled: a `.env` file the command writes, and the wrapper's existing
`load_dotenv`-free environment.** The instance already reads its settings
from its own environment (`config.env()` is `os.environ.get`); the question
is only how a value the operator types at `sadana setup` gets into that
environment for the next run. The answer is a `.env` file companion —

- **Written**: a whitespace-dotenv file that both hermes and this item's own
  `sadana gateway install` path already assume exists as the placeholder
  with no loader glued in. `~/.local/state/sadana/.env` (i.e.
  `state_dir / ".env"`), matching hermes's own
  `~/.hermes/.env` (state, not config), and matching this repo's `.gitignore`
  (`.env` is already ignored — a real, exploitation-relevant signal that an
  env-file is expected to hold keys here).
- **Read**: only by a small `load_dotenv()` added to `config.py` — called
  exactly once, at the top of `src/sadana/cli.py:main()`, before any
  subcommand parses config. It parses nothing itself: it copies each `KEY`
  present in the file into `os.environ` *when the live environment does not
  already set it*. The live environment stays authoritative (a real env var
  always wins over the file line), which is exactly the precedence the
  `config.env()` primitives already imply, and it is what makes
  "the same command can be run again and only fills what is missing" true
  for a person who has since exported a key in their shell.

This is the minimal gap-fill that satisfies requirement 5 with zero
mechanism beyond what exists: no new config store, no keyring, no encrypted
vault, no new read path — `send()` reads `os.environ["OPENROUTER_API_KEY"]`
today and keeps doing so; the gateway daemon, which runs under systemd with
`EnvironmentFile=-/etc/sadana/gateway.env`, keeps reading *its* file. For
the interactive CLI, the .env is loaded before the subcommand runs; for the
daemon, the operator is expected to have the secrets in the systemd env file
(channel setup is that file's job, see Non-goals), and the CLI-side .env is
the same `.env` a systemd `EnvironmentFile` can point at.

**Why `.env` and not writing to the systemd env file directly.** The two
consumers live in two different environments: the CLI reads the process
environment (which a `.env` loader populates), the systemd service reads
`/etc/sadana/gateway.env`. `sadana setup` runs as the *user*, and writing to
`/etc/` requires root — exactly the boundary `sadana gateway install`
already honors by requiring `sudo`. So the interactive half writes the
state-dir `.env` (user-writable, no root), and the daemon secret, which the
systemd `EnvironmentFile` genuinely needs, is additionally written to that
same state-dir `.env` so that a future `sadana gateway install` — whose
`gateway_service.install()` already prints "before starting:
/etc/sadana/gateway.env must set SADANA_GATEWAY_WEBHOOK_SECRET" — can round
it into `/etc`. Writing the daemon secret into the user's own `.env` means
the interactive path and the daemon path share one source the operator can
see, while the daemon's own file remains the daemon's.

This leaves intact the current explicit failure when the daemon has no
secret (`gateway_daemon.run()` refusing to start with
"SADANA_GATEWAY_WEBHOOK_SECRET is not set") — requirement 8.

**Module shape.**

No new module under `src/sadana/` is warranted for the pure logic; this item
is a CLI subcommand plus the smallest possible loader. The code lands in two
places that already exist:

- **`src/sadana/subcommands/setup.py`** — new file, owning its parser and
  its handler the way `chat.py`/`gateway.py` already do ("Owns both its
  parser and its handlers in one file, matching chat.py's and
  conversations.py's existing convention"). A module that touches real I/O
  (writes the `.env`) is its own file — per CLAUDE.md's rule, this file is
  the I/O module. It prompts with a masked `getpass`; in a non-TTY runs it
  never prompts (requirement 3).
- **`src/sadana/config.py`** — a `load_dotenv()` function added to the file
  that already owns "the one way to ask sadana-harness for a configuration
  value." It returns nothing (`None`), it is called once by `cli.py:main()`
  before the subcommand dispatches, and it is deliberately *not* called by
  `get_paths()` or any `env()` primitive — loading is a CLI entrypoint
  concern, not a config-resolution concern (a library caller that wants the
  file loaded calls it itself; a library caller that doesn't is unaffected).
- **`src/sadana/cli.py`** — `main()` calls `config.load_dotenv()` first,
  before `build_parser().parse_args()`.
- **`src/sadana/subcommands/gateway.py`** — no change needed. It already
  reads `config.env("SADANA_GATEWAY_WEBHOOK_SECRET", "")` at `cmd_gateway_run`
  time, which — with the file-loaded `.env` in the process env — is exactly
  the value `sadana setup` stored.

**What setup fills, and how it decides what is missing.**

```python
_VALUE_KINDS = (
    # (env var, flag name, human prompt, is_secret)
    ("OPENROUTER_API_KEY", "openrouter-key", "OpenRouter API key", True),
    ("SADANA_GATEWAY_WEBHOOK_SECRET", "webhook-secret",
     "Webhook secret (what the gateway signs inbound messages with)", True),
)
```

The provider surface is deliberately fixed to the **one wired provider**
(openrouter) plus the daemon secret the intent names directly. It does not
enumerate all 39 registered providers — those are identity stubs with no
`request_fn` (MODEL-ACCESS's registry), and a setup that promises to
configure a provider it cannot make work would be a check that never
completes.

For each entry, in order:

1. If the flag was given on the command line, use it.
2. Else if the value is already present in the live environment
   (`os.environ.get(name)`), do nothing — "leaves everything already in
   place alone."
3. Else if stdin is a TTY, prompt masked (`getpass.getpass`), and treat an
   empty reply as "missed it, report it missing."
4. Else record it as missing.

Then:

- If every value was resolved, write them all to
  `config.get_paths().state_dir / ".env"` with the upsert discipline below
  and print a short confirmation; return `0`.
- If any value is still missing, print exactly which env var(s) are missing
  and how to provide them (`sadana setup --openrouter-key ... --webhook-secret ...`
  or set them in the shell), write nothing, return `1`. This is the
  "reports what is missing and fails non-zero" half of requirement 3 — also
  the correct behavior for a scripted provisioning workflow with no keyboard
  (it can see exactly what a fresh box needs).

A fully-configured instance (`sadana setup`, TTY or not, no flags) resolves
everything in step 2, writes nothing, and returns `0` — requirement 6.

**.env write discipline (the one real I/O).**

Upsert, never append blindly — adapted from
`hermes_cli/config.py:4544 save_env_value`/`_quote_env_value`, trimmed to
the single-key upsert this item needs:

```python
def _quote_env_value(value: str) -> str:
    return f'"{value}"'  # single-quoted values are ambiguous to systemd-like parsers
```

```python
def _upsert_key(path: Path, key: str, value: str) -> None:
    if not path.exists():
        path.write_text(f"{key}={_quote_env_value(value)}\n", encoding="utf-8")
        return
    lines = path.read_text(encoding="utf-8").splitlines()
    out: list[str] = []
    found = False
    for line in lines:
        if line.startswith(f"{key}="):
            out.append(f"{key}={_quote_env_value(value)}")
            found = True
        else:
            out.append(line)
    if not found:
        out.append(f"{key}={_quote_env_value(value)}")
    path.write_text("\n".join(out) + "\n", encoding="utf-8")
```

The `line.startswith(f"{key}=")` guard means an existing line for the same
key is replaced (idempotent, requirement 6), and other keys' lines are
preserved byte-for-byte (requirement 2 — leaves what is already configured
alone). Only the keys this command owns are ever touched. Empty-string
values are not written (an empty key is a missing key).

`load_dotenv()` reads the same file with the same line shape
(`line.partition("=")`, strip surrounding quotes on the value), so writer
and reader are two halves of one format.

**Interactive vs. scripted, and what "no one at a keyboard" means.**

- **`sys.stdin.isatty()`** is the switch: prompting only when both stdin and
  stdout are TTYs (hermes's `_stream_is_tty` premise). A provisioning
  workflow runs `sadana setup --openrouter-key ... --webhook-secret ...`
  with no stdin and never blocks; or it runs `sadana setup` with nothing
  supplied, gets a missing-keys report and exit `1`, and knows what to pass
  next time.
- **Masked prompting** via `getpass.getpass(prompt)`, which also suppresses
  echo. This is requirement 4's "never printed back" for the interactive
  path. TTY detection + `getpass` together are the standard-library answer
  (wait, stdlib: no dependency to add).

**What "the same command can be run again" means for flags.**

Every flag is a *free-form override*, never a *persisted choice*: setup does
not remember the openrouter key it prompted once and skip prompting for it
next run. If the live environment has the value, step 2 fills it without
asking — that is the persistence. A caller who wants forced re-entry passes
the flag. This keeps the command stateless between runs (its only state is
the file), which is what makes requirement 2 trivially true.

## Interface

**`sadana setup`.**

```
usage: sadana setup [-h] [--openrouter-key KEY] [--webhook-secret SECRET]

One guided command turns a fresh instance into a working one. Fills only
what is missing: the OpenRouter key a live exchange needs, and the daemon
secret the gateway needs before it will start. Values already present are
left alone; values not supplied are masked-prompted for on a terminal and
reported missing otherwise.

options:
  -h, --help              show this help message and exit
  --openrouter-key KEY    OpenRouter API key to store (prompted for, masked,
                          if not given and not already set)
  --webhook-secret SECRET gateway webhook secret to store (prompted for,
                          masked, if not given and not already set)
```

**Exit codes**: `0` all values resolved (whether written or already present);
`1` one or more values still missing (nothing written).

**`src/sadana/config.py` addition.**

```python
def load_dotenv() -> None:
    """Copy KEY=value lines from ``state_dir/.env`` into the environment.

    Loaded exactly once, at the CLI entrypoint, before any subcommand reads
    config. Never overrides a variable already set in the live environment.
    """
```

Called from `cli.py:main()` as its first statement. No return value, no
exceptions on a missing file (the common case for a not-yet-set-up instance),
and a malformed line is skipped, never fatal.

**Errors.**

- Missing values with no TTY: human-readable list to stderr, exit `1`.
- Unwritable state dir: the filesystem error propagates (bad setup should be
  loud, like the config primitives' own `ValueError`s), matching the
  gateway-service behavior of surfacing a real `CalledProcessError` instead
  of swallowing it.
- A `KeyboardInterrupt`/`EOFError` during a prompt: abort cleanly, exit `1`,
  write nothing, print "setup cancelled".

## Acceptance criteria

- [ ] **Fresh instance, scripted.** On a clean env (no `OPENROUTER_API_KEY`,
      no `SADANA_GATEWAY_WEBHOOK_SECRET`): `sadana setup --openrouter-key K
      --webhook-secret S` with stdin not a TTY exits `0`;
      `~/.local/state/sadana/.env` exists and contains exactly
      `OPENROUTER_API_KEY="K"` and `SADANA_GATEWAY_WEBHOOK_SECRET="S"`; a
      subsequent `sadana chat --help` or `sadana gateway run` in that
      environment sees both keys set; and a second `sadana setup` (no flags)
      exits `0`, writes nothing, and reports nothing missing.
- [ ] **Fresh instance, interactive.** Same clean env, with a TTY: each
      missing value is prompted for masked; entering both and accepting
      completes and writes the file. Entering an empty reply at a prompt
      reports that var missing and exits `1` without writing anything.
- [ ] **Partly set up.** Clean env except `OPENROUTER_API_KEY` already
      exported in the shell: `sadana setup` prompts only for the webhook
      secret, never for the key; both end up set; the exported key's line
      in the file is not rewritten.
- [ ] **No TTY, nothing supplied, nothing set.** `sadana setup` exits `1`,
      prints both missing var names, writes no file.
- [ ] **Never printed back.** With a TTY, the key typed at the prompt does
      not appear in the captured stdout/stderr, and `sadana setup
      --openrouter-key K` does not print `K` either.
- [ ] **Idempotence/`leave-alone`.** An existing `.env` with
      `OTHER_SECRET="x"` plus a stale `OPENROUTER_API_KEY="old"` line:
      re-running setup with a new key replaces only the key line;
      `OTHER_SECRET="x"` is byte-identical before and after.
- [ ] **Real round trip** (standalone proof script, not a unit test — see
      `## Evidence` below): after setup writes the .env, a real `sadana
      chat` against OpenRouter can complete one exchange, and
      `gateway_daemon.run()` starts (secret set) and refuses to start when
      the secret is unset, with no separate check added by this item.

## Non-goals

- **Provisioning a machine, registering it, or managing accounts.** The
  blueprint's item 6 said "not scheduled until a provisioning workflow exists
  to call it" (CLI-SHELL-05's memory, `cli_shell_blueprint.md §6`). This item
  deliberately reverses that scheduling decision — the maintainer chose to
  build `sadana setup` now (intent.md §Changed during planning) — but the
  *deployment* remains out of scope: setup configures a box that already
  exists.
- **Configuring `/etc/sadana/gateway.env` for the systemd service.** Setup
  writes the *same* webhook secret to the user-writable state `.env`, but
  it does not require root and does not touch the systemd file. Getting the
  secret provisioned for the *service* is the territory of the existing
  `sadana gateway install` flow (which already prints its env-file
  requirement), not a new mechanism.
- **A second completeness check.** Setup does not probe the model, open a
  socket, or check "everything works." It fills what the existing diagnostics
  already name missing; `model_access.send()` and `gateway_daemon.run()` keep
  being the check.
- **A provider picker or model browser.** The intent names "the provider
  and its key"; hermes's `select_provider_and_model` is a materially bigger
  surface (a 39-provider fleet with stubs), and this item does not enumerate
  providers. One wired provider (openrouter) is the honest scope.

## Rejected alternatives

1. **Store into config.json / a new secrets store.** Adds a second
   configuration path the system does not read — directly violating the
   intent's own constraint that setup must go through the path the rest of
   the system already reads. A `.env` is that path already (`config.env()`
   is `os.environ.get`).
2. **Keyring / encrypted vault.** `keyring`-style storage is a dependency,
   a service, and a per-OS surface that has no consumer yet — nothing in the
   system will look it up. It also fails the scripted-provisioning case
   (a fresh EC2 box has no logged-in keyring). Declined (guideline 2: reduce
   the number of bets — .env is a file, not a subsystem).
3. **Have `sadana gateway install` also write the daemon secret into
   `/etc/sadana/gateway.env`.** Tempting (one command, both consumers), but
   it would make setup require root, breaking requirement 3's no-TTY path
   (a provisioning workflow is not root). The user-facing key belongs in the
   user's own state; the daemon file stays the daemon's surface, and the two
   are linked by the operator, not by this command.
4. **A `--prompt`-style / `-y` flag to force prompting.** Stateless setup
   with "flags for values, environment for persistence" covers both modes
   with less surface. Declined.
5. **Enumerate all 39 registered providers in the picker.** They are
   identity stubs with no `request_fn`; offering them would let setup
   "succeed" at configuring a provider that cannot complete an exchange —
   the exact false closure the intent's "cannot get from installed to
   actually works" describes. The one wired provider is the honest scope.
6. **Encrypt the `.env` at rest.** `.env` already sits untracked per
   `.gitignore`; hermes itself stores API keys in plaintext
   `~/.hermes/.env`; the daemon's own secret already lives in plaintext at
   `/etc/sadana/gateway.env`. A file the same process reads through
   `os.environ` cannot be "protected" from that process. Declined as
   theatre; the real guard is the file's 0600 perms, which setup sets.

## Concerns

- **The biggest tension is requirement 5 vs. the daemon's own
  `EnvironmentFile`.** The interactive half writes the state `.env`; the
  systemd service reads `/etc/sadana/gateway.env`. A fresh EC2 box where the
  workflow ran `sadana setup` will have the secret in the user's `.env` but
  the *service* will still refuse to start until an operator (or a future
  item) propagates it to `/etc`. The intent's "the secret the background
  process needs" (§Proposed outcome) is satisfied for the interactive path
  but only half-satisfied for the systemd path. This spec deliberately does
  not close that gap: closing it either requires root (rejected, alt 3) or
  a second write path, and the existing install flow already names its own
  requirement. A reviewer should decide whether this counts as leaving the
  daemon consonant with the intent, or as a half-provision. Confirmed as
  accepted scope in design.
- **`.env` precedence.** Live env wins over the file in `load_dotenv()`,
  so a shell-exported key overrides a stored one. This is the correct
  precedence (the file is the fallback, matching `env(default)`), but it
  means `sadana setup` will report a value "already set" (step 2) that the
  daemon, under systemd with no such export, would still not have — the
  interactive report and the daemon's view can differ slightly. Acceptable
  because `sadana gateway run` from the operator's own shell uses exactly
  that environment.
- **Requirement 8 vs. a better error.** Setup could probe
  (`curl` a model) to give a stronger "ready" signal. The intent explicitly
  forbids a second check; probing would also cost real spend on every run.
  Kept out.
- **`getpass` and TTY.** `getpass.getpass` writes its prompt to stderr and
  suppresses echo, but on a genuine TTY the terminal may still accept
  control characters; standard `getpass` has no perfect Windows story.
  This box is WSL/Linux; the standard-library fallback (inner call to a
  plain `input`) is acceptable.
- **Test isolation.** The unit tests set `SADANA_STATE_DIR` to a tmp path
  (autouse conftest fixture), so the `.env` lands in tmp and never touches
  the real state dir — matching "Tests must never write to the real state
  directory".
- **`load_dotenv()` in `config.py` is a NEW behavior for every existing
  subcommand.** Because it runs at `main()` before subcommand dispatch, a
  user with a `.env` in their state dir will, from this item on, have those
  values visible to `chat`, `conversations`, `gateway`, etc. That is the
  intent (setup's whole point is those values are read back), and the
  win-over-live-env rule keeps it from hijacking a shell that set something
  deliberately. Still, a reviewer should check the precedence and the
  once-only placement in `cli.py` — the single load site is the one
  load-bearing bookkeeping of this design.
- **The provider surface is fixed to openrouter.** If a second provider
  becomes wired later, `_VALUE_KINDS` grows; the list is deliberately a
  tuple of plain entries, not a registry (no second provider exists yet —
  CLAUDE.md's registry-of-one rule). The moment a second provider is wired,
  "which providers to offer" becomes a real product question and the fixed
  list should be revisited.
- **Security checks.** Plaintext secrets in a dotenv file match both the
  reference (hermes `~/.hermes/.env`) and the existing daemon
  (`/etc/sadana/gateway.env`); the file gets 0600 via `os.open` + mode in
  the write path. Any reviewer should confirm no setup codepath echoes a
  secret (tested by acceptance criterion 5).

## Evidence

Follows the block's established prove-script pattern. Because requirement 7
("a real exchange can happen") is a first real external round trip, and
testing-conventions bars network from the unit suite, this item's Test stage
ships **one standalone proof script** (`scripts/prove_setup_fresh_instance.py`)
that:

1. Uses a temp `SADANA_STATE_DIR`;
2. invokes `sadana setup --openrouter-key <provided> --webhook-secret <provided>`
   with stdin non-TTY;
3. asserts the `.env` exists with exactly those two keys;
4. loads it with `config.load_dotenv()` and asserts a real `config.env()`
   read sees both (mirroring what a fresh `main()` does);
5. monkeypatches `model_access.send` (the existing no-network seam) to a
   canned success and runs one `sadana chat` turn against the stored
   provider/key, asserting a real completion; and
6. verifies `gateway_daemon.run()` starts with the stored secret and refuses
   to start when it is unset — the exact same "first real daemon round
   trip" `prove_gateway_webhook_e2e.py` already established for the gateway
   path.

Its stdout, pasted into `review.md`'s `## Evidence`, is the Test stage's
whole deliverable. `make verify` before reporting complete.
