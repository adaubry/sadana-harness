"""Tests for sadana.conversation_store: create(), save(), load(), bind_persist()."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from conftest import SYSTEM_PROMPT as _SYSTEM_PROMPT
from conftest import conversation as _conversation
from conftest import tool_spec as _spec
from sadana import conversation_store, model_access, plugins
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
    bind_persist,
    create,
    load,
    open_store,
    save,
    search_conversations,
    store_path_from_config,
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

    create(conn, conv, now=100.0)
    loaded = load(conn, conv.key, now=100.0)

    assert loaded == conv


@pytest.mark.unit
def test_create_duplicate_key_raises_and_leaves_original_unchanged(tmp_path: Path) -> None:
    conn = open_store(tmp_path / "c.db")
    original = _conversation(messages=_messages())
    create(conn, original, now=0.0)

    colliding = _conversation(messages=(), next_turn_seq=99)
    with pytest.raises(ConversationAlreadyExists):
        create(conn, colliding, now=0.0)

    assert load(conn, "k1", now=0.0) == original


@pytest.mark.unit
def test_save_upserts_repeatedly_on_owned_key(tmp_path: Path) -> None:
    conn = open_store(tmp_path / "c.db")
    conv = _conversation(messages=_messages()[:1])
    create(conn, conv, now=0.0)

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
    create(conn, conv, now=0.0)
    save(conn, conv, now=0.0)
    save(conn, conv, now=0.0)

    count = conn.execute("SELECT COUNT(*) FROM messages WHERE conversation_key = ?", (conv.key,)).fetchone()[0]
    assert count == len(_messages())


# ── wall-clock budget portability ───────────────────────────────────────


@pytest.mark.unit
def test_wall_clock_budget_persists_as_remaining_seconds(tmp_path: Path) -> None:
    conn = open_store(tmp_path / "c.db")
    conv = _conversation(wall_clock_budget=WallClockBudget(deadline=130.0))

    create(conn, conv, now=100.0)  # 30s remaining at save time
    loaded = load(conn, conv.key, now=500.0)  # resumed 400s later, on a fresh clock

    assert loaded.wall_clock_budget == WallClockBudget(deadline=530.0)


@pytest.mark.unit
def test_no_wall_clock_budget_round_trips_as_none(tmp_path: Path) -> None:
    conn = open_store(tmp_path / "c.db")
    conv = _conversation(wall_clock_budget=None)
    create(conn, conv, now=0.0)
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
        create(conn, conv, now=0.0)

    assert conn.execute("SELECT COUNT(*) FROM conversations").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0] == 0


@pytest.mark.unit
def test_save_forced_failure_leaves_previous_snapshot_intact(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    conn = open_store(tmp_path / "c.db")
    first = _conversation(messages=_messages()[:1])
    create(conn, first, now=0.0)

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


def _tool_call_response(*names: str) -> model_access.Response:
    return model_access.Response(
        content=None,
        tool_calls=tuple({"function": {"name": n, "arguments": "{}"}} for n in names),
        finish_reason="tool_calls",
        usage=model_access.Usage(),
    )


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
    create(conn, conv, now=0.0)

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
    create(conn, _conversation(key="alpha"), now=0.0)
    create(conn, _conversation(key="beta"), now=0.0)

    found = search_conversations(conn, "", now=0.0)

    assert [c.key for c in found] == ["alpha", "beta"]  # ORDER BY key
    assert found[0] == load(conn, "alpha", now=0.0)
    assert found[1] == load(conn, "beta", now=0.0)


@pytest.mark.unit
def test_search_matches_key_substring_case_insensitively(tmp_path: Path) -> None:
    conn = open_store(tmp_path / "c.db")
    create(conn, _conversation(key="debugging-session"), now=0.0)
    create(conn, _conversation(key="unrelated"), now=0.0)

    found = search_conversations(conn, "DEBUG", now=0.0)

    assert [c.key for c in found] == ["debugging-session"]


@pytest.mark.unit
def test_search_matches_template_name_substring_case_insensitively(tmp_path: Path) -> None:
    conn = open_store(tmp_path / "c.db")
    create(conn, _conversation(key="k1", template_name="eval-harness"), now=0.0)
    create(conn, _conversation(key="k2", template_name="other"), now=0.0)

    found = search_conversations(conn, "EVAL", now=0.0)

    assert [c.key for c in found] == ["k1"]


@pytest.mark.unit
def test_search_matches_message_content_substring(tmp_path: Path) -> None:
    conn = open_store(tmp_path / "c.db")
    create(conn, _conversation(key="k1", messages=(Message(role="user", content="find the needle here"),)), now=0.0)
    create(conn, _conversation(key="k2", messages=(Message(role="user", content="nothing to see"),)), now=0.0)

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
    )

    found = search_conversations(conn, "widget", now=0.0)

    assert [c.key for c in found] == ["widget-session"]


@pytest.mark.unit
def test_search_no_match_returns_empty_list(tmp_path: Path) -> None:
    conn = open_store(tmp_path / "c.db")
    create(conn, _conversation(key="k1"), now=0.0)

    assert search_conversations(conn, "nothing-saved-matches-this", now=0.0) == []


@pytest.mark.unit
def test_search_escapes_percent_and_underscore_as_literals(tmp_path: Path) -> None:
    conn = open_store(tmp_path / "c.db")
    create(conn, _conversation(key="50%off"), now=0.0)
    create(conn, _conversation(key="50Xoff"), now=0.0)

    assert [c.key for c in search_conversations(conn, "50%off", now=0.0)] == ["50%off"]
    assert search_conversations(conn, "50_off", now=0.0) == []
