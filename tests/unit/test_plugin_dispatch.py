"""Tests for sadana.plugin_dispatch: PluginSet, build_plugin_set,
ChildSeqTracker, build_dispatch."""

from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from pathlib import Path

import pytest

from conftest import open_conn
from sadana import context, model_access, plugin_dispatch
from sadana.conversation import (
    Conversation,
    ConversationTemplate,
    DuplicateToolError,
    ExitReason,
    IterationBudget,
    PluginCatalogEntry,
    TemplateRecipe,
    TurnKey,
    TurnResult,
    build_surface,
    create_conversation,
)
from sadana.memory_store import DispatchContext
from sadana.plugin_dispatch import ChildSeqTracker, PluginSet, build_dispatch, build_plugin_set, take_turn_and_reconcile
from sadana.plugins import Entry, InstalledPlugin, Manifest, Node


def _template() -> ConversationTemplate:
    return ConversationTemplate(name="t", recipe=TemplateRecipe(stable_prompt="", catalog=(), tool_specs=()))


def _conversation() -> Conversation:
    conv, _t = create_conversation(
        _template(), key="c1", system_message="", iteration_budget=IterationBudget(max_total=10)
    )
    return conv


def _write_schema(plugin_dir: Path, name: str = "s.json") -> None:
    plugin_dir.mkdir(parents=True, exist_ok=True)
    (plugin_dir / name).write_text(json.dumps({"type": "object"}))


# ── build_plugin_set ─────────────────────────────────────────────────────


@pytest.mark.unit
def test_build_plugin_set_derives_one_catalog_entry_and_tool_spec_per_entry(tmp_path: Path) -> None:
    plugin_dir = tmp_path / "p1"
    _write_schema(plugin_dir)
    manifest = Manifest(
        name="plugin-one",
        version="0.1.0",
        description="d",
        entries=(Entry(tool="do_thing", purpose="Does the thing.", parameters="s.json", start="a"),),
        nodes=(Node(name="a", kind="stop"),),
    )
    installed = InstalledPlugin(name="plugin-one", directory=plugin_dir, manifest=manifest)
    plugin_set = build_plugin_set([installed])

    assert plugin_set.catalog == (
        PluginCatalogEntry(name="plugin-one", purpose="Does the thing.", entry_tool="do_thing"),
    )
    assert len(plugin_set.tool_specs) == 1
    spec = plugin_set.tool_specs[0]
    assert spec.key == "do_thing"
    assert spec.name == "do_thing"
    assert spec.parameters == {"type": "object"}
    assert spec.describe({}) == "Does the thing."
    assert plugin_set.by_tool == {"do_thing": (installed, manifest.entries[0])}


@pytest.mark.unit
def test_build_plugin_set_covers_multiple_plugins_and_entries(tmp_path: Path) -> None:
    dir_one = tmp_path / "p1"
    _write_schema(dir_one)
    manifest_one = Manifest(
        name="plugin-one",
        version="0.1.0",
        description="d",
        entries=(Entry(tool="tool_one", purpose="One.", parameters="s.json", start="a"),),
        nodes=(Node(name="a", kind="stop"),),
    )
    dir_two = tmp_path / "p2"
    _write_schema(dir_two)
    manifest_two = Manifest(
        name="plugin-two",
        version="0.1.0",
        description="d",
        entries=(Entry(tool="tool_two", purpose="Two.", parameters="s.json", start="a"),),
        nodes=(Node(name="a", kind="stop"),),
    )
    installed = [
        InstalledPlugin(name="plugin-one", directory=dir_one, manifest=manifest_one),
        InstalledPlugin(name="plugin-two", directory=dir_two, manifest=manifest_two),
    ]
    plugin_set = build_plugin_set(installed)
    assert {e.entry_tool for e in plugin_set.catalog} == {"tool_one", "tool_two"}
    assert {s.name for s in plugin_set.tool_specs} == {"tool_one", "tool_two"}
    assert set(plugin_set.by_tool) == {"tool_one", "tool_two"}


