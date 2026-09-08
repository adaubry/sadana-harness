# Spec: a real instance's background process behaves like a normal service

Intent: docs/tasks/CLI-SHELL-05-gateway-lifecycle-verb/intent.md

## Requirements

1. `sadana gateway install` writes a working systemd unit for the gateway
   daemon and enables it, without the operator hand-writing any service
   configuration. (Intent §Proposed outcome.)
2. `sadana gateway start` / `stop` / `restart` / `status` control and report
   on that installed service the way any other systemd service is
   controlled. (Intent §Proposed outcome, "smallest version still worth
   having.")
3. Once installed, the service restarts on crash and survives a reboot with
   no further action from the operator. (Intent §Proposed outcome.)
4. Nothing here builds a second way to keep a process alive, or a second
   single-instance guard — service supervision goes through the real
   machine's own service manager, and the daemon's own existing
   `flock` (`GATEWAY-DAEMON-01`) is the only thing that answers "is another
   instance already running." (Intent §Constraints.)
5. This item does not configure `SADANA_GATEWAY_WEBHOOK_SECRET` or any other
   value the daemon needs to run — those are assumed already set by the
   time `install` runs. (Intent §Constraints.)
6. This item does not provision a new machine, register one with anything,
   or decide how a fresh instance gets its first setup. (Intent §Constraints
   and §Affected users and systems.)
7. This item's own real behavior — actually installing, starting, stopping
   a real systemd service — cannot be proven by the automated test suite,
   since the environment sadana-harness is developed in has no real service
   manager to install into. (Intent §Constraints.)

## Design

One research pass covered hermes's own `hermes gateway install/start/stop/
restart/status` (`hermes_cli/gateway.py:2817-4905`, its systemd half) and its
CLI wiring (`hermes_cli/subcommands/gateway.py`). What follows names what
was adopted, trimmed, or declined, and why — hermes's version solves a
materially harder problem (many profiles per machine, both user- and
system-scope installs side by side, launchd and Windows targets, a
messaging-relay-enrollment flow) that this item does not have, so most of
the "why" below is about which of hermes's own complexity has no counterpart
here, not about disagreeing with hermes's design.

**Adopted, trimmed to what sadana actually needs:**

- **The five-verb surface itself** (`install`/`start`/`stop`/`restart`/
  `status`) and the shape of each handler — `install` writes a unit and
  enables it; `start`/`stop`/`restart` are thin `systemctl` wrappers gated
  on the unit already existing; `status` prints `systemctl status` plus one
  friendly summary line. Confirms intent.md's own five-verb scope is exactly
  hermes's own precedent, not a guess.
- **The unit-file shape** (`generate_systemd_unit()`,
  `hermes_cli/gateway.py:3995-4130`) — trimmed drastically. Hermes's own
  template branches on user-vs-system scope, remaps every path for a target
  user distinct from the installing one, derives a per-profile
  `HERMES_HOME`, assembles a PATH from managed Node dirs / WSL interop /
  cargo/go/npm user-local bins, and sets watchdog directives tied to a
  periodic heartbeat loop. Sadana has exactly one scope (system), one
  install (no profiles, no target-user remap since `install` runs as the
  same user it configures), one fixed source checkout, no managed Node/
  cargo/go/npm toolchain to expose to a Python-only daemon, and no
  systemd-watchdog heartbeat loop (`gateway_daemon.py`'s `_notify_systemd()`
  sends `READY=1` once at startup and nothing periodic after — so the
  generated unit deliberately never sets `WatchdogSec`; setting it without a
  heartbeat would make systemd kill a perfectly healthy daemon). What
  survives, in full: `Type=notify` (the daemon already calls `sd_notify`),
  `Restart=on-failure` (not hermes's `Restart=always` — see Rejected
  alternatives), `RestartSec=5`, `KillMode=mixed`, `KillSignal=SIGTERM`
  (matches `gateway_daemon.py`'s own graceful-stop signal exactly),
  `StandardOutput=journal`/`StandardError=journal`, `WantedBy=multi-user.target`.
- **Declined, not adopted: `get_python_path()`'s venv-detection shape**
  (`hermes_cli/gateway.py:3700-3716`). A first build of this item's own
  `_python_path()` did adopt its `sys.prefix != sys.base_prefix` probe,
  trimmed — and a real e2e run caught exactly why that doesn't fit here:
  `sudo sadana gateway install` invoked a bare `python3` with `sys.prefix
  == sys.base_prefix`, so the probe fell through to `sys.executable` (the
  system interpreter, no `tomllib`) instead of this project's own `.venv`
  Python. Hermes's probe exists because hermes genuinely supports several
  possibly-activated environments and has to ask "which venv is the
  *invoking* interpreter in" — CLAUDE.md states, unconditionally, that
  sadana's Python lives in `.venv` at one fixed, known location, never
  activated, never one of several. `_python_path()` resolves that path
  directly (`<repo>/.venv/bin/python3`), falling back to `sys.executable`
  only for a checkout `make` hasn't built yet — simpler, and correct for a
  project with exactly one valid interpreter rather than an unbounded set
  of possible ones.
- **`_require_root_for_system_service()`**
  (`hermes_cli/gateway.py:3333-3338`) — adopted near-verbatim: a system-scope
  action refuses with a clear message if `os.geteuid() != 0`, telling the
  operator to re-run with `sudo`. Five lines, no reason to reinvent it.
- **`_run_systemctl()`**'s shape (`hermes_cli/gateway.py:3086-3098`) — a
  `subprocess.run` wrapper that turns a missing `systemctl` binary into one
  clear `RuntimeError` instead of a raw `FileNotFoundError` traceback.
  Adopted directly; sadana has no user/system scope branch to thread through
  it, so it is simpler than hermes's own version.
- **The `--run-as-user` safety pattern** (`_system_service_identity()`,
  `hermes_cli/gateway.py:3341-3372`), trimmed: default the service's `User=`
  to `$SUDO_USER` (who actually ran `sudo`), refuse to silently install as
  literal `root` unless a caller explicitly passes `--run-as-user root`.
  Hermes's own version also resolves a group name via `grp`/`pwd` lookups
  for a *different* target user than the installer; sadana's install always
  configures the same user who ran it (or an explicit override), so no
  cross-user path-remapping is needed.

**Declined outright, and why:**

- **Multi-profile service naming and `HERMES_HOME` derivation**
  (`get_service_name()`, `hermes_cli/gateway.py:2817-2827`) — hermes
  suffixes the unit name per profile so several gateways can run on one
  machine. Sadana has exactly one instance per machine (intent.md's own
  "one machine" framing, and `GATEWAY-DAEMON-01`'s spec.md before it) — the
  unit name is the fixed constant `sadana-gateway.service`, not derived from
  anything.
- **User-scope install, and the whole subsystem it drags in**: scope
  selection (`_select_systemd_scope`), the `--system` flag itself,
  D-Bus-session preflight (`_preflight_user_systemd`,
  `UserSystemdUnavailableError`), and systemd-linger detection/guidance
  (`get_systemd_linger_status`, `print_systemd_linger_guidance`) — all
  exist in hermes because a user-scope service silently stops working at
  logout unless linger is separately enabled. Intent.md's own interview
  picked system-scope specifically to avoid this whole class of problem;
  since sadana never offers user-scope at all, there is no scope to select
  between and nothing to preflight.
- **Installed-unit drift detection and auto-repair**
  (`systemd_unit_is_current()`, `refresh_systemd_unit_if_needed()`,
  `_sync_hermes_home_from_systemd_unit()`, `hermes_cli/gateway.py:4179+`) —
  this exists because a hermes unit can go stale when `HERMES_HOME` moves or
  a profile's own config changes independently of the installed unit file.
  Sadana's generated unit has no such independently-changing input (the
  Python path and source checkout location are the only two facts baked
  into it, and reinstalling always regenerates both fresh) — so `install`
  is unconditionally idempotent: it always regenerates and rewrites the
  unit, `daemon-reload`s, and enables it. No staleness state to detect, so
  no detector to build.
- **The SIGUSR1 graceful-restart/health-probe/wedge-escalation machinery**
  (`systemd_restart()`'s ~140 lines, `hermes_cli/gateway.py:4640-4779`,
  `probe_gateway_loop_liveness`, `_escalate_wedged_gateway`) — this exists
  because hermes's own gateway needs to hot-restart *without* dropping
  in-flight work across a running event loop, and needs to detect and force
  past a wedged one. `gateway_daemon.py` (`GATEWAY-DAEMON-01`) already
  handles `SIGTERM` gracefully — `server.shutdown()` plus Python's own
  non-daemon-thread join drains an in-flight webhook request before the
  process exits, with no separate drain protocol to invoke first. `restart`
  is therefore exactly `systemctl restart sadana-gateway.service` — stop
  (graceful `SIGTERM`, already correct) then start — with no SIGUSR1
  handler to add to `gateway_daemon.py`, no liveness probe, no wedge
  escalation. If a real wedge-in-production signal ever shows up, that is
  the trigger to revisit, matching this project's own posture toward
  `conversation_store.py`'s single connection.
- **`ExecStopPost=... gateway.cgroup_cleanup`** — hermes runs cgroup-based
  process tracking for its own sandboxed plugin execution; sadana's
  EXECUTION block does first-party, in-process HTTP only, no cgroups, no
  external process tracking to clean up.
- **Legacy-unit detection/migration** (`has_legacy_hermes_units()`,
  `_LEGACY_SERVICE_NAMES`, the `migrate-legacy` verb) — exists because
  hermes renamed its own service across versions. Sadana's service has had
  exactly one name since it has never shipped a unit before.
- **`gateway enroll` / `gateway list` / `gateway setup` / `gateway
  migrate-legacy` / `hermes proxy`** — none of these are intent.md's five
  verbs. `enroll` is a cloud-relay-enrollment concept the CLI-SHELL
  blueprint's own §4.2 already declined for sadana; `proxy` is an unrelated
  credential-sharing feature with no sadana counterpart.
- **`ExecReload=/bin/kill -USR1 $MAINPID`** — hermes wires `systemctl
  reload` to its own SIGUSR1 graceful-restart protocol, declined above.
  The generated unit defines no `ExecReload`; `systemctl reload` is simply
  not supported (systemd's own default behavior for a unit without one is
  to refuse the reload with a clear error), and `restart` is the one and
  only verb for this.

### Where the new code lives, and why

Following this project's existing split — a pure module and, separately,
whatever module touches real I/O (`gateway.py`/`gateway_daemon.py`'s own
precedent from `GATEWAY-DAEMON-01`):

- **`src/sadana/gateway_unit.py`** (new, pure: no I/O, no `subprocess`, no
  filesystem writes): `generate_unit(python_path: str, src_dir: str,
  working_dir: str) -> str` — the unit-file template as a pure string
  builder, taking every environment-derived fact as an explicit parameter
  rather than reading `sys`/`os.environ` itself (`testing-conventions`'
  "pure functions that take environment as data are fine" — this is exactly
  that shape, and it is what makes the template directly unit-testable
  without faking where the interpreter thinks it is). Also
  `SERVICE_NAME = "sadana-gateway"` and `unit_path() -> Path` (fixed
  `/etc/systemd/system/sadana-gateway.service` — pure path construction, no
  I/O).
- **`src/sadana/gateway_service.py`** (new, I/O: writes a file, runs
  `subprocess`, reads `os.geteuid()`/`sys.prefix`): `install(*,
  run_as_user: str | None) -> int`, `start() -> int`, `stop() -> int`,
  `restart() -> int`, `status() -> int` — each an `int` exit code
  (CLAUDE.md's own rule for a CLI subcommand handler). `install` resolves
  `python_path`/`src_dir` (the venv-detection logic above), calls
  `gateway_unit.generate_unit(...)`, writes it, runs `systemctl
  daemon-reload` and `systemctl enable`, and prints the `EnvironmentFile`
  path (see below) as a "next step" — it never writes to that path itself.
  `start`/`stop`/`restart`/`status` require the unit to already exist
  (matching hermes's own `_require_service_installed` shape) and otherwise
  are direct `systemctl` calls via the adopted `_run_systemctl`-equivalent
  wrapper.
- **`src/sadana/subcommands/gateway.py`** (existing, extended — not a new
  file): `GATEWAY-DAEMON-01` already owns this file for `sadana gateway
  run`. This item adds five more nested subparsers
  (`install`/`start`/`stop`/`restart`/`status`) under the same `gateway`
  parser tree and their five thin `cmd_gateway_*(args) -> int` handlers,
  each importing and calling straight into `gateway_service.py` — matching
  this file's own existing convention exactly (`cmd_gateway_run` already
  does the same import-and-call shape into `gateway_daemon.py`).

### The systemd unit itself

```ini
[Unit]
Description=sadana gateway daemon
After=network-online.target
Wants=network-online.target
StartLimitIntervalSec=0

[Service]
Type=notify
ExecStart=<python_path> -m sadana.cli gateway run
WorkingDirectory=<src_dir's parent, the checkout root>
Environment="PYTHONPATH=<src_dir>"
EnvironmentFile=-/etc/sadana/gateway.env
User=<run_as_user, defaulting to $SUDO_USER>
Restart=on-failure
RestartSec=5
KillMode=mixed
KillSignal=SIGTERM
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
```

`Environment="PYTHONPATH=<src_dir>"` exists because this project deliberately
never installs itself into its own venv (`pyproject.toml`'s `pythonpath =
["src"]` is pytest's own mechanism; CLAUDE.md's own "Things Claude gets
wrong" entry warns against "fixing" this with an editable install) — the
same mechanism every `scripts/prove_*.py` script already uses
(`sys.path.insert(0, ".../src")`) is what lets a plain `python -m
sadana.cli` reach the package without one. `src_dir` is derived once, at
install time, from wherever the running `sadana` process's own `sadana`
package actually resolved from — not hardcoded, not guessed.

`EnvironmentFile=-/etc/sadana/gateway.env` (the leading `-` means "don't
fail unit activation if this file is missing") is the one place
`SADANA_GATEWAY_WEBHOOK_SECRET` and friends reach the process — systemd's
own native mechanism for this, not a sadana-built `.env` parser (there is
none, and this item does not add one). `install` never creates or writes
this file (Requirement 5) — it only prints the path so the operator knows
where their own secret needs to live before the service will actually stay
up (the daemon itself already refuses to start with a clear message if the
secret is unset, `GATEWAY-DAEMON-01`'s own `gateway_daemon.run()`).

### The four design guidelines

1. **Learn from the reference first** — the whole §Design section above is
   this; every declined piece of hermes's own design is declined because
   sadana genuinely lacks the thing that piece exists to solve (multiple
   profiles, dual scope, cross-version drift, a wedge-prone event loop,
   cgroup tracking, a renamed service).
2. **Reduce the number of bets.** This item sits nowhere near the plugin
   seam (`plugin_dispatch.py`/`plugin_manifest.py`) — it is OS-service
   lifecycle tooling, not agent capability, so guideline 2's plugin framing
   does not apply directly. What the guideline does catch here: hermes
   offers a `--system`/user-scope choice because it genuinely supports
   both; sadana does not, so there is no flag to add and no scope-selection
   function to write — one fewer bet than the reference itself takes,
   not a matched one.
3. **Catch the scenario at the least step-cost.** The scenario — "the
   background process has no OS supervision" — is caught by *adding a
   step* (a new CLI verb family plus a unit-file template), because no
   existing step can absorb it: `sadana gateway run`
   (`GATEWAY-DAEMON-01`) deliberately stays a blocking foreground call, and
   making it heavier (self-daemonizing, writing its own unit file on every
   invocation) would conflate "run once, in the foreground" with "install
   as a supervised service" — two different callers with two different
   needs. Adding a step is the cheapest of the three moves available here;
   it touches none of `gateway_daemon.py`/`gateway_dispatch.py`/
   `channel_webhook.py`.
4. **Minimise mutable state.** State inventory: the installed unit file
   itself (`/etc/systemd/system/sadana-gateway.service` — real OS state
   that must persist across the process boundary, matching the existing
   `gateway.lock` precedent for "a real external file is the only way two
   processes agree on something"); the enabled/active bit systemd itself
   already owns (never cached or duplicated in Python — every `status`
   call asks `systemctl` fresh). Nothing else. No drift-tracking state
   (declined above), no installed-scope registry, no PID file beyond
   whatever `gateway_daemon.py` already manages for its own single-instance
   lock.

## Interface

```python
# src/sadana/gateway_unit.py (pure)
SERVICE_NAME = "sadana-gateway"

def unit_path() -> Path: ...  # /etc/systemd/system/sadana-gateway.service

def generate_unit(*, python_path: str, src_dir: str, working_dir: str, run_as_user: str) -> str:
    """The unit file's full text — see spec.md's own template above."""


# src/sadana/gateway_service.py (I/O)
def install(*, run_as_user: str | None = None) -> int:
    """Writes the unit, daemon-reloads, enables it. Refuses (returns 1) if
    not root. Idempotent — always regenerates and overwrites."""

def start() -> int: ...
def stop() -> int: ...
def restart() -> int: ...
def status() -> int:
    """Never fails on 'not running' — that's a normal status to report,
    same posture ExitReason.COMPLETED's sibling outcomes already take."""


# src/sadana/subcommands/gateway.py (extended, not new)
def cmd_gateway_install(args: argparse.Namespace) -> int: ...
def cmd_gateway_start(args: argparse.Namespace) -> int: ...
def cmd_gateway_stop(args: argparse.Namespace) -> int: ...
def cmd_gateway_restart(args: argparse.Namespace) -> int: ...
def cmd_gateway_status(args: argparse.Namespace) -> int: ...
```

## Acceptance criteria

- [ ] `gateway_unit.generate_unit()` called with fixed inputs returns text
      containing `Type=notify`, `Restart=on-failure`, `KillSignal=SIGTERM`,
      the given `python_path` in its `ExecStart` line, the given `src_dir`
      in its `PYTHONPATH` environment line, and no `WatchdogSec` or
      `ExecReload` directive — a direct unit test, no filesystem or
      subprocess involved.
- [ ] `gateway_unit.unit_path()` returns
      `/etc/systemd/system/sadana-gateway.service`.
- [ ] `build_gateway_parser()` wires `install`/`start`/`stop`/`restart`/
      `status` to their five handlers, `install` accepting an optional
      `--run-as-user`, matching `test_subcommands_gateway.py`'s own
      existing style for the `run` verb.
- [ ] `gateway_service.install()`, called as a non-root user (the real
      condition every CI/dev run is actually in), returns `1` and writes no
      file — a direct unit test asserting the refusal, not the success path.
- [ ] A standalone script, `scripts/prove_gateway_lifecycle_e2e.py`,
      matching `scripts/prove_gateway_webhook_e2e.py`'s existing shape, run
      by hand on a real systemd machine as root: installs the service,
      confirms the unit file and `systemctl is-enabled` both report
      correctly, starts it, confirms `systemctl is-active` reports running
      and a real webhook POST against it succeeds (reusing
      `SADANA_GATEWAY_WEBHOOK_SECRET` set in the test's own
      `/etc/sadana/gateway.env`), stops it, confirms it is no longer
      active, restarts it, confirms it comes back, and asserts the unit's
      `Restart=on-failure` actually recovers the process after a `kill -9`
      of its main PID. Its output is pasted into `review.md` §Evidence
      (CLAUDE.md's rule for a block's first real external round trip) —
      it does not run in `make test`.
- [ ] `make verify` ends `VERIFY OK`.

## Non-goals

- User-scope (`systemctl --user`) install, or any flag to choose between
  scopes — system-scope only, per intent.md's own resolved decision.
- Detecting or auto-repairing a stale installed unit — nothing about this
  item's fixed inputs can drift the way a multi-profile `HERMES_HOME` can.
- Graceful, in-place restart via a custom signal protocol, liveness
  probing, or wedge escalation — `systemctl restart` plus
  `gateway_daemon.py`'s already-graceful `SIGTERM` handling covers
  `restart` completely.
- Writing, prompting for, or generating `SADANA_GATEWAY_WEBHOOK_SECRET` or
  any other value the daemon needs — `install` only names the conventional
  `EnvironmentFile` path; the operator's own job to fill it.
- `gateway enroll`, `gateway list`, `gateway setup`, `gateway
  migrate-legacy`, or anything under `hermes proxy` — none are this item's
  five verbs, and several (enroll, proxy) were already declined for sadana
  by the CLI-SHELL blueprint itself.
- Fleet provisioning, or deciding how a brand-new machine gets its first
  setup — a separate, unbuilt system's job (intent.md §Affected users and
  systems).
- launchd (macOS) or Windows Scheduled Task equivalents — the deployed
  target is Ubuntu EC2 only.

## Rejected alternatives

- **Copying hermes's dual-scope (`--system`/user) design even though sadana
  only ever installs system-scope.** Rejected: a flag with one real value
  is not a flag, it is a comment; the whole D-Bus-preflight/linger
  subsystem that scope duality drags in has no sadana counterpart to
  protect.
- **Reusing hermes's `systemd_unit_is_current()`/refresh-on-install
  drift-repair chokepoint.** Rejected: nothing in this item's generated
  unit can go stale independently of a fresh `install` regenerating it —
  building a staleness detector for a staleness scenario that cannot occur
  here is speculative machinery with no real trigger.
- **A custom SIGUSR1-based graceful-restart protocol with liveness probing
  and wedge escalation**, matching hermes's own `systemd_restart()`.
  Rejected: `gateway_daemon.py`'s existing `SIGTERM` handling is already
  graceful (drains via Python's own non-daemon-thread join), so
  `systemctl restart`'s own stop-then-start sequence already does the
  right thing with zero new code in the daemon itself.
- **`Restart=always`** (hermes's own choice). Declined in favor of
  `Restart=on-failure`: `gateway_daemon.py` returns `1` and exits
  deliberately — not a crash — when the webhook secret is unset or another
  instance already holds the lock (`GATEWAY-DAEMON-01`'s own acceptance
  criteria). `Restart=always` would spin that intentional refusal into an
  infinite restart loop; `on-failure` only restarts on that same class of
  outcome systemd itself considers a failure, and a clean, deliberate exit
  is not one to loop on. (Considered `RestartPreventExitStatus=1` on top of
  `on-failure` to stop even the failure-class retries for this one
  specific exit code — declined for now: a start-up misconfiguration
  loops at a bounded `RestartSec=5` rather than hot-looping, and the
  operator sees it immediately in `journalctl`/`status`; revisit if a real
  "silently restart-looping on a misconfigured box" complaint shows up.)
- **`install` writing a default `/etc/sadana/gateway.env` template file for
  the operator to fill in.** Considered, since it would be a small
  convenience — rejected because intent.md's own constraint is explicit
  that secret configuration is out of this item's scope entirely, not
  "out of scope except for a starter template"; printing the expected path
  as a next-step message gives the same discoverability without `install`
  ever touching a file that might contain, or come to contain, a secret.

## Concerns

- **This item's own correctness cannot be verified by `make verify` alone**
  — the compliance pass at Deploy will have to trust a standalone script's
  output the same way `GATEWAY-DAEMON-01`'s did, but that script here needs
  root and a real systemd, which `GATEWAY-DAEMON-01`'s own webhook proof
  did not. Named directly in intent.md's own Constraints already, not new
  here, but worth restating: whoever runs the Deploy stage for this item
  needs access to a real Ubuntu box, not just this WSL dev environment.
- **No new CLAUDE.md rule is proposed by this spec.** The `EnvironmentFile`-
  based secret-injection convention (§Design) is a real, possibly-reusable
  pattern if a future systemd-installed sadana service ever needs its own
  secret — but this is the first systemd-anything item this project has
  built, so there is exactly one example, not two. Per this project's own
  registry-of-one posture applied to policy-writing itself: name it as a
  pattern worth reusing in a future item's own spec.md, not as a
  cross-block rule yet.
- **`install`'s default `--run-as-user` resolution (`$SUDO_USER`) silently
  does nothing useful if `install` is ever run without `sudo` at all** —
  `_require_root_for_system_service` already catches that case first
  (refuses before reaching the user-resolution step), so this is
  order-of-operations correct, not a live gap — named because it was the
  one place in the adopted hermes logic worth double-checking against our
  own `_require_root_for_system_service` call order during build.
