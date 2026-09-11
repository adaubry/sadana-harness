"""Tests for sadana.subcommands.plugin."""

from __future__ import annotations

import argparse
import io
from pathlib import Path

import pytest

from conftest import make_upstream_repo as _make_upstream_repo
from conftest import run_git
from sadana import config, plugins
from sadana.env_file import env_path
from sadana.subcommands.plugin import (
    build_plugin_parser,
    cmd_plugin_install,
    cmd_plugin_register,
    cmd_plugin_set,
    cmd_plugin_settings,
)


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


# ── plugin settings / plugin set ─────────────────────────────────────────


def _clear(monkeypatch: pytest.MonkeyPatch, var: str) -> None:
    """Unset `var` in a way monkeypatch will restore.

    A bare `delenv(..., raising=False)` on an absent variable records
    nothing, so anything that sets it afterwards — `config.load_dotenv()`
    writes `os.environ` directly — outlives the test. Setting it first is
    what gives monkeypatch something to put back.
    """
    monkeypatch.setenv(var, "placeholder")
    monkeypatch.delenv(var)


def _install_plugin_declaring(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, settings_toml: str) -> Path:
    """A plugin already sitting in the installed-plugins root. These tests
    are about what a person can see and store, not about how the plugin got
    there, so they place it rather than cloning it."""
    _clear(monkeypatch, "SADANA_PLUGIN__WEATHER__API_KEY")
    _clear(monkeypatch, "SADANA_PLUGIN__WEATHER__UNITS")
    plugins_root = tmp_path / "plugins"
    plugin_dir = plugins_root / "weather"
    plugin_dir.mkdir(parents=True)
    (plugin_dir / "plugin.toml").write_text(
        '[plugin]\nname = "weather"\nversion = "0.1.0"\ndescription = "d"\n' + settings_toml
    )
    monkeypatch.setenv("SADANA_PLUGINS_DIR", str(plugins_root))
    return plugin_dir


_TWO_SETTINGS = (
    '\n[[setting]]\nname = "api_key"\npurpose = "Account key"\nsecret = true\n'
    '\n[[setting]]\nname = "units"\npurpose = "metric or imperial"\nsecret = false\n'
)


@pytest.mark.unit
def test_plugin_settings_lists_what_is_needed_and_what_is_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _install_plugin_declaring(tmp_path, monkeypatch, _TWO_SETTINGS)
    monkeypatch.setenv("SADANA_PLUGIN__WEATHER__UNITS", "metric")

    assert cmd_plugin_settings(argparse.Namespace(name="weather")) == 0

    out = capsys.readouterr().out
    assert "api_key" in out
    assert "NOT SET" in out
    assert "units" in out
    assert "Account key" in out


