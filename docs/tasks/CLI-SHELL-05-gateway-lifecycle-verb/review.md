# Review: CLI-SHELL-05 — gateway lifecycle verb (from plan.md 2026-09-08)

Reviewed: f47862e..HEAD (working tree, uncommitted) — 10 files, +1331/-9
Reviewer context: fresh subagent (general-purpose, no prior context beyond
the diff and the three artifacts) — delegated per deploy-skill's own
instruction, since this session wrote the code under review.
Second opinion: none beyond that — `/ponytail-review` + `/simplify` ran
during build as a self-check (four parallel review agents), not a
substitute for this cold review.

## Evidence

```
$ make verify
docs/tasks/CLI-SHELL-05-gateway-lifecycle-verb/intent.md: note — reads like design, not intent — module names and code belong in spec.md
docs/tasks/CLI-SHELL-05-gateway-lifecycle-verb: all present artifacts valid
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
ruff-format................................................................Passed
shellcheck.................................................................Passed
Detect secrets.............................................................Passed
docs/reference/ citations resolve to tracked files.......................Passed
LINT OK
Success: no issues found in 24 source files
TYPES OK
▶ hermetic run: TZ=UTC LANG=C.UTF-8 PYTHONHASHSEED=0, empty env
........................................................................ [ 17%]
........................................................................ [ 34%]
........................................................................ [ 52%]
........................................................................ [ 69%]
........................................................................ [ 87%]
....................................................                     [100%]
412 passed in 4.15s
TESTS OK
VERIFY OK
```

```
$ sudo scripts/prove_gateway_lifecycle_e2e.py
=== writing a throwaway /etc/sadana/gateway.env ===
[ok] wrote /etc/sadana/gateway.env

=== install ===
Created symlink /etc/systemd/system/multi-user.target.wants/sadana-gateway.service → /etc/systemd/system/sadana-gateway.service.
installed /etc/systemd/system/sadana-gateway.service
before starting: /etc/sadana/gateway.env must set SADANA_GATEWAY_WEBHOOK_SECRET
next: sudo sadana gateway start
[ok] /etc/systemd/system/sadana-gateway.service installed and enabled

=== start ===
gateway service started
[ok] service is active

=== a real HTTP request against the systemd-supervised process ===
[ok] the real, systemd-supervised process answered a real HTTP request (401 for a wrong secret)

=== stop ===
gateway service stopped
[ok] service is inactive

=== restart ===
gateway service restarted
[ok] service is active again after restart

=== kill -9 the main PID, prove Restart=on-failure recovers it ===
killing PID 474213...
[ok] service recovered with a new PID 474319 (was 474213)

ALL ASSERTIONS PASSED

=== cleanup ===
Removed /etc/systemd/system/multi-user.target.wants/sadana-gateway.service.
[ok] cleanup complete — unit removed, daemon-reloaded, env file removed
```

