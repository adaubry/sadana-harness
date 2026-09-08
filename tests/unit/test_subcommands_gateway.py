"""Tests for sadana.subcommands.gateway: parser wiring only.

`cmd_gateway_run()` itself binds a real socket and blocks until a signal —
not unit-testable without either a real socket or a fully-faked daemon
lifecycle. That real round trip is `scripts/prove_gateway_webhook_e2e.py`'s
job (spec.md's acceptance criteria), not this suite's.
"""

from __future__ import annotations

import argparse

import pytest

from sadana.subcommands.gateway import build_gateway_parser, cmd_gateway_run


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
