# Plan: CLI-SHELL-05 — gateway lifecycle verb (from intent.md 2026-09-08)

`intent.md` and `spec.md` are both written and approved. spec.md already did
the reference-corpus work (hermes's `hermes_cli/gateway.py:2817-4905`,
trimmed hard — no dual scope, no drift detection, no SIGUSR1 wedge-probing
restart, no cgroup cleanup, no legacy-unit migration) and named the exact
file layout and unit-file template. This plan turns that into an
implementation plan against the real current state of `src/sadana/`
(verified directly: `subcommands/gateway.py`, `gateway_daemon.py`,
`test_subcommands_gateway.py`, `test_gateway_daemon.py`, `pyproject.toml`'s
`sadana = "sadana.cli:main"` entry point and mypy config).

One discovery changes the Deploy-stage story from what intent.md assumed:
this WSL dev box actually has real systemd running (PID 1 is `systemd`) —
intent.md's own Constraints section assumed it didn't. Asked directly: the
maintainer wants the real e2e proof run here, with sudo prompts approved
live, rather than deferred to a separate real box.

## Files that change

- `src/sadana/gateway_unit.py` (new, pure) — `SERVICE_NAME`, `unit_path()`,
  `generate_unit()`.
- `tests/unit/test_gateway_unit.py` (new) — the unit-file template's
  load-bearing directives, and `unit_path()`.
- `src/sadana/gateway_service.py` (new, I/O) — `install()`, `start()`,
  `stop()`, `restart()`, `status()`, plus private helpers
  (`_python_path()`, `_src_dir()`, `_default_run_as_user()`,
  `_run_systemctl()`).
- `tests/unit/test_gateway_service.py` (new) — only the "requires root"
  refusal paths for `install`/`start`/`stop`/`restart`. `status()` is not
  unit-tested at all: unlike the other four, it doesn't require root, so
  exercising it means touching the real `/etc/systemd/system/` path
  outside any tmp sandboxing — that's the standalone script's job.
- `src/sadana/subcommands/gateway.py` (existing, extended) — five new
  nested subparsers (`install`/`start`/`stop`/`restart`/`status`) under the
  same `gateway` parser tree `GATEWAY-DAEMON-01` already built, and five
  new thin `cmd_gateway_*(args) -> int` handlers.
- `tests/unit/test_subcommands_gateway.py` (existing, extended) — parser
  wiring tests for the five new verbs.
- `scripts/prove_gateway_lifecycle_e2e.py` (new) — the real round trip:
  install → verify unit + enabled → start → verify active + a real webhook
  POST succeeds → stop → verify inactive → restart → verify it comes back
  → kill -9 the main PID → verify `Restart=on-failure` brings it back →
  uninstall as cleanup. Requires root; run via `sudo` with the maintainer
  approving each command. Output goes into `review.md` §Evidence at
  Deploy — not `make test`.

## Order of work

1. **`gateway_unit.py`** — pure template builder. `generate_unit(*,
   python_path, src_dir, working_dir, run_as_user) -> str` returns the unit
   text from spec.md's own template verbatim (`Type=notify`,
   `ExecStart={python_path} -m sadana.cli gateway run`,
   `WorkingDirectory={working_dir}`, `Environment="PYTHONPATH={src_dir}"`,
   `EnvironmentFile=-/etc/sadana/gateway.env`, `User={run_as_user}`,
   `Restart=on-failure`, `RestartSec=5`, `KillMode=mixed`,
   `KillSignal=SIGTERM`, `StandardOutput=journal`, `StandardError=journal`,
   `WantedBy=multi-user.target`). `SERVICE_NAME = "sadana-gateway"`;
   `unit_path() -> Path` returns
   `/etc/systemd/system/{SERVICE_NAME}.service`. `test_gateway_unit.py`
   asserts every directive named above is present verbatim, and that
   `WatchdogSec`/`ExecReload` are absent. No dependencies on anything new —
   safest step, goes first.