@pytest.mark.unit
def test_build_plugin_set_does_not_check_for_a_duplicate_tool_name(tmp_path: Path) -> None:
    """Requirement 1's "fail loudly" is satisfied downstream, by the
    already-existing conversation.build_surface — not by a second check
    here. This test proves both halves: build_plugin_set itself raises
    nothing, and running its output through build_surface does."""
    dir_one = tmp_path / "p1"
    _write_schema(dir_one)
    manifest_one = Manifest(
        name="plugin-one",
        version="0.1.0",
        description="d",
        entries=(Entry(tool="shared_tool", purpose="One.", parameters="s.json", start="a"),),
        nodes=(Node(name="a", kind="stop"),),
    )
    dir_two = tmp_path / "p2"
    _write_schema(dir_two)
    manifest_two = Manifest(
        name="plugin-two",
        version="0.1.0",
        description="d",
        entries=(Entry(tool="shared_tool", purpose="Two.", parameters="s.json", start="a"),),
        nodes=(Node(name="a", kind="stop"),),
    )
    installed = [
        InstalledPlugin(name="plugin-one", directory=dir_one, manifest=manifest_one),
        InstalledPlugin(name="plugin-two", directory=dir_two, manifest=manifest_two),
    ]

    plugin_set = build_plugin_set(installed)  # must not raise
    assert len(plugin_set.tool_specs) == 2

    with pytest.raises(DuplicateToolError):
        build_surface(plugin_set.tool_specs)


# ── build_dispatch ───────────────────────────────────────────────────────


@pytest.mark.unit
def test_build_dispatch_unknown_tool_returns_a_dag_result_not_an_exception() -> None:
    plugin_set = PluginSet(catalog=(), tool_specs=(), by_tool={})
    dispatch, _tracker = build_dispatch(_conversation(), plugin_set, stable_prompt="", provider="p", model="m", now=0.0)
    result = asyncio.run(dispatch("nonexistent_tool", {}))
    assert result.failed_node == "entry"
    assert "nonexistent_tool" in result.text


def _fake_run_child_factory(*, exit_reason: ExitReason, final_text: str | None):
    seen_parent_seqs: list[int] = []

    async def fake_run_child(parent, spec, **_kwargs):  # type: ignore[no-untyped-def]
        seen_parent_seqs.append(parent.next_child_seq)
        turn_result = TurnResult(
            turn_key=TurnKey(conversation=parent.key, turn_seq=0),
            final_text=final_text,
            exit_reason=exit_reason,
            detail=None,
            model_calls=1,
            usage=model_access.Usage(),
            appended=range(0),
            context_state=context.ContextState(),
        )
        updated_parent = replace(parent, next_child_seq=parent.next_child_seq + 1)
        return turn_result, updated_parent, updated_parent

    return fake_run_child, seen_parent_seqs


def _one_ask_node_plugin_set(tmp_path: Path) -> PluginSet:
    plugin_dir = tmp_path / "p"
    plugin_dir.mkdir()
    manifest = Manifest(
        name="p",
        version="0.1.0",
        description="d",
        entries=(Entry(tool="do_it", purpose="p", parameters="s.json", start="ask_step"),),
        nodes=(Node(name="ask_step", kind="ask", skill="s1"),),
    )
    installed = InstalledPlugin(name="p", directory=plugin_dir, manifest=manifest)
    return PluginSet(catalog=(), tool_specs=(), by_tool={"do_it": (installed, manifest.entries[0])})


def _fake_take_turn_factory(*, final_text: str = "hi"):  # type: ignore[no-untyped-def]
    """A `_take_turn` stand-in returning a minimal, fully-defaulted
    `TurnResult` for `conv` unchanged — shared by both
    `take_turn_and_reconcile` tests below that only care about the
    wrapper's own behavior (tracker reconciliation, recording), not
    anything `_take_turn` itself would compute."""

    async def fake_take_turn(conv, **_kwargs):  # type: ignore[no-untyped-def]
        turn_result = TurnResult(
            turn_key=TurnKey(conversation=conv.key, turn_seq=0),
            final_text=final_text,
            exit_reason=ExitReason.COMPLETED,
            detail=None,
            model_calls=1,
            usage=model_access.Usage(),
            appended=range(0),
            context_state=context.ContextState(),
        )
        return turn_result, conv  # conv.next_child_seq is left exactly as given

    return fake_take_turn


