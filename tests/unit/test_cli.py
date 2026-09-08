"""Tests for sadana.cli: build_parser(), main()."""

from __future__ import annotations

import pytest

from sadana import __version__
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