2. **`gateway_service.py`** — I/O.

   ```python
   def _python_path() -> str:
       if sys.prefix != sys.base_prefix:
           candidate = Path(sys.prefix) / "bin" / "python3"
           if candidate.exists():
               return str(candidate)
       return sys.executable

   def _src_dir() -> str:
       return str(Path(__file__).resolve().parent.parent)  # .../src

   def _default_run_as_user() -> str:
       username = os.environ.get("SUDO_USER") or os.environ.get("USER") or os.environ.get("LOGNAME") or getpass.getuser()
       if not username:
           raise ValueError("could not determine which user the gateway service should run as")
       return username

   def _run_systemctl(args: list[str], *, check: bool, **kwargs: object) -> subprocess.CompletedProcess:
       try:
           return subprocess.run(["systemctl", *args], check=check, **kwargs)
       except FileNotFoundError as exc:
           raise RuntimeError("systemctl is not available on this machine") from exc

   def install(*, run_as_user: str | None = None) -> int:
       if os.geteuid() != 0:
           print("gateway install requires root; re-run with sudo", file=sys.stderr)
           return 1
       user = run_as_user or _default_run_as_user()
       if user == "root" and run_as_user is None:
           print("refusing to install as root; pass --run-as-user root to override", file=sys.stderr)
           return 1
       python_path, src_dir = _python_path(), _src_dir()
       unit_text = gateway_unit.generate_unit(
           python_path=python_path, src_dir=src_dir,
           working_dir=str(Path(src_dir).parent), run_as_user=user,
       )
       path = gateway_unit.unit_path()
       path.parent.mkdir(parents=True, exist_ok=True)
       path.write_text(unit_text, encoding="utf-8")
       _run_systemctl(["daemon-reload"], check=True)
       _run_systemctl(["enable", gateway_unit.SERVICE_NAME], check=True)
       print(f"installed {path}")
       print("before starting: /etc/sadana/gateway.env must set SADANA_GATEWAY_WEBHOOK_SECRET")
       print("next: sudo sadana gateway start")
       return 0

   def _require_installed() -> bool:
       if not gateway_unit.unit_path().exists():
           print("gateway service is not installed; run: sudo sadana gateway install", file=sys.stderr)
           return False
       return True

   def start() -> int:
       if os.geteuid() != 0:
           print("gateway start requires root; re-run with sudo", file=sys.stderr)
           return 1
       if not _require_installed():
           return 1
       _run_systemctl(["start", gateway_unit.SERVICE_NAME], check=True)
       print("gateway service started")
       return 0

   # stop(), restart(): same root+installed guard, then a plain
   # `systemctl stop|restart <SERVICE_NAME>` call.

   def status() -> int:
       if not gateway_unit.unit_path().exists():
           print("gateway service is not installed", file=sys.stderr)
           return 1
       _run_systemctl(["status", gateway_unit.SERVICE_NAME, "--no-pager"], check=False)
       result = _run_systemctl(
           ["is-active", gateway_unit.SERVICE_NAME], check=False, capture_output=True, text=True
       )
       active = (result.stdout or "").strip() == "active"
       print("gateway service is running" if active else "gateway service is stopped")
       return 0 if active else 1
   ```

   **Amended after the self-check and the real e2e run** (this plan's own
   `_python_path()` sketch above is superseded — see spec.md's Design
   section, "Declined, not adopted"): the four root-checks collapsed into
   one shared `_require_root(action) -> bool`; `start`/`stop`/`restart`
   collapsed into one shared `_control(verb, past_tense) -> int`;
   `_python_path()` drops the `sys.prefix`-probing tier entirely and
   resolves `<repo>/.venv/bin/python3` directly — a first real run of the
   e2e script (step 4) caught it picking the system Python (no `tomllib`)
   under `sudo`, and the fix is also the simpler function, not just a
   patch. `_run_systemctl()`'s `**kwargs: object` became explicit
   `capture_output`/`text` parameters (mypy's `subprocess.run` overloads
   don't resolve through `**kwargs`).

   `test_gateway_service.py`: `install()`, `start()`, `stop()`,
   `restart()` each called with no special setup → `1`, verified against
   this process's genuinely real non-root identity (matching
   `test_gateway_daemon.py`'s own precedent of relying on a real unset env
   var rather than monkeypatching one).

3. **`subcommands/gateway.py`** — five new nested subparsers under the
   existing `gateway` parser (`install` with an optional `--run-as-user`;
   `start`/`stop`/`restart`/`status` with no flags), five thin
   `cmd_gateway_*` handlers each calling straight into `gateway_service`,
   matching `cmd_gateway_run`'s own existing import-and-call shape. Add
   `from sadana import gateway_service`. `test_subcommands_gateway.py` gets
   one new test per verb confirming `args.func` and default flag values.

