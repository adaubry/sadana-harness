"""Tests for sadana.conversation_store: create(), save(), load(), bind_persist()."""

from __future__ import annotations

import asyncio
import sqlite3
from dataclasses import replace
from pathlib import Path

import pytest

from conftest import SYSTEM_PROMPT as _SYSTEM_PROMPT
from conftest import conversation as _conversation
from conftest import tool_call_response as _tool_call_response
from conftest import tool_spec as _spec
from sadana import conversation_store, ids, ledger, model_access, plugins
from sadana.context import ContextState
from sadana.conversation import (
    ExitReason,
    IterationBudget,
    Message,
    WallClockBudget,
    build_surface,
    run_turn,
    turn_prompt_hash,
)
from sadana.conversation_store import (
    ConversationAlreadyExists,
    ConversationNotFound,
    accounts_with_conversations,
    advance_scheduled_trigger,
    bind_persist,
    create,
    delete_pause,
    delete_scheduled_trigger,
    due_triggers,
    load,
    load_pause,
    open_store,
    save,
    save_pause,
    search_conversations,
    store_path_from_config,
    upsert_scheduled_trigger,
    write_txn,
)


def _messages() -> tuple[Message, ...]:
    return (
        Message(role="user", content="hi"),
        Message(role="assistant", tool_calls=({"id": "call_1", "name": "noop", "arguments": {}},)),
        Message(role="tool", content="ok", tool_call_id="call_1"),
    )


# ── store_path_from_config ──────────────────────────────────────────────