def _one_call_node_plugin_set(tmp_path: Path) -> PluginSet:
    plugin_dir = tmp_path / "p"
    plugin_dir.mkdir()
    (plugin_dir / "init.py").write_text("def reach_out(value):\n    return {'reached': value}\n")
    manifest = Manifest(
        name="p",
        version="0.1.0",
        description="d",
        entries=(Entry(tool="do_it", purpose="p", parameters="s.json", start="call_step"),),
        nodes=(Node(name="call_step", kind="call", body="init:reach_out"),),
    )
    installed = InstalledPlugin(name="p", directory=plugin_dir, manifest=manifest)
    return PluginSet(catalog=(), tool_specs=(), by_tool={"do_it": (installed, manifest.entries[0])})


@pytest.mark.unit
def test_build_dispatch_ask_updates_the_tracker_from_run_childs_updated_parent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_run_child, seen_parent_seqs = _fake_run_child_factory(exit_reason=ExitReason.COMPLETED, final_text="done")
    monkeypatch.setattr(plugin_dispatch, "run_child", fake_run_child)

    plugin_set = _one_ask_node_plugin_set(tmp_path)
    conversation = _conversation()
    dispatch, tracker = build_dispatch(conversation, plugin_set, stable_prompt="", provider="p", model="m", now=0.0)

    result = asyncio.run(dispatch("do_it", {}))

    assert result.failed_node is None
    assert result.text == "done"
    assert seen_parent_seqs == [conversation.next_child_seq]
    assert tracker.next_seq == conversation.next_child_seq + 1


