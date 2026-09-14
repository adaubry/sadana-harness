"""Every `state` column holds a word from its table's written closed set.

H16's P1 acceptance criterion: "Every resource carries … a state from a written
closed set." The sets are declared one per table, beside the table
(`conversation_store.CONVERSATION_STATES` and friends); this file is the half
that makes them binding rather than decorative.

A contract between two pieces of data, not a snapshot: it asserts that what the
code *writes* stays inside what the code *declares*. Adding a state word is a
work item — declaring it in one place and writing it in another — and
forgetting either half fails here.
"""

from __future__ import annotations

import sqlite3
from dataclasses import replace
from pathlib import Path

import pytest

from conftest import conversation as build_conversation
from conftest import dag_result, turn_result, write_character
from sadana import (
    conversation_store,
    ledger,
    memory_store,
    observability,
    persona_store,
    plugin_install,
    plugins,
    stores,
)
from sadana.conversation import Message, TurnKey

#: table -> the module constant that closes its `state` column.
_CLOSED_SETS = {
    "conversations": conversation_store.CONVERSATION_STATES,
    "messages": conversation_store.MESSAGE_STATES,
    "memory_entries": memory_store.ENTRY_STATES,
    "agents": persona_store.AGENT_STATES,
    "plugin_state": plugin_install.PLUGIN_STATES,
    "turn_runs": observability.TURN_RUN_STATES,
    "plugin_runs": observability.PLUGIN_RUN_STATES,
    "artifacts": observability.ARTIFACT_STATES,
    "approvals": conversation_store.APPROVAL_STATES,
}


def _exercise_every_write_site(conn: sqlite3.Connection, tmp_path: Path) -> None:
    """Drive a real write through every table that owns a `state` column, so
    the assertions below run against words a write site actually produced —
    against an empty database they would pass trivially."""
    convo = build_conversation(key="k1", messages=(Message(role="user", content="hi"),))
    conversation_store.create(conn, convo, now=0.0, account_key="local", agent=None)  # pragma: allowlist secret
    conversation_store.save_pause(conn, conversation_key="k1", plugin="p", entry="e", node="n", trace=(), artifacts=())
    conversation_store.delete_pause(conn, conversation_key="k1")

    memory_store.write_entry(conn, "local", "kept_fact", "a", now=1.0)
    memory_store.write_entry(conn, "local", "gone_fact", "b", now=1.0)
    memory_store.forget_entry(conn, "local", "gone_fact", now=2.0)

    characters_dir = tmp_path / "characters"
    write_character("reviewer", characters_dir=characters_dir)
    persona_store.reconcile_agents(conn, characters_dir)

    plugins_root = tmp_path / "plugins"
    (plugins_root / "broken").mkdir(parents=True)
    (plugins_root / "broken" / "plugin.toml").write_text("this is not toml [[[")
    plugin_install.reconcile_plugin_state(conn, plugins_root)

    observability._insert_turn_run(conn, turn_result(), duration_s=1.0, recorded_at=100.0)
    observability._insert_plugin_run(
        conn,
        TurnKey(conversation="k1", turn_seq=0),
        0,
        replace(dag_result(), artifacts=(plugins.Artifact(kind="link", name="a", ref="https://example.test/1"),)),
        duration_s=1.0,
        recorded_at=100.0,
    )


@pytest.mark.unit
def test_no_write_site_produces_a_state_outside_its_tables_closed_set(tmp_path: Path) -> None:
    conn = stores.Connections(tmp_path / "c.db").writer
    _exercise_every_write_site(conn, tmp_path)

    seen = {}
    for table, allowed in _CLOSED_SETS.items():
        words = {row[0] for row in conn.execute(f"SELECT DISTINCT state FROM {table}")}
        seen[table] = words
        assert words <= allowed, f"{table}.state holds {sorted(words - allowed)}, outside {sorted(allowed)}"

    assert all(seen.values()), f"no rows written for {[t for t, w in seen.items() if not w]}"


@pytest.mark.unit
def test_the_states_the_ledger_reports_come_from_the_same_closed_sets(tmp_path: Path) -> None:
    """The ledger carries the state word onward to whoever is mirroring, so a
    word that never reaches a table but does reach the feed would be just as
    wrong."""
    conn = stores.Connections(tmp_path / "c.db").writer
    _exercise_every_write_site(conn, tmp_path)

    every_word = set().union(*_CLOSED_SETS.values())
    reported = {c.state for c in ledger.changes_since(conn, 0, 500) if c.state is not None}

    assert reported, "no change carried a state at all"
    assert reported <= every_word, f"the ledger reports {sorted(reported - every_word)}"


@pytest.mark.unit
def test_every_declared_state_word_is_a_plain_lowercase_token() -> None:
    """A state word crosses the wire to a console and is branched on there. One
    with a space, a capital or punctuation in it is one somebody will
    mis-transcribe."""
    for table, allowed in _CLOSED_SETS.items():
        for word in allowed:
            assert word.replace("_", "").isalnum() and word.islower(), f"{table}: {word!r}"


@pytest.mark.unit
def test_a_tables_declared_default_is_one_of_its_own_words(tmp_path: Path) -> None:
    """The DDL default and the closed set are written in two places and must
    agree — a default outside the set would put every legacy row in a state
    nothing downstream handles."""
    conn = stores.Connections(tmp_path / "c.db").writer

    for table, allowed in _CLOSED_SETS.items():
        (default,) = [
            row["dflt_value"].strip("'")
            for row in conn.execute(f"PRAGMA table_info({table})")
            if row["name"] == "state"
        ]
        assert default in allowed, f"{table}.state defaults to {default!r}, outside {sorted(allowed)}"
