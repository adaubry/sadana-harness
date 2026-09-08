"""Tests for sadana.gateway_unit."""

from __future__ import annotations

from pathlib import Path

import pytest

from sadana.gateway_unit import SERVICE_NAME, generate_unit, unit_path


@pytest.mark.unit
def test_unit_path_is_the_fixed_system_location() -> None:
    assert unit_path() == Path("/etc/systemd/system") / f"{SERVICE_NAME}.service"


@pytest.mark.unit
def test_generate_unit_contains_the_load_bearing_directives() -> None:
    text = generate_unit(
        python_path="/opt/venv/bin/python3",
        src_dir="/opt/sadana/src",
        working_dir="/opt/sadana",
        run_as_user="deploy",
    )
    assert "Type=notify" in text
    assert "Restart=on-failure" in text
    assert "RestartSec=5" in text
    assert "KillMode=mixed" in text
    assert "KillSignal=SIGTERM" in text
    assert "ExecStart=/opt/venv/bin/python3 -m sadana.cli gateway run" in text
    assert "WorkingDirectory=/opt/sadana" in text
    assert 'Environment="PYTHONPATH=/opt/sadana/src"' in text
    assert "EnvironmentFile=-/etc/sadana/gateway.env" in text
    assert "User=deploy" in text
    assert "StandardOutput=journal" in text
    assert "StandardError=journal" in text
    assert "WantedBy=multi-user.target" in text


@pytest.mark.unit
def test_generate_unit_never_sets_a_watchdog_or_reload_signal() -> None:
    # No periodic sd_notify heartbeat exists in gateway_daemon.py — a
    # WatchdogSec here would have systemd kill a healthy daemon. No SIGUSR1
    # restart protocol exists either — ExecReload would silently do nothing.
    text = generate_unit(python_path="/usr/bin/python3", src_dir="/x/src", working_dir="/x", run_as_user="u")
    assert "WatchdogSec" not in text
    assert "ExecReload" not in text
