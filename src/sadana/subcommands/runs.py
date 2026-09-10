"""The `sadana runs` subcommand.

OBSERVABILITY-01 of the OBSERVABILITY block
(`docs/tasks/OBSERVABILITY-01-turn-and-plugin-run-records/spec.md`).
Requirement 3's readback: a way to see a conversation's recorded turn and
plugin-dispatch numbers without opening `conversations.sqlite3` by hand.
Mirrors `subcommands/conversations.py`'s own shape — one file owning both
its parser and its handler.
"""

from __future__ import annotations

import argparse
import sqlite3
from contextlib import closing

from sadana import conversation_store, observability


def build_runs_parser(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    parser = subparsers.add_parser("runs", help="show a conversation's recorded turn and plugin-run numbers")
    parser.add_argument("conversation_key", help="the conversation to show recorded runs for")
    parser.set_defaults(func=cmd_runs)


def format_turn_run_line(row: sqlite3.Row) -> str:
    return (
        f"turn {row['turn_seq']}: {row['duration_s']:.3f}s, {row['model_calls']} model call(s), "
        f"{row['prompt_tokens']}+{row['completion_tokens']} tokens, {row['exit_reason']}"
    )


def format_plugin_run_line(row: sqlite3.Row) -> str:
    outcome = row["failed_node"] or "ok"
    return (
        f"turn {row['turn_seq']}.{row['seq_in_turn']}: {row['plugin']}/{row['entry']}, "
        f"{row['duration_s']:.3f}s, {row['node_count']} node(s), {outcome}"
    )


def cmd_runs(args: argparse.Namespace) -> int:
    path = conversation_store.store_path_from_config()
    with closing(conversation_store.open_store(path)) as conn:
        observability.make_recorder(conn)  # ensures turn_runs/plugin_runs exist even for a never-recorded key
        turn_rows = conn.execute(
            "SELECT * FROM turn_runs WHERE conversation_key = ? ORDER BY turn_seq", (args.conversation_key,)
        ).fetchall()
        plugin_rows = conn.execute(
            "SELECT * FROM plugin_runs WHERE conversation_key = ? ORDER BY turn_seq, seq_in_turn",
            (args.conversation_key,),
        ).fetchall()
    if not turn_rows and not plugin_rows:
        print("No recorded runs.")
        return 0
    for row in turn_rows:
        print(format_turn_run_line(row))
    for row in plugin_rows:
        print(format_plugin_run_line(row))
    return 0
