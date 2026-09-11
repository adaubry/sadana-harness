"""Tests for sadana.subcommands.persona."""

from __future__ import annotations

import argparse
from pathlib import Path

import pytest

from conftest import open_conn, write_character
from sadana import persona, persona_store
from sadana.subcommands.persona import (
    cmd_persona_list,
    cmd_persona_new,
    cmd_persona_show,
    cmd_persona_use,
)


def _write(name: str, description: str = "how it sounds when I'm working") -> Path:
    return write_character(name, description=description, tone="dry")


def _selection(account: str) -> str | None:
    conn = open_conn()
    try:
        persona_store.ensure_schema(conn)
        return persona_store.get_selection(conn, account)
    finally:
        conn.close()


@pytest.mark.unit
def test_list_shows_a_character_dropped_in_by_hand(capsys: pytest.CaptureFixture[str]) -> None:
    _write("working")
    assert cmd_persona_list(argparse.Namespace(account=None)) == 0
    out = capsys.readouterr().out
    assert "working" in out
    assert "how it sounds when I'm working" in out


@pytest.mark.unit
def test_list_with_no_characters_says_where_they_go(capsys: pytest.CaptureFixture[str]) -> None:
    assert cmd_persona_list(argparse.Namespace(account=None)) == 0
    out = capsys.readouterr().out
    assert str(persona_store.characters_dir_from_config()) in out
    assert "persona new" in out


@pytest.mark.unit
def test_list_marks_the_account_selection(capsys: pytest.CaptureFixture[str]) -> None:
    _write("working")
    _write("terse", description="clipped, one line")
    assert cmd_persona_use(argparse.Namespace(account="a1", name="terse")) == 0
    capsys.readouterr()

    assert cmd_persona_list(argparse.Namespace(account="a1")) == 0
    marked = [line for line in capsys.readouterr().out.splitlines() if line.startswith("*")]
    assert len(marked) == 1
    assert marked[0].startswith("* terse:")


@pytest.mark.unit
def test_list_says_when_the_selected_character_is_gone(capsys: pytest.CaptureFixture[str]) -> None:
    path = _write("working")
    assert cmd_persona_use(argparse.Namespace(account="a1", name="working")) == 0
    path.unlink()
    capsys.readouterr()

    assert cmd_persona_list(argparse.Namespace(account="a1")) == 0
    assert "missing" in capsys.readouterr().out


@pytest.mark.unit
def test_show_prints_the_rendered_voice(capsys: pytest.CaptureFixture[str]) -> None:
    _write("working")
    assert cmd_persona_show(argparse.Namespace(name="working")) == 0
    out = capsys.readouterr().out
    assert "You read the code first." in out
    assert "Tone: dry" in out
    assert "how it sounds when I'm working" not in out


@pytest.mark.unit
@pytest.mark.parametrize("name", ["absent", "../escape"])
def test_show_refuses_an_unknown_or_unsafe_name(name: str, capsys: pytest.CaptureFixture[str]) -> None:
    assert cmd_persona_show(argparse.Namespace(name=name)) == 1
    assert capsys.readouterr().err.startswith("error:")


@pytest.mark.unit
def test_use_selects_a_character() -> None:
    _write("working")
    assert cmd_persona_use(argparse.Namespace(account="a1", name="working")) == 0
    assert _selection("a1") == "working"


@pytest.mark.unit
def test_use_leaves_the_prior_selection_alone_when_the_name_is_unknown(capsys: pytest.CaptureFixture[str]) -> None:
    _write("working")
    assert cmd_persona_use(argparse.Namespace(account="a1", name="working")) == 0

    assert cmd_persona_use(argparse.Namespace(account="a1", name="nope")) == 1
    assert capsys.readouterr().err.startswith("error:")
    assert _selection("a1") == "working"


@pytest.mark.unit
def test_use_none_returns_the_account_to_the_neutral_voice() -> None:
    _write("working")
    assert cmd_persona_use(argparse.Namespace(account="a1", name="working")) == 0
    assert cmd_persona_use(argparse.Namespace(account="a1", name=persona.NEUTRAL_NAME)) == 0
    assert _selection("a1") is None


@pytest.mark.unit
def test_new_writes_a_template_and_prints_its_path(capsys: pytest.CaptureFixture[str]) -> None:
    assert cmd_persona_new(argparse.Namespace(name="fresh")) == 0
    printed = Path(capsys.readouterr().out.strip())
    assert printed.exists()
    assert persona_store.load_character(persona_store.characters_dir_from_config(), "fresh").name == "fresh"


@pytest.mark.unit
def test_new_refuses_to_overwrite_a_character_you_wrote(capsys: pytest.CaptureFixture[str]) -> None:
    path = _write("mine")
    before = path.read_text(encoding="utf-8")
    assert cmd_persona_new(argparse.Namespace(name="mine")) == 1
    assert capsys.readouterr().err.startswith("error:")
    assert path.read_text(encoding="utf-8") == before


@pytest.mark.unit
def test_new_reports_a_filesystem_failure_instead_of_a_traceback(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def _refuse(*_args: object, **_kwargs: object) -> Path:
        raise OSError("read-only file system")

    monkeypatch.setattr(persona_store, "write_template", _refuse)
    assert cmd_persona_new(argparse.Namespace(name="fresh")) == 1
    assert capsys.readouterr().err.startswith("error:")