This is the final of five real runs against this WSL box's own real systemd
during build. The first two caught and fixed real bugs live (see `## Findings`
below and plan.md's own inline "Amended..." notes): a wrong-Python-interpreter
bug in `_python_path()` (resolved system Python 3.10, no `tomllib`, instead of
this project's own `.venv` Python 3.11, when invoked via `sudo` with no active
venv), and a false-positive-shaped assertion in this proof script itself (a
recovered `MainPID` of `0` satisfied `!= pid_before` trivially without being a
real process). Both are fixed in this diff, each re-proven by a subsequent
real run; this final run also re-confirms the error-handling fix from the
cold review below (§Findings, Important #1) didn't disturb the happy path.

## Findings

Two Important findings from the cold review, both fixed in this branch before
this evidence was gathered; two Nits left as accepted, low-value cost.

### Important

- **[Bugs] — FIXED.** `src/sadana/gateway_service.py` — `install()`'s
  `daemon-reload`/`enable` calls and `_control()`'s `start`/`stop`/`restart`
  call all used `_run_systemctl(..., check=True)` with no surrounding
  `try`/`except`, so a real `systemctl` failure raised an uncaught
  `subprocess.CalledProcessError` out of a function whose contract
  (CLAUDE.md: "A CLI subcommand's handler returns an int for its own exit
  code") is to return `int`. The realistic trigger is exactly the scenario
  spec.md's own design already treats as normal: `gateway_daemon.run()`
  deliberately returns `1` without calling `sd_notify` when the webhook
  secret is unset or its own flock is already held — with `Type=notify`,
  that makes `systemctl start`/`restart` itself fail, so `sadana gateway
  start` on a misconfigured box would have printed systemd's own error text
  followed by a raw Python traceback instead of a clean exit code. Fix: a
  new `_run_systemctl_checked()` catches `subprocess.CalledProcessError`
  and returns `bool`; `install()`/`_control()` use it for every `check=True`
  call and return `1` cleanly on failure. Re-verified live: the final real
  run above still completes the full happy path with no regression.
- **[Compliance] — FIXED.** `tests/unit/test_gateway_service.py` —
  spec.md's acceptance criterion 4 reads: "`gateway_service.install()`,
  called as a non-root user … returns `1` and **writes no file**." The
  original test only asserted the return value, never that
  `gateway_unit.unit_path()` stayed absent — the "writes no file" half had
  no test backing it at all. Fix: `test_install_refuses_before_writing_the_unit_file`
  asserts the path is absent both before and after the refused call.

### Nits

- `src/sadana/gateway_service.py` — `--run-as-user` is written into
  `User=<value>` with no check that the account actually exists; a typo
  only surfaces at `start` time via systemd's own error, one step later
  than necessary. Low severity — the caller is already root by the time
  this path is reached. Left as-is.
- `_require_installed()`'s message and `status()`'s former inline
  duplicate of the same check were phrased slightly differently — fixed as
  a side effect of the Important bug fix above (`status()` now calls
  `_require_installed()` directly instead of repeating the check), so this
  is resolved too, not left open.

## What was checked and found clean

- **Acceptance criteria** (spec.md): all six checked — `generate_unit()`'s
  directives and `unit_path()` in `tests/unit/test_gateway_unit.py`; parser
  wiring for all five verbs in `tests/unit/test_subcommands_gateway.py`;
  the install-refusal criterion (both halves, after the fix above); the
  standalone script's full real round trip, pasted above.
- **plan.md's `## Files that change`** vs. the diff: exact match — no file
  touched that the plan didn't name, no named file left untouched.
- **spec.md's `## Rejected alternatives`**: re-checked against the diff —
  no `--system`/user-scope flag, no drift-detection/auto-repair, no SIGUSR1
  restart protocol or `ExecReload`, `Restart=on-failure` not `always`, no
  `ExecStopPost` cgroup cleanup, no legacy-unit migration, `install()`
  never opens `/etc/sadana/gateway.env`. No drift found.
- **The declined `sys.prefix`-probing tier** (spec.md's own live
  correction, mid-build): confirmed `_python_path()` resolves
  `<repo>/.venv/bin/python3` directly, no `sys.prefix`/`sys.base_prefix`
  reference anywhere in the function.
- **Security**: the real file write (`/etc/systemd/system/sadana-gateway.service`)
  carries no secret — `EnvironmentFile=-/etc/sadana/gateway.env` is the
  only secret-adjacent line, and `install()` never opens that path itself.
  Every `subprocess` call is a fixed-list `["systemctl", *args]`, `shell`
  never set — no injection surface. `--run-as-user` reaches only an
  f-string into the unit file, not a shell command, and the caller
  supplying it is already root-gated. `_default_run_as_user()`'s
  `SUDO_USER`→`USER`→`LOGNAME`→`getpass.getuser()` chain only runs after
  `_require_root()` has already gated non-root callers out. No secrets or
  PII in any `print()` call anywhere in this diff.

## Decision

Approved by Adam, 2026-09-08. Both Important findings were already fixed
in this branch before approval (see `## Findings` above); both Nits were
reviewed and accepted as low-value cost, not silently dropped.