@pytest.mark.unit
def test_plugin_settings_never_prints_a_stored_value(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _install_plugin_declaring(tmp_path, monkeypatch, _TWO_SETTINGS)
    monkeypatch.setenv("SADANA_PLUGIN__WEATHER__API_KEY", "the-value-nobody-may-see")

    cmd_plugin_settings(argparse.Namespace(name="weather"))

    assert "the-value-nobody-may-see" not in capsys.readouterr().out


@pytest.mark.unit
def test_plugin_settings_says_so_when_a_plugin_needs_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _install_plugin_declaring(tmp_path, monkeypatch, "")
    assert cmd_plugin_settings(argparse.Namespace(name="weather")) == 0
    assert "no settings" in capsys.readouterr().out


@pytest.mark.unit
def test_plugin_settings_fails_for_a_plugin_that_is_not_installed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("SADANA_PLUGINS_DIR", str(tmp_path / "plugins"))
    assert cmd_plugin_settings(argparse.Namespace(name="weather")) == 1
    assert "not installed" in capsys.readouterr().err


@pytest.mark.unit
def test_plugin_set_writes_the_value_to_the_env_file_and_nowhere_near_the_plugin(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    plugin_dir = _install_plugin_declaring(tmp_path, monkeypatch, _TWO_SETTINGS)
    before = sorted(p.name for p in plugin_dir.iterdir())

    exit_code = cmd_plugin_set(argparse.Namespace(name="weather", setting="api_key", value="stored-value"))

    assert exit_code == 0
    assignment = 'SADANA_PLUGIN__WEATHER__API_KEY="stored-value"'  # pragma: allowlist secret
    assert assignment in env_path().read_text(encoding="utf-8")
    assert sorted(p.name for p in plugin_dir.iterdir()) == before


@pytest.mark.unit
def test_a_value_stored_by_plugin_set_is_what_read_setting_finds_after_load_dotenv(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The round trip that matters: the writer and the reader agree, through
    the real .env format, with nothing in between."""
    _install_plugin_declaring(tmp_path, monkeypatch, _TWO_SETTINGS)
    cmd_plugin_set(argparse.Namespace(name="weather", setting="api_key", value="stored-value"))

    config.load_dotenv()

    assert plugins.read_setting("weather", "api_key") == "stored-value"


@pytest.mark.unit
def test_plugin_set_refuses_a_setting_the_plugin_does_not_declare(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _install_plugin_declaring(tmp_path, monkeypatch, _TWO_SETTINGS)

    exit_code = cmd_plugin_set(argparse.Namespace(name="weather", setting="nope", value="x"))

    assert exit_code == 1
    assert "declares no setting" in capsys.readouterr().err
    assert not env_path().exists()


@pytest.mark.unit
def test_plugin_set_refuses_an_empty_value(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """An empty value reads as missing to `read_setting`, so storing one
    would produce a plugin that looks configured and is not."""
    _install_plugin_declaring(tmp_path, monkeypatch, _TWO_SETTINGS)

    exit_code = cmd_plugin_set(argparse.Namespace(name="weather", setting="api_key", value=""))

    assert exit_code == 1
    assert "empty" in capsys.readouterr().err
    assert not env_path().exists()


@pytest.mark.unit
def test_plugin_set_reads_the_value_from_stdin_when_there_is_no_terminal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The scripted path must not force the credential into argv, where it is
    world-readable under /proc and lands in shell history."""
    _install_plugin_declaring(tmp_path, monkeypatch, _TWO_SETTINGS)
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    monkeypatch.setattr("sys.stdin", io.StringIO("piped-in\n"))

    assert cmd_plugin_set(argparse.Namespace(name="weather", setting="api_key", value=None)) == 0

    assignment = 'SADANA_PLUGIN__WEATHER__API_KEY="piped-in"'  # pragma: allowlist secret
    assert assignment in env_path().read_text(encoding="utf-8")


@pytest.mark.unit
def test_plugin_set_with_nothing_on_stdin_stores_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _install_plugin_declaring(tmp_path, monkeypatch, _TWO_SETTINGS)
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    monkeypatch.setattr("sys.stdin", io.StringIO(""))

    assert cmd_plugin_set(argparse.Namespace(name="weather", setting="api_key", value=None)) == 1
    assert "empty" in capsys.readouterr().err
    assert not env_path().exists()


@pytest.mark.unit
def test_plugin_set_fails_for_a_plugin_that_is_not_installed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("SADANA_PLUGINS_DIR", str(tmp_path / "plugins"))
    assert cmd_plugin_set(argparse.Namespace(name="weather", setting="api_key", value="x")) == 1
    assert "not installed" in capsys.readouterr().err
    assert not env_path().exists()


@pytest.mark.unit
def test_plugin_set_prompts_without_echo_for_a_secret(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _install_plugin_declaring(tmp_path, monkeypatch, _TWO_SETTINGS)
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("builtins.input", _refuse_echoing_prompt)
    monkeypatch.setattr("getpass.getpass", lambda _prompt: "typed-in-the-dark")

    assert cmd_plugin_set(argparse.Namespace(name="weather", setting="api_key", value=None)) == 0
    assert plugins.read_setting("weather", "api_key") is None  # not exported, only written
    assignment = 'SADANA_PLUGIN__WEATHER__API_KEY="typed-in-the-dark"'  # pragma: allowlist secret
    assert assignment in env_path().read_text(encoding="utf-8")


def _refuse_echoing_prompt(_prompt: str) -> str:
    raise AssertionError("a secret must be prompted for with getpass, never input()")


@pytest.mark.unit
def test_plugin_install_tells_you_what_the_new_plugin_needs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = _make_upstream_repo(tmp_path, plugin_name="weather")
    (repo / "plugin.toml").write_text(
        '[plugin]\nname = "weather"\nversion = "v1.0.0"\ndescription = "a test plugin"\n' + _TWO_SETTINGS
    )
    run_git(["commit", "-q", "-a", "-m", "settings"], cwd=repo)
    run_git(["tag", "-f", "v1.0.0"], cwd=repo)
    monkeypatch.setenv("SADANA_PLUGINS_DIR", str(tmp_path / "plugins"))

    cmd_plugin_register(argparse.Namespace(name="weather", repo_url=str(repo)))
    assert cmd_plugin_install(argparse.Namespace(name="weather", tag="v1.0.0", replace=False)) == 0

    out = capsys.readouterr().out
    assert "api_key" in out
    assert "NOT SET" in out
