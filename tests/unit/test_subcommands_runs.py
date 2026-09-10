"""Tests for sadana.subcommands.runs."""

from __future__ import annotations

import argparse
import asyncio

import pytest

from conftest import dag_result as _dag_result
from conftest import turn_result as _turn_result
from sadana import observability
from sadana.conversation import TurnKey
from sadana.conversation_store import open_store, store_path_from_config
from sadana.subcommands.runs import build_runs_parser, cmd_runs, format_plugin_run_line, format_turn_run_line


def _record_one_turn(*, conversation: str = "c1", turn_seq: int = 0) -> None:
    conn = open_store(store_path_from_config())
    try:
        recorder = observability.make_recorder(conn)
        asyncio.run(recorder.record_turn(_turn_result(conversation=conversation, turn_seq=turn_seq), 1.5))
    finally:
        conn.close()


def _record_one_plugin_run(*, conversation: str = "c1", turn_seq: int = 0, seq_in_turn: int = 0) -> None:
    conn = open_store(store_path_from_config())
    try:
        recorder = observability.make_recorder(conn)
        turn_key = TurnKey(conversation=conversation, turn_seq=turn_seq)
        asyncio.run(recorder.record_plugin_run(turn_key, seq_in_turn, _dag_result(), 0.25))
    finally:
        conn.close()


@pytest.mark.unit
def test_format_turn_run_line_includes_duration_calls_tokens_and_exit_reason() -> None:
    conn = open_store(store_path_from_config())
    try:
        observability.make_recorder(conn)
        conn.execute(
            "INSERT INTO turn_runs (conversation_key, turn_seq, duration_s, model_calls, prompt_tokens, "
            "completion_tokens, exit_reason, recorded_at) VALUES ('c1', 0, 1.5, 2, 10, 5, 'completed', 0.0)"
        )
        row = conn.execute("SELECT * FROM turn_runs").fetchone()
    finally:
        conn.close()
    line = format_turn_run_line(row)
    assert "turn 0" in line
    assert "1.500s" in line
    assert "2 model call" in line
    assert "10+5 tokens" in line
    assert "completed" in line


@pytest.mark.unit
def test_format_plugin_run_line_shows_ok_or_the_failed_node() -> None:
    conn = open_store(store_path_from_config())
    try:
        observability.make_recorder(conn)
        conn.execute(
            "INSERT INTO plugin_runs (conversation_key, turn_seq, seq_in_turn, plugin, entry, duration_s, "
            "node_count, failed_node, recorded_at) VALUES ('c1', 0, 0, 'p1', 'do_it', 0.25, 3, NULL, 0.0)"
        )
        conn.execute(
            "INSERT INTO plugin_runs (conversation_key, turn_seq, seq_in_turn, plugin, entry, duration_s, "
            "node_count, failed_node, recorded_at) VALUES ('c1', 0, 1, 'p1', 'do_it', 0.1, 1, 'call_step', 0.0)"
        )
        ok_row, failed_row = conn.execute("SELECT * FROM plugin_runs ORDER BY seq_in_turn").fetchall()
    finally:
        conn.close()
    assert "ok" in format_plugin_run_line(ok_row)
    assert "call_step" in format_plugin_run_line(failed_row)


@pytest.mark.unit
def test_cmd_runs_prints_recorded_turn_and_plugin_rows(capsys: pytest.CaptureFixture[str]) -> None:
    _record_one_turn(conversation="c1", turn_seq=0)
    _record_one_plugin_run(conversation="c1", turn_seq=0, seq_in_turn=0)

    assert cmd_runs(argparse.Namespace(conversation_key="c1")) == 0

    out = capsys.readouterr().out
    assert "turn 0" in out
    assert "p1/do_it" in out


@pytest.mark.unit
def test_cmd_runs_unknown_key_prints_a_clear_message_not_an_error(capsys: pytest.CaptureFixture[str]) -> None:
    assert cmd_runs(argparse.Namespace(conversation_key="does-not-exist")) == 0
    assert capsys.readouterr().out.strip() != ""


@pytest.mark.unit
def test_cmd_runs_only_shows_rows_for_the_given_conversation(capsys: pytest.CaptureFixture[str]) -> None:
    _record_one_turn(conversation="alpha", turn_seq=0)
    _record_one_turn(conversation="beta", turn_seq=0)

    assert cmd_runs(argparse.Namespace(conversation_key="alpha")) == 0

    out = capsys.readouterr().out
    assert out.count("turn 0") == 1  # beta's own "turn 0" row must not leak into alpha's output


@pytest.mark.unit
def test_build_runs_parser_registers_runs_with_a_required_conversation_key() -> None:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    build_runs_parser(subparsers)

    args = parser.parse_args(["runs", "c1"])
    assert args.conversation_key == "c1"
    assert args.func is cmd_runs

    with pytest.raises(SystemExit):
        parser.parse_args(["runs"])