@pytest.mark.unit
def test_build_dispatch_ask_reports_failure_when_the_child_did_not_complete(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_run_child, _seen = _fake_run_child_factory(exit_reason=ExitReason.BUDGET_EXHAUSTED, final_text=None)
    monkeypatch.setattr(plugin_dispatch, "run_child", fake_run_child)

    plugin_set = _one_ask_node_plugin_set(tmp_path)
    dispatch, _tracker = build_dispatch(_conversation(), plugin_set, stable_prompt="", provider="p", model="m", now=0.0)

    result = asyncio.run(dispatch("do_it", {}))

    assert result.failed_node == "ask_step"


# ── take_turn_and_reconcile ──────────────────────────────────────────────


@pytest.mark.unit
def test_take_turn_and_reconcile_applies_the_trackers_seq_onto_the_result(monkeypatch: pytest.MonkeyPatch) -> None:
    """conversation.take_turn's own return value carries whatever
    next_child_seq the Conversation it was called with already had —
    exactly the gap docs/reference/dispatch_closure_state_bug.md names,
    since take_turn never learns what a dispatch call's ask callback did.
    This proves the wrapper overrides that stale value with the tracker's,
    not the other way around."""
    monkeypatch.setattr(plugin_dispatch, "_take_turn", _fake_take_turn_factory())

    async def _unused_dispatch(_name: str, _arguments: dict) -> plugin_dispatch.plugins.DagResult:
        raise AssertionError("dispatch should not be called by this test")

    conversation = _conversation()
    tracker = ChildSeqTracker(next_seq=5)
    result, updated = asyncio.run(
        take_turn_and_reconcile(
            conversation, _unused_dispatch, tracker, user_input="hi", provider="p", model="m", now=0.0
        )
    )

    assert result.final_text == "hi"
    assert updated.next_child_seq == 5


# ── recording (OBSERVABILITY-01) ─────────────────────────────────────────


def _fake_recorder():  # type: ignore[no-untyped-def]
    calls: dict[str, list] = {"turn": [], "plugin_run": []}

    async def record_turn(result, duration_s):  # type: ignore[no-untyped-def]
        calls["turn"].append((result, duration_s))

    async def record_plugin_run(turn_key, seq, result, duration_s):  # type: ignore[no-untyped-def]
        calls["plugin_run"].append((turn_key, seq, result, duration_s))

    return record_turn, record_plugin_run, calls


@pytest.mark.unit
def test_take_turn_and_reconcile_calls_record_turn_with_the_result_and_a_duration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(plugin_dispatch, "_take_turn", _fake_take_turn_factory())
    record_turn, _record_plugin_run, calls = _fake_recorder()

    async def _unused_dispatch(_name: str, _arguments: dict) -> plugin_dispatch.plugins.DagResult:
        raise AssertionError("dispatch should not be called by this test")

    conversation = _conversation()
    tracker = ChildSeqTracker(next_seq=0)
    result, _updated = asyncio.run(
        take_turn_and_reconcile(
            conversation,
            _unused_dispatch,
            tracker,
            user_input="hi",
            provider="p",
            model="m",
            now=0.0,
            record_turn=record_turn,
        )
    )
    assert len(calls["turn"]) == 1
    recorded_result, duration_s = calls["turn"][0]
    assert recorded_result is result
    assert duration_s >= 0.0


@pytest.mark.unit
def test_build_dispatch_calls_record_plugin_run_once_per_dispatch_call(tmp_path: Path) -> None:
    plugin_set = _one_call_node_plugin_set(tmp_path)
    _record_turn, record_plugin_run, calls = _fake_recorder()
    conversation = _conversation()

    dispatch, _tracker = build_dispatch(
        conversation,
        plugin_set,
        stable_prompt="",
        provider="p",
        model="m",
        now=0.0,
        record_plugin_run=record_plugin_run,
    )

    asyncio.run(dispatch("do_it", {"a": 1}))
    asyncio.run(dispatch("do_it", {"a": 2}))

    assert len(calls["plugin_run"]) == 2
    (turn_key_0, seq_0, result_0, duration_0), (turn_key_1, seq_1, result_1, duration_1) = calls["plugin_run"]
    assert turn_key_0 == turn_key_1 == TurnKey(conversation=conversation.key, turn_seq=conversation.next_turn_seq)
    assert (seq_0, seq_1) == (0, 1)
    assert duration_0 >= 0.0 and duration_1 >= 0.0
    assert result_0.plugin == "p" and result_1.plugin == "p"


@pytest.mark.unit
def test_build_dispatch_does_not_call_record_plugin_run_for_an_unknown_tool() -> None:
    plugin_set = PluginSet(catalog=(), tool_specs=(), by_tool={})
    _record_turn, record_plugin_run, calls = _fake_recorder()

    dispatch, _tracker = build_dispatch(
        _conversation(),
        plugin_set,
        stable_prompt="",
        provider="p",
        model="m",
        now=0.0,
        record_plugin_run=record_plugin_run,
    )
    asyncio.run(dispatch("nonexistent_tool", {}))

    assert calls["plugin_run"] == []


@pytest.mark.unit
def test_build_dispatch_ask_calls_record_turn_with_the_childs_own_turn_result(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_run_child, _seen = _fake_run_child_factory(exit_reason=ExitReason.COMPLETED, final_text="done")
    monkeypatch.setattr(plugin_dispatch, "run_child", fake_run_child)
    plugin_set = _one_ask_node_plugin_set(tmp_path)
    record_turn, _record_plugin_run, calls = _fake_recorder()

    dispatch, _tracker = build_dispatch(
        _conversation(), plugin_set, stable_prompt="", provider="p", model="m", now=0.0, record_turn=record_turn
    )
    asyncio.run(dispatch("do_it", {}))

    assert len(calls["turn"]) == 1
    recorded_result, duration_s = calls["turn"][0]
    assert recorded_result.final_text == "done"
    assert duration_s >= 0.0


@pytest.mark.unit
def test_build_dispatch_does_not_add_its_own_guard_around_the_given_recorder(tmp_path: Path) -> None:
    """The best-effort contract (a recording failure never reaches the run
    being observed) belongs entirely to `observability.py`'s own
    try/except around a real `sqlite3.Error` — proven in
    `test_observability.py`. This proves `build_dispatch` doesn't add a
    second, redundant guard of its own: a recorder that raises something
    that isn't `observability`'s problem (a plain bug in a test double, a
    caller-supplied recorder that never went through `make_recorder`)
    still propagates, exactly like `persist` already does."""
    plugin_set = _one_call_node_plugin_set(tmp_path)

    async def raising_record_plugin_run(_turn_key, _seq, _result, _duration_s):  # type: ignore[no-untyped-def]
        raise RuntimeError("boom")

    dispatch, _tracker = build_dispatch(
        _conversation(),
        plugin_set,
        stable_prompt="",
        provider="p",
        model="m",
        now=0.0,
        record_plugin_run=raising_record_plugin_run,
    )

    with pytest.raises(RuntimeError, match="boom"):
        asyncio.run(dispatch("do_it", {"a": 1}))


@pytest.mark.unit
def test_build_dispatch_threads_a_given_approve_to_run_graph(tmp_path: Path) -> None:
    """G3-real-plugin-under-eval: without this passthrough, a `call` node
    built through `build_dispatch` falls back to `run_graph`'s own
    default, which blocks on real stdin — this test would hang instead of
    failing if the passthrough were missing."""
    plugin_set = _one_call_node_plugin_set(tmp_path)
    seen: list[tuple[str, str]] = []

    async def fake_approve(plugin: str, node: str, _value: object) -> bool:
        seen.append((plugin, node))
        return True

    dispatch, _tracker = build_dispatch(
        _conversation(), plugin_set, stable_prompt="", provider="p", model="m", now=0.0, approve=fake_approve
    )

    result = asyncio.run(dispatch("do_it", {"a": 1}))

    assert result.failed_node is None
    assert result.text == json.dumps({"reached": {"a": 1}})
    assert seen == [("p", "call_step")]


def _account_reporting_plugin_set(tmp_path: Path) -> PluginSet:
    """A `call` node whose body reports only the account key it saw on
    `_sadana_memory_ctx` — never the raw value — so a `DispatchContext`
    (not JSON-serializable) never has to survive into `run_graph`'s own
    final `_coerce_text` call."""
    plugin_dir = tmp_path / "p"
    plugin_dir.mkdir()
    plugin_dir.joinpath("init.py").write_text(
        "def report(value):\n"
        "    ctx = value.get('_sadana_memory_ctx')\n"
        "    return {'saw_account': getattr(ctx, 'account_key', None)}\n"
    )
    manifest = Manifest(
        name="p",
        version="0.1.0",
        description="d",
        entries=(Entry(tool="do_it", purpose="p", parameters="s.json", start="call_step"),),
        nodes=(Node(name="call_step", kind="call", body="init:report"),),
    )
    installed = InstalledPlugin(name="p", directory=plugin_dir, manifest=manifest)
    return PluginSet(catalog=(), tool_specs=(), by_tool={"do_it": (installed, manifest.entries[0])})


@pytest.mark.unit
def test_build_dispatch_memory_context_wins_over_a_model_supplied_value(tmp_path: Path) -> None:
    """MEMORY-01: `_sadana_memory_ctx` is merged into `arguments` *last*, so
    a model that happened to supply its own value under that key can never
    make a node body see anything but the trusted context."""
    plugin_set = _account_reporting_plugin_set(tmp_path)
    conn = open_conn()
    trusted = DispatchContext(account_key="real-account", conn=conn)

    async def approve_ok(_plugin: str, _node: str, _value: object) -> bool:
        return True

    dispatch, _tracker = build_dispatch(
        _conversation(),
        plugin_set,
        stable_prompt="",
        provider="p",
        model="m",
        now=0.0,
        approve=approve_ok,
        memory_context=trusted,
    )
    result = asyncio.run(dispatch("do_it", {"_sadana_memory_ctx": "attacker-supplied"}))

    assert result.failed_node is None
    assert result.text == json.dumps({"saw_account": "real-account"})


@pytest.mark.unit
def test_build_dispatch_memory_context_none_leaves_arguments_unchanged(tmp_path: Path) -> None:
    """`memory_context` defaults to `None` — a caller that never passes it
    gets exactly today's behavior, with no `_sadana_memory_ctx` key at all."""
    plugin_set = _one_call_node_plugin_set(tmp_path)

    async def approve_ok(_plugin: str, _node: str, _value: object) -> bool:
        return True

    dispatch, _tracker = build_dispatch(
        _conversation(), plugin_set, stable_prompt="", provider="p", model="m", now=0.0, approve=approve_ok
    )
    result = asyncio.run(dispatch("do_it", {"a": 1}))

    assert result.failed_node is None
    assert result.text == json.dumps({"reached": {"a": 1}})
