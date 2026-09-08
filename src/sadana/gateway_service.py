"""Installing and controlling the gateway daemon as a real systemd service.

`docs/tasks/CLI-SHELL-05-gateway-lifecycle-verb/spec.md`. I/O: writes a real
unit file, runs `systemctl`, reads process identity. Owns none of the
daemon's own lifecycle — that's `gateway_daemon.py` — and none of the unit
file's own content — that's the pure `gateway_unit.py`. This module only
answers "is the daemon installed as a service," "start/stop/restart it,"
and "is it running."
"""

from __future__ import annotations

import getpass
import os
import subprocess
import sys
from pathlib import Path

from sadana import gateway_unit


def _src_dir() -> str:
    return str(Path(__file__).resolve().parent.parent)  # .../src


def _python_path() -> str:
    """CLAUDE.md states, unconditionally, that this project's Python lives
    in `.venv` at a fixed, known location — never activated, never one of
    several possible environments (unlike hermes, which genuinely supports
    multiple activated/`uv run` environments and asks "which venv is the
    *invoking* interpreter in," `get_python_path()`,
    `hermes_cli/gateway.py:3700-3716`). Probing `sys.prefix` for that
    question doesn't fit sadana's own single-venv invariant, and asking it
    anyway is exactly what broke a first real run of this item's own e2e
    proof: `sudo sadana gateway install` invoked a bare `python3` with
    `sys.prefix == sys.base_prefix`, so the probe fell through to
    `sys.executable` — the system interpreter (3.10, no `tomllib`) — instead
    of this project's own `.venv` Python (3.11). Resolving `.venv` directly
    is both the fix and the simpler function; `sys.executable` remains only
    as a last resort for a checkout `make` hasn't built yet."""
    venv_python = Path(_src_dir()).parent / ".venv" / "bin" / "python3"
    if venv_python.exists():
        return str(venv_python)
    return sys.executable


def _default_run_as_user() -> str:
    """Adopted from hermes's `_system_service_identity()`
    (`hermes_cli/gateway.py:3341-3372`), trimmed: sadana's `install` always
    configures the same user it resolves here (or an explicit override),
    never a different target user, so no cross-user path remapping is
    needed."""
    username = os.environ.get("SUDO_USER") or os.environ.get("USER") or os.environ.get("LOGNAME") or getpass.getuser()
    if not username:
        raise ValueError("could not determine which user the gateway service should run as")
    return username


def _require_root(action: str) -> bool:
    if os.geteuid() != 0:
        print(f"gateway {action} requires root; re-run with sudo", file=sys.stderr)
        return False
    return True


def _run_systemctl(
    args: list[str], *, check: bool, capture_output: bool = False, text: bool = False
) -> subprocess.CompletedProcess:
    """Adopted from hermes's `_run_systemctl()` (`hermes_cli/gateway.py:3086-3098`):
    a missing `systemctl` binary becomes one clear error, not a raw
    `FileNotFoundError` traceback."""
    try:
        return subprocess.run(["systemctl", *args], check=check, capture_output=capture_output, text=text)
    except FileNotFoundError as exc:
        raise RuntimeError("systemctl is not available on this machine") from exc


def _run_systemctl_checked(args: list[str], *, action: str) -> bool:
    """`_run_systemctl(..., check=True)` failing is an expected outcome for
    a CLI handler contracted to return an `int` (CLAUDE.md's own rule) —
    e.g. `gateway_daemon.run()` deliberately exits `1` without calling
    `sd_notify` when the webhook secret is unset or its own flock is
    already held, and with `Type=notify` that makes `systemctl
    start`/`restart` itself fail. `systemctl`'s own error text already
    reached the terminal (no `capture_output` here); this just stops it
    from becoming an uncaught traceback on top."""
    try:
        _run_systemctl(args, check=True)
        return True
    except subprocess.CalledProcessError:
        print(f"gateway {action} failed — see systemctl's own output above", file=sys.stderr)
        return False


def install(*, run_as_user: str | None = None) -> int:
    """Writes the unit, daemon-reloads, enables it. Refuses (returns `1`,
    writes nothing) if not root. Idempotent — always regenerates and
    overwrites; nothing about a sadana install can drift the way a
    multi-profile hermes install can (spec.md's own Rejected alternatives),
    so there is no staleness to detect."""
    if not _require_root("install"):
        return 1

    user = run_as_user or _default_run_as_user()
    if user == "root" and run_as_user is None:
        print(
            "refusing to install the gateway service to run as root; pass --run-as-user root to override",
            file=sys.stderr,
        )
        return 1

    python_path = _python_path()
    src_dir = _src_dir()
    unit_text = gateway_unit.generate_unit(
        python_path=python_path,
        src_dir=src_dir,
        working_dir=str(Path(src_dir).parent),
        run_as_user=user,
    )
    path = gateway_unit.unit_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(unit_text, encoding="utf-8")

    if not _run_systemctl_checked(["daemon-reload"], action="install"):
        return 1
    if not _run_systemctl_checked(["enable", gateway_unit.SERVICE_NAME], action="install"):
        return 1

    print(f"installed {path}")
    print("before starting: /etc/sadana/gateway.env must set SADANA_GATEWAY_WEBHOOK_SECRET")
    print("next: sudo sadana gateway start")
    return 0


def _require_installed() -> bool:
    if not gateway_unit.unit_path().exists():
        print("gateway service is not installed; run: sudo sadana gateway install", file=sys.stderr)
        return False
    return True


def _control(verb: str, past_tense: str) -> int:
    if not _require_root(verb):
        return 1
    if not _require_installed():
        return 1
    if not _run_systemctl_checked([verb, gateway_unit.SERVICE_NAME], action=verb):
        return 1
    print(f"gateway service {past_tense}")
    return 0


def start() -> int:
    return _control("start", "started")


def stop() -> int:
    return _control("stop", "stopped")


def restart() -> int:
    return _control("restart", "restarted")


def status() -> int:
    """Never fails on "not running" — that's a normal status to report, the
    same posture `ExitReason.COMPLETED`'s sibling outcomes already take.
    Returns `0` if active, `1` otherwise (matching `systemctl is-active`'s
    own exit-code convention) — including when the service isn't installed
    at all."""
    if not _require_installed():
        return 1

    _run_systemctl(["status", gateway_unit.SERVICE_NAME, "--no-pager"], check=False)
    result = _run_systemctl(["is-active", gateway_unit.SERVICE_NAME], check=False, capture_output=True, text=True)
    active = (result.stdout or "").strip() == "active"
    print("gateway service is running" if active else "gateway service is stopped")
    return 0 if active else 1
