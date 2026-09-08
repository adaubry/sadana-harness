"""Tests for sadana.cli: build_parser(), main()."""

from __future__ import annotations

from pathlib import Path

import pytest

from sadana import __version__
from sadana import config as sadana_config
from sadana.cli import build_parser, main


@pytest.mark.unit
def test_version_flag_exits_zero_and_prints_version(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc:
        main(["--version"])
    assert exc.value.code == 0
    assert __version__ in capsys.readouterr().out


@pytest.mark.unit
@pytest.mark.parametrize("flag", ["--help", "-h"])
def test_help_flag_exits_zero_and_prints_help(flag: str, capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc:
        main([flag])
    assert exc.value.code == 0
    assert capsys.readouterr().out


@pytest.mark.unit
def test_no_args_exits_two_and_prints_to_stderr(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc:
        main([])
    assert exc.value.code == 2
    assert capsys.readouterr().err


@pytest.mark.unit
def test_unrecognized_command_exits_two(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc:
        main(["nonsense-command"])
    assert exc.value.code == 2
    assert capsys.readouterr().err


@pytest.mark.unit
def test_none_argv_reads_sys_argv(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("sys.argv", ["sadana", "--version"])
    with pytest.raises(SystemExit) as exc:
        main()
    assert exc.value.code == 0


@pytest.mark.unit
def test_build_parser_returns_a_fresh_instance_each_call() -> None:
    assert build_parser() is not build_parser()


@pytest.mark.unit
def test_no_args_names_conversations_as_a_valid_choice(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit):
        main([])
    assert "conversations" in capsys.readouterr().err


@pytest.mark.unit
def test_conversations_subcommand_reachable_via_main() -> None:
    assert main(["conversations"]) == 0


@pytest.mark.unit
def test_setup_subcommand_reachable_via_main(capsys: pytest.CaptureFixture[str]) -> None:
    # no TTY + nothing in env → reports missing and exits 1; reaching that
    # return proves the handler ran (dispatch reached cmd_setup).
    assert main(["setup"]) == 1
    assert "OPENROUTER_API_KEY" in capsys.readouterr().err


@pytest.mark.unit
def test_main_loads_dotenv_before_subcommand_dispatch(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    state = tmp_path / "state"
    state.mkdir()
    (state / ".env").write_text('SADANA_CLI_TEST_KEY="from-file"\n', encoding="utf-8")
    monkeypatch.setenv("SADANA_STATE_DIR", str(state))
    monkeypatch.delenv("SADANA_CLI_TEST_KEY", raising=False)
    # "conversations" runs no handler that reads config — reaching it proves
    # main() dispatched; load_dotenv ran and the value is now visible.
    assert main(["conversations"]) == 0
    assert sadana_config.env("SADANA_CLI_TEST_KEY", "") == "from-file"


@pytest.mark.unit
def test_main_load_dotenv_never_overrides_live_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    state = tmp_path / "state"
    state.mkdir()
    (state / ".env").write_text('SADANA_CLI_TEST_KEY="from-file"\n', encoding="utf-8")
    monkeypatch.setenv("SADANA_STATE_DIR", str(state))
    monkeypatch.setenv("SADANA_CLI_TEST_KEY", "from-shell")
    assert main(["conversations"]) == 0
    assert sadana_config.env("SADANA_CLI_TEST_KEY", "") == "from-shell"
