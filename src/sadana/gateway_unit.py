"""What a systemd unit for the gateway daemon looks like.

`docs/tasks/CLI-SHELL-05-gateway-lifecycle-verb/spec.md`. Pure: a template
string builder, no I/O — every environment-derived fact (which Python, where
the source tree lives, which user runs it) is an explicit parameter rather
than something this module reads for itself
(`testing-conventions`: "pure functions that take environment as data are
fine"). `gateway_service.py` is the one place that resolves those facts for
real and writes the result to disk.

System-scope only, one fixed service name — sadana has exactly one instance
per machine, so there is no profile or scope to derive a name from
(unlike hermes's own `get_service_name()`, `hermes_cli/gateway.py:2817-2827`).
"""

from __future__ import annotations

from pathlib import Path

SERVICE_NAME = "sadana-gateway"


def unit_path() -> Path:
    return Path("/etc/systemd/system") / f"{SERVICE_NAME}.service"


def generate_unit(*, python_path: str, src_dir: str, working_dir: str, run_as_user: str) -> str:
    """`Type=notify` matches `gateway_daemon.run()`'s own `_notify_systemd("READY=1")`
    call. No `WatchdogSec`: that call is one-shot at startup, not a periodic
    heartbeat, so declaring a watchdog here would have systemd kill a
    healthy daemon for never sending `WATCHDOG=1`. `Restart=on-failure`, not
    `always`: `gateway_daemon.run()` returns `1` deliberately (an unset
    secret, the flock already held) — not a crash — and `always` would spin
    that refusal into an infinite restart loop. No `ExecReload`: hermes wires
    `systemctl reload` to a SIGUSR1 graceful-restart protocol sadana's own
    daemon has no counterpart for; `restart` is the only verb for this,
    since a plain `systemctl restart` (stop, already graceful via
    `gateway_daemon.run()`'s own `SIGTERM` handling, then start) already
    does the right thing."""
    return f"""[Unit]
Description=sadana gateway daemon
After=network-online.target
Wants=network-online.target
StartLimitIntervalSec=0

[Service]
Type=notify
ExecStart={python_path} -m sadana.cli gateway run
WorkingDirectory={working_dir}
Environment="PYTHONPATH={src_dir}"
EnvironmentFile=-/etc/sadana/gateway.env
User={run_as_user}
Restart=on-failure
RestartSec=5
KillMode=mixed
KillSignal=SIGTERM
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
"""
