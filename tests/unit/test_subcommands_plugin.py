"""Tests for sadana.subcommands.plugin."""

from __future__ import annotations

import argparse
from pathlib import Path

import pytest

from conftest import make_upstream_repo as _make_upstream_repo
from sadana.subcommands.plugin import build_plugin_parser, cmd_plugin_install, cmd_plugin_register


@pytest.mark.unit
def test_cmd_plugin_register_prints_confirmation_and_returns_zero(capsys: pytest.CaptureFixture[str]) -> None:
    args = argparse.Namespace(name="greeter", repo_url="https://example.invalid/greeter.git")
    assert cmd_plugin_register(args) == 0
    assert "greeter" in capsys.readouterr().out


@pytest.mark.unit
def test_cmd_plugin_register_twice_fails_on_stderr(capsys: pytest.CaptureFixture[str]) -> None:
    args = argparse.Namespace(name="greeter", repo_url="https://example.invalid/greeter.git")
    cmd_plugin_register(args)
    assert cmd_plugin_register(args) == 1
    assert "already registered" in capsys.readouterr().err


@pytest.mark.unit
def test_cmd_plugin_install_happy_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = _make_upstream_repo(tmp_path)
    plugins_root = tmp_path / "plugins"
    monkeypatch.setenv("SADANA_PLUGINS_DIR", str(plugins_root))

    cmd_plugin_register(argparse.Namespace(name="greeter", repo_url=str(repo)))
    exit_code = cmd_plugin_install(argparse.Namespace(name="greeter", tag="v1.0.0", replace=False))

    assert exit_code == 0
    assert "installed" in capsys.readouterr().out
    assert (plugins_root / "greeter" / "plugin.toml").is_file()


@pytest.mark.unit
def test_cmd_plugin_install_unknown_name_fails_on_stderr(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("SADANA_PLUGINS_DIR", str(tmp_path / "plugins"))
    exit_code = cmd_plugin_install(argparse.Namespace(name="nobody-registered-this", tag="v1.0.0", replace=False))
    assert exit_code == 1
    assert "not registered" in capsys.readouterr().err


@pytest.mark.unit
def test_cmd_plugin_install_already_installed_fails_without_replace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = _make_upstream_repo(tmp_path)
    monkeypatch.setenv("SADANA_PLUGINS_DIR", str(tmp_path / "plugins"))
    cmd_plugin_register(argparse.Namespace(name="greeter", repo_url=str(repo)))
    cmd_plugin_install(argparse.Namespace(name="greeter", tag="v1.0.0", replace=False))

    exit_code = cmd_plugin_install(argparse.Namespace(name="greeter", tag="v1.0.0", replace=False))

    assert exit_code == 1
    assert "already installed" in capsys.readouterr().err


@pytest.mark.unit
def test_build_plugin_parser_wires_register_and_install() -> None:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    build_plugin_parser(subparsers)

    register_args = parser.parse_args(["plugin", "register", "greeter", "https://example.invalid/greeter.git"])
    assert register_args.func is cmd_plugin_register
    assert register_args.name == "greeter"
    assert register_args.repo_url == "https://example.invalid/greeter.git"

    install_args = parser.parse_args(["plugin", "install", "greeter", "v1.0.0", "--replace"])
    assert install_args.func is cmd_plugin_install
    assert install_args.tag == "v1.0.0"
    assert install_args.replace is True

    with pytest.raises(SystemExit):
        parser.parse_args(["plugin"])
