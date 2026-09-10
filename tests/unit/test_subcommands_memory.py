"""Tests for sadana.subcommands.memory."""

from __future__ import annotations

import argparse

import pytest

from sadana.conversation_store import open_store, store_path_from_config
from sadana.memory_store import ensure_schema, get_rubric_override, write_entry
from sadana.subcommands.memory import (
    build_memory_parser,
    cmd_memory_forget,
    cmd_memory_list,
    cmd_memory_set_rubric,
)


def _write(account: str, entry_key: str, content: str) -> None:
    conn = open_store(store_path_from_config())
    try:
        ensure_schema(conn)
        write_entry(conn, account, entry_key, content, now=0.0)
    finally:
        conn.close()


@pytest.mark.unit
def test_cmd_memory_list_prints_every_entry(capsys: pytest.CaptureFixture[str]) -> None:
    _write("a1", "dog_name", "Buddy")
    _write("a1", "tz", "UTC+2")

    assert cmd_memory_list(argparse.Namespace(account="a1")) == 0

    out = capsys.readouterr().out
    assert "dog_name" in out and "Buddy" in out
    assert "tz" in out and "UTC+2" in out


@pytest.mark.unit
def test_cmd_memory_list_nothing_stored_prints_a_clear_message(capsys: pytest.CaptureFixture[str]) -> None:
    assert cmd_memory_list(argparse.Namespace(account="empty-account")) == 0
    assert capsys.readouterr().out.strip() != ""


@pytest.mark.unit
def test_cmd_memory_list_only_shows_the_given_account(capsys: pytest.CaptureFixture[str]) -> None:
    _write("a1", "dog_name", "Buddy")
    _write("a2", "dog_name", "Rex")

    assert cmd_memory_list(argparse.Namespace(account="a1")) == 0

    out = capsys.readouterr().out
    assert "Buddy" in out
    assert "Rex" not in out


@pytest.mark.unit
def test_cmd_memory_forget_removes_an_entry(capsys: pytest.CaptureFixture[str]) -> None:
    _write("a1", "dog_name", "Buddy")
    _write("a1", "tz", "UTC+2")

    assert cmd_memory_forget(argparse.Namespace(account="a1", entry_key="dog_name")) == 0
    capsys.readouterr()

    assert cmd_memory_list(argparse.Namespace(account="a1")) == 0
    out = capsys.readouterr().out
    assert "Buddy" not in out
    assert "UTC+2" in out


@pytest.mark.unit
def test_cmd_memory_set_rubric_persists(capsys: pytest.CaptureFixture[str]) -> None:
    assert cmd_memory_set_rubric(argparse.Namespace(account="a1", text="also remember their timezone")) == 0
    capsys.readouterr()

    conn = open_store(store_path_from_config())
    try:
        ensure_schema(conn)
        assert get_rubric_override(conn, "a1") == "also remember their timezone"
    finally:
        conn.close()


@pytest.mark.unit
def test_build_memory_parser_registers_list_forget_and_set_rubric() -> None:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    build_memory_parser(subparsers)

    list_args = parser.parse_args(["memory", "list", "a1"])
    assert list_args.account == "a1"
    assert list_args.func is cmd_memory_list

    forget_args = parser.parse_args(["memory", "forget", "a1", "dog_name"])
    assert forget_args.entry_key == "dog_name"
    assert forget_args.func is cmd_memory_forget

    rubric_args = parser.parse_args(["memory", "set-rubric", "a1", "some text"])
    assert rubric_args.text == "some text"
    assert rubric_args.func is cmd_memory_set_rubric

    with pytest.raises(SystemExit):
        parser.parse_args(["memory"])