@pytest.mark.unit
def test_store_path_from_config_defaults_under_state_dir(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SADANA_CONVERSATION_STORE_PATH", raising=False)
    from sadana import config

    assert store_path_from_config() == config.get_paths().state_dir / "conversations.sqlite3"


@pytest.mark.unit
def test_store_path_from_config_honors_override(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    override = tmp_path / "somewhere" / "convos.db"
    monkeypatch.setenv("SADANA_CONVERSATION_STORE_PATH", str(override))
    assert store_path_from_config() == override


# ── open_store / write_txn ──────────────────────────────────────────────


@pytest.mark.unit
def test_open_store_creates_file_and_both_tables(tmp_path: Path) -> None:
    path = tmp_path / "nested" / "c.db"
    conn = open_store(path)
    try:
        assert path.exists()
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        assert {"conversations", "messages"} <= tables
    finally:
        conn.close()


@pytest.mark.unit
def test_write_txn_commits_on_success(tmp_path: Path) -> None:
    conn = open_store(tmp_path / "c.db")
    with write_txn(conn) as c:
        c.execute(
            "INSERT INTO conversations (key, template_name, system_prompt, prompt_sha256, "
            "prompt_epoch, tool_surface_json, next_turn_seq, iteration_max_total, "
            "iteration_used, wall_clock_remaining_s, next_child_seq) "
            "VALUES ('x', 't', 's', 'h', 0, '[]', 0, 10, 0, NULL, 0)"
        )
    assert conn.execute("SELECT COUNT(*) FROM conversations").fetchone()[0] == 1


@pytest.mark.unit
def test_write_txn_rolls_back_on_failure(tmp_path: Path) -> None:
    conn = open_store(tmp_path / "c.db")
    with pytest.raises(RuntimeError), write_txn(conn) as c:
        c.execute(
            "INSERT INTO conversations (key, template_name, system_prompt, prompt_sha256, "
            "prompt_epoch, tool_surface_json, next_turn_seq, iteration_max_total, "
            "iteration_used, wall_clock_remaining_s, next_child_seq) "
            "VALUES ('x', 't', 's', 'h', 0, '[]', 0, 10, 0, NULL, 0)"
        )
        raise RuntimeError("boom")
    assert conn.execute("SELECT COUNT(*) FROM conversations").fetchone()[0] == 0


# ── create / save / load round trip ─────────────────────────────────────


@pytest.mark.unit
def test_create_then_load_round_trips_every_field(tmp_path: Path) -> None:
    conn = open_store(tmp_path / "c.db")
    conv = _conversation(messages=_messages(), next_turn_seq=1)

    create(conn, conv, now=100.0, account_key="a1")  # pragma: allowlist secret
    loaded = load(conn, conv.key, now=100.0)

    assert loaded == conv


@pytest.mark.unit
def test_create_duplicate_key_raises_and_leaves_original_unchanged(tmp_path: Path) -> None:
    conn = open_store(tmp_path / "c.db")
    original = _conversation(messages=_messages())
    create(conn, original, now=0.0, account_key="a1")  # pragma: allowlist secret

    colliding = _conversation(messages=(), next_turn_seq=99)
    with pytest.raises(ConversationAlreadyExists):
        create(conn, colliding, now=0.0, account_key="a1")  # pragma: allowlist secret

    assert load(conn, "k1", now=0.0) == original


@pytest.mark.unit
def test_save_upserts_repeatedly_on_owned_key(tmp_path: Path) -> None:
    conn = open_store(tmp_path / "c.db")
    conv = _conversation(messages=_messages()[:1])
    create(conn, conv, now=0.0, account_key="a1")  # pragma: allowlist secret

    grown = _conversation(messages=_messages(), next_turn_seq=1)
    save(conn, grown, now=0.0)
    save(conn, grown, now=0.0)  # a second save of the same progress is not an error

    assert load(conn, "k1", now=0.0) == grown


@pytest.mark.unit
def test_load_unknown_key_raises_conversation_not_found(tmp_path: Path) -> None:
    conn = open_store(tmp_path / "c.db")
    with pytest.raises(ConversationNotFound):
        load(conn, "nobody-ever-saved-this", now=0.0)


@pytest.mark.unit
def test_resaving_same_messages_does_not_duplicate_rows(tmp_path: Path) -> None:
    conn = open_store(tmp_path / "c.db")
    conv = _conversation(messages=_messages())
    create(conn, conv, now=0.0, account_key="a1")  # pragma: allowlist secret
    save(conn, conv, now=0.0)
    save(conn, conv, now=0.0)

    count = conn.execute("SELECT COUNT(*) FROM messages WHERE conversation_key = ?", (conv.key,)).fetchone()[0]
    assert count == len(_messages())


# ── context state / cache boundary survive a resume (C12) ──────────────


@pytest.mark.unit
def test_load_reconstructs_context_state_and_stable_prompt_len_across_a_second_connection(tmp_path: Path) -> None:
    path = tmp_path / "c.db"
    conv = _conversation()
    conv = replace(
        conv,
        stable_prompt_len=10,  # narrower than the full system prompt
        context_state=ContextState(total_prompt_tokens=123, total_completion_tokens=45),
    )

    conn = open_store(path)
    create(conn, conv, now=0.0, account_key="a1")  # pragma: allowlist secret
    conn.close()  # the failure mode this fix targets only shows up across a real reconnect

    reopened = open_store(path)
    loaded = load(reopened, conv.key, now=0.0)

    assert loaded == conv


@pytest.mark.unit
def test_load_falls_back_for_a_legacy_row_and_migration_is_idempotent(tmp_path: Path) -> None:
    path = tmp_path / "c.db"
    raw = sqlite3.connect(path)
    raw.execute(
        """CREATE TABLE conversations (
            key                    TEXT PRIMARY KEY,
            template_name          TEXT NOT NULL,
            system_prompt          TEXT NOT NULL,
            prompt_sha256          TEXT NOT NULL,
            prompt_epoch           INTEGER NOT NULL,
            tool_surface_json      TEXT NOT NULL,
            next_turn_seq          INTEGER NOT NULL,
            iteration_max_total    INTEGER NOT NULL,
            iteration_used         INTEGER NOT NULL,
            wall_clock_remaining_s REAL,
            next_child_seq         INTEGER NOT NULL
        )"""
    )
    raw.execute(
        "CREATE TABLE messages (conversation_key TEXT NOT NULL, msg_seq INTEGER NOT NULL, role TEXT NOT NULL, "
        "content TEXT, tool_calls_json TEXT NOT NULL, tool_call_id TEXT, "
        "PRIMARY KEY (conversation_key, msg_seq))"
    )
    raw.execute(
        "INSERT INTO conversations (key, template_name, system_prompt, prompt_sha256, prompt_epoch, "
        "tool_surface_json, next_turn_seq, iteration_max_total, iteration_used, wall_clock_remaining_s, "
        "next_child_seq) VALUES ('legacy', 't1', ?, 'h', 0, '[]', 0, 10, 0, NULL, 0)",
        (_SYSTEM_PROMPT,),
    )
    raw.commit()
    raw.close()

    first = open_store(path)  # must migrate: adds the three new columns
    first.close()
    second = open_store(path)  # must not raise: migration runs again, columns already present

    loaded = load(second, "legacy", now=0.0)

    assert loaded.context_state == ContextState()
    assert loaded.stable_prompt_len == len(_SYSTEM_PROMPT)


# ── wall-clock budget portability ───────────────────────────────────────


@pytest.mark.unit
def test_wall_clock_budget_persists_as_remaining_seconds(tmp_path: Path) -> None:
    conn = open_store(tmp_path / "c.db")
    conv = _conversation(wall_clock_budget=WallClockBudget(deadline=130.0))

    create(conn, conv, now=100.0, account_key="a1")  # 30s remaining at save time  # pragma: allowlist secret
    loaded = load(conn, conv.key, now=500.0)  # resumed 400s later, on a fresh clock

    assert loaded.wall_clock_budget == WallClockBudget(deadline=530.0)


@pytest.mark.unit
def test_no_wall_clock_budget_round_trips_as_none(tmp_path: Path) -> None:
    conn = open_store(tmp_path / "c.db")
    conv = _conversation(wall_clock_budget=None)
    create(conn, conv, now=0.0, account_key="a1")  # pragma: allowlist secret
    assert load(conn, conv.key, now=999.0).wall_clock_budget is None


# ── atomicity across the two tables ─────────────────────────────────────


@pytest.mark.unit
def test_create_forced_failure_leaves_no_partial_row(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    conn = open_store(tmp_path / "c.db")
    conv = _conversation(messages=_messages())

    def boom(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("disk full")

    monkeypatch.setattr(conversation_store, "_insert_messages", boom)
    with pytest.raises(RuntimeError):
        create(conn, conv, now=0.0, account_key="a1")  # pragma: allowlist secret

    assert conn.execute("SELECT COUNT(*) FROM conversations").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0] == 0


@pytest.mark.unit
def test_save_forced_failure_leaves_previous_snapshot_intact(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    conn = open_store(tmp_path / "c.db")
    first = _conversation(messages=_messages()[:1])
    create(conn, first, now=0.0, account_key="a1")  # pragma: allowlist secret

    grown = _conversation(messages=_messages(), next_turn_seq=1)

    def boom(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("disk full")

    monkeypatch.setattr(conversation_store, "_insert_messages", boom)
    with pytest.raises(RuntimeError):
        save(conn, grown, now=0.0)

    # the conversations row's own columns must not have moved either,
    # even though the (patched) failure happened after that UPDATE ran
    assert load(conn, "k1", now=0.0) == first


# ── bind_persist against a real failure ─────────────────────────────────


def _run_turn(surface, *, dispatch, persist):
    return asyncio.run(
        run_turn(
            conversation="c1",
            turn_seq=0,
            messages=(),
            user_input="hello",
            system_prompt=_SYSTEM_PROMPT,
            prompt_sha256=turn_prompt_hash(_SYSTEM_PROMPT, surface),
            tool_surface=surface,
            iteration_budget=IterationBudget(max_total=10),
            wall_clock_budget=None,
            now=0.0,
            provider="p",
            model="m",
            dispatch=dispatch,
            context_state=ContextState(),
            stable_prompt_len=len(_SYSTEM_PROMPT),
            persist=persist,
        )
    )


@pytest.mark.unit
def test_bind_persist_real_failure_aborts_turn_before_dispatch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    conn = open_store(tmp_path / "c.db")
    conv = _conversation()
    conn.close()  # a real, not simulated, failure: every write now raises

    surface = build_surface([_spec("noop", "noop")])
    monkeypatch.setattr(model_access, "send", lambda request: _tool_call_response("noop"))

    dispatch_called: list[str] = []

    async def dispatch_tracker(name: str, arguments: dict) -> plugins.DagResult:
        dispatch_called.append(name)
        return plugins.DagResult(plugin="test", entry="test", text="ok")

    persist = bind_persist(conn, conv, now=0.0)
    result, _messages, _budget, _prompt = _run_turn(surface, dispatch=dispatch_tracker, persist=persist)

    assert result.exit_reason == ExitReason.PERSISTENCE_FAILED
    assert dispatch_called == []


@pytest.mark.unit
def test_bind_persist_success_saves_growing_messages(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    conn = open_store(tmp_path / "c.db")
    conv = _conversation()
    create(conn, conv, now=0.0, account_key="a1")  # pragma: allowlist secret

    surface = build_surface([_spec("noop", "noop")])
    responses = iter(
        [
            _tool_call_response("noop"),
            model_access.Response(content="done", tool_calls=(), finish_reason="stop", usage=model_access.Usage()),
        ]
    )
    monkeypatch.setattr(model_access, "send", lambda request: next(responses))

    async def dispatch_ok(name: str, arguments: dict) -> plugins.DagResult:
        return plugins.DagResult(plugin="test", entry="test", text="ran", artifacts=(), trace=(), failed_node=None)

    persist = bind_persist(conn, conv, now=0.0)
    result, _messages, _budget, _prompt = _run_turn(surface, dispatch=dispatch_ok, persist=persist)

    assert result.exit_reason == ExitReason.COMPLETED
    saved = load(conn, conv.key, now=0.0)
    assert len(saved.messages) >= 1  # the mid-turn persist() call landed durably
    assert any(m.role == "assistant" and m.tool_calls for m in saved.messages)


# ── search_conversations ────────────────────────────────────────────────


@pytest.mark.unit
def test_search_empty_store_returns_empty_list(tmp_path: Path) -> None:
    conn = open_store(tmp_path / "c.db")
    assert search_conversations(conn, "", now=0.0) == []


@pytest.mark.unit
def test_search_empty_query_returns_every_conversation(tmp_path: Path) -> None:
    conn = open_store(tmp_path / "c.db")
    create(conn, _conversation(key="alpha"), now=0.0, account_key="a1")  # pragma: allowlist secret
    create(conn, _conversation(key="beta"), now=0.0, account_key="a1")  # pragma: allowlist secret

    found = search_conversations(conn, "", now=0.0)

    assert [c.key for c in found] == ["alpha", "beta"]  # ORDER BY key
    assert found[0] == load(conn, "alpha", now=0.0)
    assert found[1] == load(conn, "beta", now=0.0)


@pytest.mark.unit
def test_search_matches_key_substring_case_insensitively(tmp_path: Path) -> None:
    conn = open_store(tmp_path / "c.db")
    create(conn, _conversation(key="debugging-session"), now=0.0, account_key="a1")  # pragma: allowlist secret
    create(conn, _conversation(key="unrelated"), now=0.0, account_key="a1")  # pragma: allowlist secret

    found = search_conversations(conn, "DEBUG", now=0.0)

    assert [c.key for c in found] == ["debugging-session"]


@pytest.mark.unit
def test_search_matches_template_name_substring_case_insensitively(tmp_path: Path) -> None:
    conn = open_store(tmp_path / "c.db")
    create(
        conn,
        _conversation(key="k1", template_name="eval-harness"),
        now=0.0,
        account_key="a1",  # pragma: allowlist secret
    )
    create(conn, _conversation(key="k2", template_name="other"), now=0.0, account_key="a1")  # pragma: allowlist secret

    found = search_conversations(conn, "EVAL", now=0.0)

    assert [c.key for c in found] == ["k1"]


@pytest.mark.unit
def test_search_matches_message_content_substring(tmp_path: Path) -> None:
    conn = open_store(tmp_path / "c.db")
    create(
        conn,
        _conversation(key="k1", messages=(Message(role="user", content="find the needle here"),)),
        now=0.0,
        account_key="a1",  # pragma: allowlist secret
    )
    create(
        conn,
        _conversation(key="k2", messages=(Message(role="user", content="nothing to see"),)),
        now=0.0,
        account_key="a1",  # pragma: allowlist secret
    )

    found = search_conversations(conn, "NEEDLE", now=0.0)

    assert [c.key for c in found] == ["k1"]


@pytest.mark.unit
def test_search_deduplicates_conversation_matching_multiple_fields(tmp_path: Path) -> None:
    conn = open_store(tmp_path / "c.db")
    create(
        conn,
        _conversation(
            key="widget-session",
            template_name="widget-template",
            messages=(Message(role="user", content="about widgets"),),
        ),
        now=0.0,
        account_key="a1",  # pragma: allowlist secret
    )

    found = search_conversations(conn, "widget", now=0.0)

    assert [c.key for c in found] == ["widget-session"]


@pytest.mark.unit
def test_search_no_match_returns_empty_list(tmp_path: Path) -> None:
    conn = open_store(tmp_path / "c.db")
    create(conn, _conversation(key="k1"), now=0.0, account_key="a1")  # pragma: allowlist secret

    assert search_conversations(conn, "nothing-saved-matches-this", now=0.0) == []


@pytest.mark.unit
def test_search_escapes_percent_and_underscore_as_literals(tmp_path: Path) -> None:
    conn = open_store(tmp_path / "c.db")
    create(conn, _conversation(key="50%off"), now=0.0, account_key="a1")  # pragma: allowlist secret
    create(conn, _conversation(key="50Xoff"), now=0.0, account_key="a1")  # pragma: allowlist secret

    assert [c.key for c in search_conversations(conn, "50%off", now=0.0)] == ["50%off"]
    assert search_conversations(conn, "50_off", now=0.0) == []


# ── scheduled_triggers ────────────────────────────────────────────────────


@pytest.mark.unit
def test_due_triggers_returns_only_triggers_whose_time_has_arrived(tmp_path: Path) -> None:
    conn = open_store(tmp_path / "c.db")
    upsert_scheduled_trigger(conn, name="past", trigger_text="fire", next_run_at=100.0, interval_seconds=None)
    upsert_scheduled_trigger(conn, name="future", trigger_text="fire", next_run_at=200.0, interval_seconds=None)

    due = due_triggers(conn, now=150.0)

    assert [t.name for t in due] == ["past"]


@pytest.mark.unit
def test_upsert_scheduled_trigger_replaces_an_existing_row_by_name(tmp_path: Path) -> None:
    conn = open_store(tmp_path / "c.db")
    upsert_scheduled_trigger(conn, name="t", trigger_text="first", next_run_at=100.0, interval_seconds=None)
    upsert_scheduled_trigger(conn, name="t", trigger_text="second", next_run_at=200.0, interval_seconds=604800.0)

    (due,) = due_triggers(conn, now=1_000_000.0)
    assert due.trigger_text == "second"
    assert due.next_run_at == 200.0
    assert due.interval_seconds == 604800.0


@pytest.mark.unit
def test_advance_scheduled_trigger_moves_next_run_at(tmp_path: Path) -> None:
    conn = open_store(tmp_path / "c.db")
    upsert_scheduled_trigger(conn, name="t", trigger_text="fire", next_run_at=100.0, interval_seconds=604800.0)

    advance_scheduled_trigger(conn, name="t", next_run_at=999.0)

    assert due_triggers(conn, now=100.0) == ()
    (due,) = due_triggers(conn, now=1000.0)
    assert due.next_run_at == 999.0


@pytest.mark.unit
def test_delete_scheduled_trigger_removes_the_row(tmp_path: Path) -> None:
    conn = open_store(tmp_path / "c.db")
    upsert_scheduled_trigger(conn, name="t", trigger_text="fire", next_run_at=100.0, interval_seconds=None)

    delete_scheduled_trigger(conn, name="t")

    assert due_triggers(conn, now=1_000_000.0) == ()


@pytest.mark.unit
def test_delete_scheduled_trigger_on_an_unknown_name_is_a_silent_no_op(tmp_path: Path) -> None:
    conn = open_store(tmp_path / "c.db")
    delete_scheduled_trigger(conn, name="never-existed")  # must not raise


# ── plugin_pauses ─────────────────────────────────────────────────────────


def _trace() -> tuple[plugins.NodeTrace, ...]:
    return (plugins.NodeTrace(node="start", kind="call", visit=0, ok=True, port=None, detail=None),)


def _artifacts() -> tuple[plugins.Artifact, ...]:
    return (plugins.Artifact(kind="link", name="n", ref="https://example.test"),)


@pytest.mark.unit
def test_load_pause_returns_none_for_a_conversation_with_no_pause(tmp_path: Path) -> None:
    conn = open_store(tmp_path / "c.db")
    assert load_pause(conn, conversation_key="k1") is None


@pytest.mark.unit
def test_save_pause_then_load_pause_roundtrips(tmp_path: Path) -> None:
    conn = open_store(tmp_path / "c.db")
    save_pause(
        conn,
        conversation_key="k1",
        plugin="plugin-d",
        entry="plugin_d_entry",
        node="await_answer",
        trace=_trace(),
        artifacts=_artifacts(),
    )

    pause = load_pause(conn, conversation_key="k1")

    assert pause is not None
    assert pause.plugin == "plugin-d"
    assert pause.entry == "plugin_d_entry"
    assert pause.node == "await_answer"
    assert pause.trace == _trace()
    assert pause.artifacts == _artifacts()


@pytest.mark.unit
def test_save_pause_upserts_by_conversation_key(tmp_path: Path) -> None:
    conn = open_store(tmp_path / "c.db")
    save_pause(conn, conversation_key="k1", plugin="plugin-d", entry="e", node="first", trace=(), artifacts=())
    save_pause(conn, conversation_key="k1", plugin="plugin-d", entry="e", node="second", trace=_trace(), artifacts=())

    pause = load_pause(conn, conversation_key="k1")

    assert pause is not None
    assert pause.node == "second"


@pytest.mark.unit
def test_delete_pause_removes_the_row(tmp_path: Path) -> None:
    conn = open_store(tmp_path / "c.db")
    save_pause(conn, conversation_key="k1", plugin="p", entry="e", node="n", trace=(), artifacts=())

    delete_pause(conn, conversation_key="k1")

    assert load_pause(conn, conversation_key="k1") is None


@pytest.mark.unit
def test_delete_pause_on_an_unknown_key_is_a_silent_no_op(tmp_path: Path) -> None:
    conn = open_store(tmp_path / "c.db")
    delete_pause(conn, conversation_key="never-existed")  # must not raise


# ── PERSONA-02: whose conversation this is ───────────────────────────────


@pytest.mark.unit
def test_create_records_which_account_the_conversation_belongs_to(tmp_path: Path) -> None:
    conn = open_store(tmp_path / "c.db")
    create(conn, _conversation(key="k1"), now=0.0, account_key="adam")  # pragma: allowlist secret

    assert accounts_with_conversations(conn) == frozenset({"adam"})


@pytest.mark.unit
def test_accounts_with_conversations_is_one_entry_per_account_not_per_conversation(tmp_path: Path) -> None:
    conn = open_store(tmp_path / "c.db")
    create(conn, _conversation(key="k1"), now=0.0, account_key="adam")  # pragma: allowlist secret
    create(conn, _conversation(key="k2"), now=0.0, account_key="adam")  # pragma: allowlist secret
    create(conn, _conversation(key="k3"), now=0.0, account_key="webhook:99")  # pragma: allowlist secret

    assert accounts_with_conversations(conn) == frozenset({"adam", "webhook:99"})


@pytest.mark.unit
def test_a_conversation_recorded_before_this_item_still_loads_and_belongs_to_nobody(tmp_path: Path) -> None:
    """The legacy shape: a `conversations` row with no `conversation_accounts`
    row beside it. It must keep loading, and it must be absent from the
    listing rather than crashing it."""
    conn = open_store(tmp_path / "c.db")
    create(conn, _conversation(key="legacy"), now=0.0, account_key="adam")  # pragma: allowlist secret
    conn.execute("DELETE FROM conversation_accounts WHERE conversation_key = 'legacy'")
    conn.commit()

    assert load(conn, "legacy", now=0.0).key == "legacy"
    assert accounts_with_conversations(conn) == frozenset()


# ── a pause remembers which run it belonged to ───────────────────────────


@pytest.mark.unit
def test_a_pause_round_trips_the_run_it_belonged_to(tmp_path: Path) -> None:
    """So the resumed half writes into the same directory the paused half
    did — a run's output directory is keyed by turn and sequence, and a
    resume happens in a later process with nothing else to go on."""
    conn = open_store(tmp_path / "c.db")
    save_pause(
        conn,
        conversation_key="k1",
        plugin="plugin-d",
        entry="plugin_d_entry",
        node="await_answer",
        trace=(),
        artifacts=(),
        turn_seq=4,
        seq_in_turn=2,
    )

    pause = load_pause(conn, conversation_key="k1")

    assert pause is not None
    assert (pause.turn_seq, pause.seq_in_turn) == (4, 2)


@pytest.mark.unit
def test_a_store_whose_plugin_pauses_predates_these_columns_migrates_and_still_loads(tmp_path: Path) -> None:
    """The migration's actual safety claim, driven through the ALTER branch.

    Every store already on disk has a `plugin_pauses` without these two
    columns, and `load_pause`'s SELECT names them — so if the new DDL were
    wrong, the first `open_store` or the first resume against a real store
    would fail. Writing the pre-migration table by hand is the only way to
    execute that path: `open_store` on a fresh file creates the table from
    `_SCHEMA`, which already has both columns, so a row that merely omits
    them proves the NULL read and nothing about the migration.
    """
    path = tmp_path / "c.db"
    raw = sqlite3.connect(path)
    raw.execute(
        """CREATE TABLE plugin_pauses (
            conversation_key  TEXT PRIMARY KEY,
            plugin            TEXT NOT NULL,
            entry             TEXT NOT NULL,
            node              TEXT NOT NULL,
            trace_json        TEXT NOT NULL,
            artifacts_json    TEXT NOT NULL
        )"""
    )
    raw.execute(
        "INSERT INTO plugin_pauses (conversation_key, plugin, entry, node, trace_json, artifacts_json) "
        "VALUES ('k1', 'plugin-d', 'plugin_d_entry', 'await_answer', '[]', '[]')"
    )
    raw.commit()
    raw.close()

    conn = open_store(path)
    pause = load_pause(conn, conversation_key="k1")

    assert pause is not None
    assert pause.node == "await_answer"
    assert pause.turn_seq is None
    assert pause.seq_in_turn is None

    # Idempotent: a second open of the same file must not re-run the ALTER.
    reopened = open_store(path)
    assert load_pause(reopened, conversation_key="k1") is not None


# ── H16: identity, the ledger, and the legacy fill ─────────────────────────


_PRE_H16_CONVERSATIONS = """CREATE TABLE conversations (
    key                    TEXT PRIMARY KEY,
    template_name          TEXT NOT NULL,
    system_prompt          TEXT NOT NULL,
    prompt_sha256          TEXT NOT NULL,
    prompt_epoch           INTEGER NOT NULL,
    tool_surface_json      TEXT NOT NULL,
    next_turn_seq          INTEGER NOT NULL,
    iteration_max_total    INTEGER NOT NULL,
    iteration_used         INTEGER NOT NULL,
    wall_clock_remaining_s REAL,
    next_child_seq         INTEGER NOT NULL
)"""

_PRE_H16_MESSAGES = (
    "CREATE TABLE messages (conversation_key TEXT NOT NULL, msg_seq INTEGER NOT NULL, role TEXT NOT NULL, "
    "content TEXT, tool_calls_json TEXT NOT NULL, tool_call_id TEXT, "
    "PRIMARY KEY (conversation_key, msg_seq))"
)


def _pre_h16_store(path: Path, *, messages: int = 1) -> None:
    """A store as it was written before H16, by hand.

    The only way to exercise the migration: `open_store` would create the
    current `_SCHEMA`, which already has every column, so a row that merely
    omits them cannot be produced through the module's own API. Same technique
    the C12 migration test above already uses.
    """
    raw = sqlite3.connect(path)
    raw.execute(_PRE_H16_CONVERSATIONS)
    raw.execute(_PRE_H16_MESSAGES)
    raw.execute(
        "INSERT INTO conversations (key, template_name, system_prompt, prompt_sha256, prompt_epoch, "
        "tool_surface_json, next_turn_seq, iteration_max_total, iteration_used, wall_clock_remaining_s, "
        "next_child_seq) VALUES ('legacy', 't1', ?, 'h', 0, '[]', 0, 10, 0, NULL, 0)",
        (_SYSTEM_PROMPT,),
    )
    for seq in range(messages):
        raw.execute(
            "INSERT INTO messages (conversation_key, msg_seq, role, content, tool_calls_json, tool_call_id) "
            "VALUES ('legacy', ?, 'user', 'hi', '[]', NULL)",
            (seq,),
        )
    raw.commit()
    raw.close()


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}


@pytest.mark.unit
def test_a_fresh_store_carries_every_identity_column(tmp_path: Path) -> None:
    conn = open_store(tmp_path / "c.db")

    assert {"id", "created_at", "updated_at", "version", "state", "tags_json", "agent"} <= _columns(
        conn, "conversations"
    )
    assert {"id", "created_at", "state", "run_id"} <= _columns(conn, "messages")


@pytest.mark.unit
def test_a_pre_h16_store_gains_every_identity_column(tmp_path: Path) -> None:
    path = tmp_path / "c.db"
    _pre_h16_store(path)

    conn = open_store(path)

    assert {"id", "created_at", "updated_at", "version", "state", "tags_json", "agent"} <= _columns(
        conn, "conversations"
    )
    assert {"id", "created_at", "state", "run_id"} <= _columns(conn, "messages")


@pytest.mark.unit
def test_the_legacy_fill_gives_every_old_row_an_id_and_a_floor_timestamp(tmp_path: Path) -> None:
    path = tmp_path / "c.db"
    _pre_h16_store(path, messages=3)

    conn = open_store(path)

    row = conn.execute("SELECT id, created_at, updated_at, version, state FROM conversations").fetchone()
    assert ids.parse_id("conv", row["id"]) is not None
    assert row["created_at"] == row["updated_at"]
    assert row["version"] == 1
    assert row["state"] == "active"
    assert conn.execute("SELECT COUNT(*) FROM messages WHERE id IS NULL").fetchone()[0] == 0


@pytest.mark.unit
def test_the_legacy_fill_is_idempotent_and_writes_no_ledger_rows(tmp_path: Path) -> None:
    """A mirror's first sync reads the inventory, so filling a row that did
    not really change must not announce itself as news."""
    path = tmp_path / "c.db"
    _pre_h16_store(path, messages=3)

    first = open_store(path)
    filled = first.execute("SELECT id, created_at FROM conversations").fetchone()
    assert ledger.ledger_head(first) == 0
    first.close()

    second = open_store(path)

    assert tuple(second.execute("SELECT id, created_at FROM conversations").fetchone()) == tuple(filled)
    assert ledger.ledger_head(second) == 0


@pytest.mark.unit
def test_create_mints_an_id_and_records_one_change(tmp_path: Path) -> None:
    conn = open_store(tmp_path / "c.db")

    create(conn, _conversation(key="k1"), now=0.0, account_key="local", agent="reviewer")  # pragma: allowlist secret

    changes = ledger.changes_since(conn, 0, 10)
    row = conn.execute("SELECT id, agent, version, state FROM conversations WHERE key = 'k1'").fetchone()
    assert ids.parse_id("conv", row["id"]) is not None
    assert row["agent"] == "reviewer"
    assert [(c.noun, c.kind, c.id) for c in changes] == [("conversations", "created", row["id"])]


@pytest.mark.unit
def test_an_account_with_no_character_selected_records_no_agent(tmp_path: Path) -> None:
    """`None` is an answer — the neutral voice — not a missing value."""
    conn = open_store(tmp_path / "c.db")

    create(conn, _conversation(key="k1"), now=0.0, account_key="local")  # pragma: allowlist secret

    assert conn.execute("SELECT agent FROM conversations WHERE key = 'k1'").fetchone()["agent"] is None


@pytest.mark.unit
def test_saving_an_existing_conversation_keeps_its_id_and_bumps_its_version(tmp_path: Path) -> None:
    """The whole of "key immutable, id unique" in one assertion: a second
    write must not mint a second identity for the same thing."""
    conn = open_store(tmp_path / "c.db")
    convo = _conversation(key="k1")
    create(conn, convo, now=0.0, account_key="local", agent="reviewer")  # pragma: allowlist secret
    before = conn.execute("SELECT id, created_at, agent FROM conversations WHERE key = 'k1'").fetchone()

    save(conn, convo, now=0.0)

    after = conn.execute("SELECT id, created_at, agent, version FROM conversations WHERE key = 'k1'").fetchone()
    assert after["id"] == before["id"]
    assert after["created_at"] == before["created_at"]
    assert after["agent"] == before["agent"]
    assert after["version"] == 2


@pytest.mark.unit
def test_saving_records_one_message_change_per_row_that_genuinely_landed(tmp_path: Path) -> None:
    """A turn flushes repeatedly and the flushes overlap. Counting the input
    would announce changes that did not happen."""
    conn = open_store(tmp_path / "c.db")
    convo = _conversation(key="k1", messages=(Message(role="user", content="one"),))
    create(conn, convo, now=0.0, account_key="local")  # pragma: allowlist secret

    grown = replace(convo, messages=(*convo.messages, Message(role="assistant", content="two")))
    save(conn, grown, now=0.0)
    save(conn, grown, now=0.0)  # the same history again: nothing new landed

    message_changes = [c for c in ledger.changes_since(conn, 0, 50) if c.noun == "messages"]
    assert len(message_changes) == 2
    assert len({c.id for c in message_changes}) == 2


@pytest.mark.unit
def test_a_change_row_cannot_outlive_the_write_it_describes(tmp_path: Path) -> None:
    """`create` raising on a duplicate key must leave nothing behind — not the
    conversation row, and not the announcement of it."""
    conn = open_store(tmp_path / "c.db")
    create(conn, _conversation(key="k1"), now=0.0, account_key="local")  # pragma: allowlist secret
    head = ledger.ledger_head(conn)

    with pytest.raises(ConversationAlreadyExists):
        create(conn, _conversation(key="k1"), now=0.0, account_key="local")  # pragma: allowlist secret

    assert ledger.ledger_head(conn) == head


@pytest.mark.unit
def test_pausing_and_resuming_each_record_a_change_on_the_conversation(tmp_path: Path) -> None:
    """A pause is not one of `ledger.NOUNS` and has no id; what an observer
    needs to learn is that the conversation started and stopped waiting."""
    conn = open_store(tmp_path / "c.db")
    create(conn, _conversation(key="k1"), now=0.0, account_key="local")  # pragma: allowlist secret
    head = ledger.ledger_head(conn)

    save_pause(conn, conversation_key="k1", plugin="p", entry="e", node="n", trace=(), artifacts=())
    delete_pause(conn, conversation_key="k1")

    after = ledger.changes_since(conn, head, 10)
    assert [(c.noun, c.kind) for c in after] == [("conversations", "changed"), ("conversations", "changed")]
    assert [c.version for c in after] == [2, 3]


@pytest.mark.unit
def test_the_stored_timestamps_are_wall_clock_not_the_monotonic_now_threaded_in(tmp_path: Path) -> None:
    """`client_surface` passes `time.monotonic()` as `now`, because that is
    what a wall-clock budget needs. A timestamp two machines compare cannot
    come from there (`console_fit_plan.md` §5(g))."""
    conn = open_store(tmp_path / "c.db")

    create(conn, _conversation(key="k1"), now=0.0, account_key="local")  # pragma: allowlist secret

    created_at = conn.execute("SELECT created_at FROM conversations WHERE key = 'k1'").fetchone()["created_at"]
    assert created_at > 1_700_000_000.0