4. **`scripts/prove_gateway_lifecycle_e2e.py`** — last, the only step
   touching real root/systemd/a real bound port together. Structure:
   - Guarded `if __name__ == "__main__":` requiring `os.geteuid() == 0`.
   - Writes a throwaway `/etc/sadana/gateway.env` with a fixed test
     `SADANA_GATEWAY_WEBHOOK_SECRET` before starting (cleanup removes it).
   - `install()` → assert `gateway_unit.unit_path()` exists and
     `systemctl is-enabled sadana-gateway` reports `enabled`.
   - `start()` → assert `systemctl is-active` reports `active`; POST a
     real webhook request with a wrong secret and assert `401`.
     **Amended from the original plan's "assert `200`"**: `install`
     launches `sadana gateway run` as a *separate* systemd-managed OS
     process, so this script cannot monkeypatch that process's own
     `model_access.send` the way `prove_gateway_webhook_e2e.py` could in
     its own single process — a full successful turn would need a real
     `OPENROUTER_API_KEY` and a real network call, which isn't what this
     item proves. `401` still proves the real, systemd-installed process
     is genuinely bound and serving the real webhook protocol; the
     successful-turn round trip is `GATEWAY-DAEMON-01`'s own already-proven
     job.
   - `stop()` → assert `is-active` no longer reports `active`.
   - `restart()` → assert it comes back active.
   - `kill -9` the unit's `MainPID` → wait, then assert `is-active`
     reports `active` again with a new PID — proving `Restart=on-failure`
     recovers a real crash.
   - Cleanup, always run (`try`/`finally`): stop, disable, remove the unit
     file, daemon-reload, remove the throwaway env file.
   - Plain `assert`s with messages, printed step by step, matching
     `prove_gateway_webhook_e2e.py`'s own shape. Output pasted into
     `review.md` §Evidence at Deploy.

5. **Self-check and `make verify`.** `/ponytail-review` + `/simplify`
   against the full diff, apply what's worth taking now, note the rest in
   one sentence each — and if a self-check finding changes anything this
   plan committed to in writing, amend this plan.md in the same diff (the
   lesson from `GATEWAY-DAEMON-01`'s own Deploy-stage cold review). Then
   `make verify`, paste the output.

## Risks

**What could this change break?** `subcommands/gateway.py`'s existing `run`
verb and its tests — extending the same file/parser tree. Mitigated by
running `test_subcommands_gateway.py` narrowly right after step 3, and by
never touching `cmd_gateway_run`'s own body. Nothing else in the existing
codebase is touched.

**Most risky step, and why it's last:** step 4, the only one touching real
root privilege, a real systemd instance, and a real bound port together —
same reasoning `GATEWAY-DAEMON-01`'s own plan used for its riskiest step.
Runs only after `gateway_unit.py`/`gateway_service.py`'s deterministic
paths are already unit-tested. Step 4 is also destructive-adjacent (writes
a real system unit, starts a real process, sends a real `kill -9`) —
mitigated by a `try`/`finally` cleanup that always runs, and by the
maintainer approving each `sudo` command live.

**Re-checked against spec.md's own Rejected Alternatives — no drift
found:** no `--system`/user-scope flag; no drift-detection/auto-repair on
`install`; no SIGUSR1 restart protocol, liveness probe, or wedge
escalation; `Restart=on-failure`, not `Restart=always`; no `ExecStopPost`
cgroup cleanup; no legacy-unit migration; `install` never writes
`/etc/sadana/gateway.env` itself, only prints the path.

## Proof

- `test_gateway_unit.py` green after step 1.
- `test_gateway_service.py` green after step 2.
- `test_subcommands_gateway.py` green after step 3, including its
  pre-existing `run`-verb tests unmodified.
- `scripts/prove_gateway_lifecycle_e2e.py`'s full printed output — install
  → enabled, start → active + real webhook `200`, stop → inactive, restart
  → active again, `kill -9` → recovered, cleanup leaves no trace — run for
  real in this session with sudo, pasted into `review.md` §Evidence at
  Deploy.
- `/ponytail-review` + `/simplify` findings triaged; plan.md amended in
  the same diff if any finding changes something it committed to.
- `make verify` output ending `VERIFY OK`.
