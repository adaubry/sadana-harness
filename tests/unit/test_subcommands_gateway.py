"""Tests for sadana.subcommands.gateway: parser wiring only.

`cmd_gateway_run()` itself binds a real socket and blocks until a signal —
not unit-testable without either a real socket or a fully-faked daemon
lifecycle. That real round trip is `scripts/prove_gateway_webhook_e2e.py`'s
job (spec.md's acceptance criteria), not this suite's.
"""

from __future__ import annotations

import argparse
import threading

import pytest

from sadana import gateway_daemon, scheduling
from sadana.subcommands.gateway import (
    build_gateway_parser,
    cmd_gateway_install,
    cmd_gateway_restart,
    cmd_gateway_run,
    cmd_gateway_start,
    cmd_gateway_status,
    cmd_gateway_stop,
)


@pytest.mark.unit
def test_cmd_gateway_run_refuses_to_start_when_secret_is_unset(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Migrated from `test_gateway_daemon.py` (PLUGIN-MARKET-01): this
    check moved from `gateway_daemon.run()` into its one caller, since the
    daemon's own generic lifecycle has no business knowing what a
    "secret" is. `gateway_daemon.run` is monkeypatched to fail the test
    outright if reached, proving the guard fires first."""
    monkeypatch.delenv("SADANA_GATEWAY_WEBHOOK_SECRET", raising=False)

    def _fail_if_reached(**_kwargs: object) -> int:
        raise AssertionError("gateway_daemon.run should not have been reached")

    monkeypatch.setattr(gateway_daemon, "run", _fail_if_reached)

    result = cmd_gateway_run(argparse.Namespace(host=None, port=None))

    assert result == 1
    assert "SADANA_GATEWAY_WEBHOOK_SECRET" in capsys.readouterr().err


@pytest.mark.unit
def test_cmd_gateway_run_starts_the_scheduling_tick_thread_before_the_daemon(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The only other existing test for this function
    (`..._refuses_to_start_when_secret_is_unset`) returns before reaching
    this code at all, so this is the first real coverage of the thread-start
    line GATEWAY-DAEMON-02 added. Both `gateway_daemon.run` and
    `scheduling.run_tick_loop` are monkeypatched — the former to avoid a
    real socket bind, the latter (real code, real `daemon=True` thread) to
    avoid an actual infinite tick loop running in the background. Waits on
    a real `threading.Event` rather than a fixed sleep, since the thread
    genuinely races this test's own assertions (testing-conventions: no
    timing-based flakiness)."""
    monkeypatch.setenv("SADANA_GATEWAY_WEBHOOK_SECRET", "s")
    monkeypatch.setattr(gateway_daemon, "run", lambda **_kwargs: 0)

    called = threading.Event()
    seen: dict[str, object] = {}

    def _fake_run_tick_loop(**kwargs: object) -> None:
        seen.update(kwargs)
        called.set()

    monkeypatch.setattr(scheduling, "run_tick_loop", _fake_run_tick_loop)

    result = cmd_gateway_run(argparse.Namespace(host=None, port=None))

    assert result == 0
    assert called.wait(timeout=5), "scheduling.run_tick_loop was never started"
    assert seen["interval_seconds"] == 30
    # the connection, the plugin scan, the provider, the model and the
    # recorder all travel inside the one runtime now
    assert "runtime" in seen


@pytest.mark.unit
def test_build_gateway_parser_defaults_and_wiring() -> None:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    build_gateway_parser(subparsers)

    args = parser.parse_args(["gateway", "run"])
    assert (args.host, args.port) == (None, None)
    assert args.func is cmd_gateway_run


@pytest.mark.unit
def test_build_gateway_parser_accepts_host_and_port() -> None:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    build_gateway_parser(subparsers)

    args = parser.parse_args(["gateway", "run", "--host", "0.0.0.0", "--port", "9000"])
    assert (args.host, args.port) == ("0.0.0.0", 9000)


@pytest.mark.unit
def test_build_gateway_parser_requires_the_run_subcommand() -> None:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    build_gateway_parser(subparsers)

    with pytest.raises(SystemExit) as exc:
        parser.parse_args(["gateway"])
    assert exc.value.code == 2


@pytest.mark.unit
def test_build_gateway_parser_install_defaults_and_wiring() -> None:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    build_gateway_parser(subparsers)

    args = parser.parse_args(["gateway", "install"])
    assert args.run_as_user is None
    assert args.func is cmd_gateway_install


@pytest.mark.unit
def test_build_gateway_parser_install_accepts_run_as_user() -> None:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    build_gateway_parser(subparsers)

    args = parser.parse_args(["gateway", "install", "--run-as-user", "deploy"])
    assert args.run_as_user == "deploy"


@pytest.mark.unit
@pytest.mark.parametrize(
    ("verb", "handler"),
    [
        ("start", cmd_gateway_start),
        ("stop", cmd_gateway_stop),
        ("restart", cmd_gateway_restart),
        ("status", cmd_gateway_status),
    ],
)
def test_build_gateway_parser_control_verbs_wiring(verb: str, handler: object) -> None:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    build_gateway_parser(subparsers)

    args = parser.parse_args(["gateway", verb])
    assert args.func is handler
