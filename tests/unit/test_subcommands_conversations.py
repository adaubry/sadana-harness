"""Tests for sadana.subcommands.conversations."""

from __future__ import annotations

import argparse

import pytest

from conftest import conversation as _conversation
from sadana.conversation import Conversation, Message
from sadana.conversation_store import create, open_store, store_path_from_config
from sadana.subcommands.conversations import (
    build_conversations_parser,
    cmd_conversations,
    format_conversation_line,
)


def _save(conversation: Conversation) -> None:
    conn = open_store(store_path_from_config())
    try:
        create(conn, conversation, now=0.0)
    finally:
        conn.close()


@pytest.mark.unit
def test_format_conversation_line_pluralizes_message_count() -> None:
    one = _conversation(key="k1", messages=(Message(role="user", content="hi"),))
    none = _conversation(key="k2", messages=())
    assert format_conversation_line(one) == "k1 (t1, 1 message)"
    assert format_conversation_line(none) == "k2 (t1, 0 messages)"


@pytest.mark.unit
def test_cmd_conversations_lists_everything_with_no_query(capsys: pytest.CaptureFixture[str]) -> None:
    _save(_conversation(key="alpha"))
    _save(_conversation(key="beta"))

    assert cmd_conversations(argparse.Namespace(query="")) == 0

    out = capsys.readouterr().out
    assert "alpha" in out
    assert "beta" in out


@pytest.mark.unit
def test_cmd_conversations_narrows_by_query(capsys: pytest.CaptureFixture[str]) -> None:
    _save(_conversation(key="debugging-session"))
    _save(_conversation(key="unrelated"))

    assert cmd_conversations(argparse.Namespace(query="debug")) == 0

    out = capsys.readouterr().out
    assert "debugging-session" in out
    assert "unrelated" not in out


@pytest.mark.unit
def test_cmd_conversations_empty_store_prints_a_clear_message(capsys: pytest.CaptureFixture[str]) -> None:
    assert cmd_conversations(argparse.Namespace(query="")) == 0
    assert capsys.readouterr().out.strip() != ""


@pytest.mark.unit
def test_cmd_conversations_does_not_modify_the_store() -> None:
    _save(_conversation(key="alpha"))
    cmd_conversations(argparse.Namespace(query=""))

    conn = open_store(store_path_from_config())
    try:
        count = conn.execute("SELECT COUNT(*) FROM conversations").fetchone()[0]
    finally:
        conn.close()
    assert count == 1


@pytest.mark.unit
def test_build_conversations_parser_registers_conversations_with_optional_query() -> None:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    build_conversations_parser(subparsers)

    args = parser.parse_args(["conversations"])
    assert args.query == ""
    assert args.func is cmd_conversations

    args = parser.parse_args(["conversations", "foo"])
    assert args.query == "foo"
