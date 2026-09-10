"""Tests for sadana.subcommands.editor: parser wiring and the loopback refusal.

`cmd_editor()` binds a real socket and blocks until a signal, so the serving
itself belongs to `scripts/prove_editor_e2e.py`, not here — the same split
`test_subcommands_gateway.py` already keeps.
"""

from __future__ import annotations

import argparse

import pytest

from sadana import gateway_daemon
from sadana.cli import build_parser
from sadana.subcommands.editor import build_editor_parser, cmd_editor


@pytest.mark.unit
def test_editor_refuses_to_bind_anywhere_but_this_machine(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The editor has no authentication, so a public bind is refused rather
    than served — hermes shipped the opposite and had to take it back.
    `gateway_daemon.run` fails the test outright if reached, proving the
    check fires before anything binds."""
    monkeypatch.setenv("SADANA_EDITOR_HOST", "0.0.0.0")

    def _fail_if_reached(**_kwargs: object) -> int:
        raise AssertionError("gateway_daemon.run should not have been reached")

    monkeypatch.setattr(gateway_daemon, "run", _fail_if_reached)

    assert cmd_editor(argparse.Namespace(port=None)) == 1
    assert "refuses to bind" in capsys.readouterr().err


@pytest.mark.unit
@pytest.mark.parametrize("host", ["127.0.0.1", "localhost", "::1"])
def test_editor_serves_the_loopback_addresses(monkeypatch: pytest.MonkeyPatch, host: str) -> None:
    monkeypatch.setenv("SADANA_EDITOR_HOST", host)
    monkeypatch.setattr(gateway_daemon, "run", lambda **_kwargs: 0)

    assert cmd_editor(argparse.Namespace(port=None)) == 0


@pytest.mark.unit
def test_editor_creates_the_plugins_root_if_it_is_missing(monkeypatch: pytest.MonkeyPatch, tmp_path: object) -> None:
    """A first run on a fresh machine opens an empty editor rather than
    failing on a directory nobody has made yet."""
    from sadana import plugins

    monkeypatch.setattr(gateway_daemon, "run", lambda **_kwargs: 0)
    root = plugins._plugins_root()
    assert not root.exists()

    cmd_editor(argparse.Namespace(port=None))

    assert root.is_dir()


@pytest.mark.unit
def test_build_editor_parser_wiring() -> None:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    build_editor_parser(subparsers)

    args = parser.parse_args(["editor", "--port", "9999"])
    assert args.port == 9999
    assert args.func is cmd_editor


@pytest.mark.unit
def test_editor_is_reachable_from_the_top_level_parser() -> None:
    args = build_parser().parse_args(["editor"])
    assert args.func is cmd_editor
    assert args.port is None
