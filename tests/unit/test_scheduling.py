"""Tests for sadana.scheduling: _is_due(), _advance(), tick()."""

from __future__ import annotations

import asyncio
import threading

import pytest

from conftest import make_runtime, open_connections, plain_response, write_character
from sadana import conversation_store, gateway_dispatch, memory, model_access, persona, persona_store, plugin_dispatch
from sadana.conversation_store import due_triggers, load, upsert_scheduled_trigger
from sadana.door.nouns import approvals as door_approvals
from sadana.gateway import MessageEvent, session_key_for
from sadana.scheduling import _advance, _is_due, tick


@pytest.mark.unit
@pytest.mark.parametrize(
    ("next_run_at", "now", "expected"),
    [(100.0, 100.0, True), (100.0, 99.0, False), (100.0, 200.0, True)],
)
def test_is_due(next_run_at: float, now: float, expected: bool) -> None:
    assert _is_due(next_run_at, now) is expected


@pytest.mark.unit
def test_advance_is_always_now_plus_interval_never_next_run_at_plus_interval() -> None:
    """Requirement 5's own mechanism: a trigger due since `next_run_at=0`,
    ticked at `now=1_000_000`, advances to `1_000_010`, not `10` — no
    backlog of already-past firings."""
    assert _advance(now=1_000_000.0, interval_seconds=10.0) == 1_000_010.0


def _fake_handle_inbound(
    calls: list[MessageEvent], *, raise_for: str | None = None, accounts: list[str | None] | None = None
):
    async def _handle_inbound(_runtime: object, event: MessageEvent, *, account: str | None = None) -> tuple[bool, str]:
        calls.append(event)
        if accounts is not None:
            accounts.append(account)
        if event.chat_id == raise_for:
            raise RuntimeError("boom")
        return True, "ok"

    return _handle_inbound


@pytest.mark.unit
def test_tick_fires_only_due_triggers(monkeypatch: pytest.MonkeyPatch) -> None:
    connections = open_connections()
    conn = connections.writer
    upsert_scheduled_trigger(conn, name="due", trigger_text="go", next_run_at=0.0, interval_seconds=None)
    upsert_scheduled_trigger(
        conn, name="not-due", trigger_text="go", next_run_at=9_999_999_999.0, interval_seconds=None
    )
    calls: list[MessageEvent] = []
    monkeypatch.setattr(gateway_dispatch, "handle_inbound", _fake_handle_inbound(calls))

    fired = asyncio.run(tick(make_runtime(connections)))

    assert fired == 1
    assert [c.chat_id for c in calls] == ["due"]
    assert calls[0].platform == "schedule"
    assert calls[0].text == "go"


@pytest.mark.unit
def test_tick_deletes_a_one_shot_trigger_after_firing(monkeypatch: pytest.MonkeyPatch) -> None:
    connections = open_connections()
    conn = connections.writer
    upsert_scheduled_trigger(conn, name="once", trigger_text="go", next_run_at=0.0, interval_seconds=None)
    monkeypatch.setattr(gateway_dispatch, "handle_inbound", _fake_handle_inbound([]))

    asyncio.run(tick(make_runtime(connections)))

    assert due_triggers(conn, now=9_999_999_999.0) == ()


@pytest.mark.unit
def test_tick_advances_a_recurring_trigger_to_now_plus_interval(monkeypatch: pytest.MonkeyPatch) -> None:
    connections = open_connections()
    conn = connections.writer
    upsert_scheduled_trigger(conn, name="weekly", trigger_text="go", next_run_at=0.0, interval_seconds=604800.0)
    monkeypatch.setattr(gateway_dispatch, "handle_inbound", _fake_handle_inbound([]))

    asyncio.run(tick(make_runtime(connections)))

    (row,) = due_triggers(conn, now=9_999_999_999.0)
    assert row.next_run_at > 604800.0 - 1  # advanced from *now*, not from the old next_run_at


@pytest.mark.unit
def test_tick_one_failing_trigger_does_not_stop_the_others_and_stays_due(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connections = open_connections()
    conn = connections.writer
    upsert_scheduled_trigger(conn, name="broken", trigger_text="go", next_run_at=0.0, interval_seconds=None)
    upsert_scheduled_trigger(conn, name="fine", trigger_text="go", next_run_at=0.0, interval_seconds=None)
    calls: list[MessageEvent] = []
    monkeypatch.setattr(gateway_dispatch, "handle_inbound", _fake_handle_inbound(calls, raise_for="broken"))

    fired = asyncio.run(tick(make_runtime(connections)))

    assert fired == 1
    assert {c.chat_id for c in calls} == {"broken", "fine"}
    remaining = due_triggers(conn, now=0.0)
    assert [t.name for t in remaining] == ["broken"]


# ── H18: expiry runs on the same tick ────────────────────────────────────


@pytest.mark.unit
def test_tick_calls_expire_due_once(monkeypatch: pytest.MonkeyPatch) -> None:
    connections = open_connections()
    monkeypatch.setattr(gateway_dispatch, "handle_inbound", _fake_handle_inbound([]))
    calls: list[float] = []

    async def fake_expire_due(_conns: object, now: float) -> int:
        calls.append(now)
        return 0

    monkeypatch.setattr(door_approvals, "expire_due", fake_expire_due)

    asyncio.run(tick(make_runtime(connections)))

    assert len(calls) == 1


@pytest.mark.unit
def test_tick_one_failing_approval_expiry_does_not_stop_trigger_firing(monkeypatch: pytest.MonkeyPatch) -> None:
    """The same per-item posture `test_tick_one_failing_trigger_does_not_stop_the_others_and_stays_due`
    already proves for triggers, now for `expire_due`'s own rows: a row
    whose resume raises is logged and skipped, and neither stops another
    due trigger from firing nor gets marked `expired` itself."""
    connections = open_connections()
    conn = connections.writer
    conversation_store.save_pause(
        conn, conversation_key="k-expire", plugin="p", entry="e", node="n", trace=(), artifacts=(), kind="wait"
    )
    conn.execute("UPDATE approvals SET expires_at = 0 WHERE conversation_key = 'k-expire'")
    conn.commit()
    upsert_scheduled_trigger(conn, name="due", trigger_text="go", next_run_at=0.0, interval_seconds=None)
    monkeypatch.setattr(gateway_dispatch, "handle_inbound", _fake_handle_inbound([]))

    async def _raising_resume(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("boom")

    monkeypatch.setattr(plugin_dispatch, "resume_paused_run", _raising_resume)

    fired = asyncio.run(tick(make_runtime(connections)))

    assert fired == 1  # the trigger still fired despite the failed expiry
    row = conn.execute("SELECT state FROM approvals WHERE conversation_key = 'k-expire'").fetchone()
    assert row["state"] == "waiting"  # never marked expired, since the resume raised


class _RecordingLock:
    """A real re-entrant lock, wrapped to count every acquisition.

    Deploy-stage cold review, before H16: `tick()`'s own direct `conn` touches
    raced every webhook request's own thread with no lock at all. The guarantee
    survived H16; only its mechanism moved, from one process-wide lock held
    around everything to the writer lock held inside each transaction.
    """

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self.acquisitions = 0

    def __enter__(self) -> None:
        self._lock.acquire()
        self.acquisitions += 1

    def __exit__(self, *_exc: object) -> None:
        self._lock.release()


@pytest.mark.unit
def test_tick_reads_through_a_reader_and_writes_under_the_writer_lock(monkeypatch: pytest.MonkeyPatch) -> None:
    """What the cold review's finding became after H16.

    The due-triggers query no longer takes any lock at all — it goes through
    this thread's own read-only connection, so a tick never queues behind
    somebody's turn. The post-fire delete still takes the writer lock, because
    it is a write. `handle_inbound` is called outside both, which is what keeps
    it from deadlocking against the conversation lock `take_turn` acquires.
    """
    connections = open_connections()
    conn = connections.writer
    upsert_scheduled_trigger(conn, name="due", trigger_text="go", next_run_at=0.0, interval_seconds=None)
    monkeypatch.setattr(gateway_dispatch, "handle_inbound", _fake_handle_inbound([]))
    recording_lock = _RecordingLock()
    monkeypatch.setattr(conversation_store, "_WRITE_LOCK", recording_lock)
    readers: list[int] = []
    real_reader = connections.reader
    monkeypatch.setattr(connections, "reader", lambda: (readers.append(1), real_reader())[1])

    asyncio.run(tick(make_runtime(connections)))

    assert readers, "the due-triggers query must not run on the writer"
    # Exactly one: the post-fire delete. Not two, which would mean the read had
    # taken the writer lock as well.
    assert recording_lock.acquisitions == 1


# ── PERSONA-02: a trigger the owner wrote runs as the owner ──────────────


@pytest.mark.unit
def test_a_fired_trigger_runs_as_the_owner_not_as_itself(monkeypatch: pytest.MonkeyPatch) -> None:
    """Whose the work is, and which thread it continues, are different
    questions: the account becomes the owner's while the conversation key
    stays the trigger's own."""
    monkeypatch.setenv("SADANA_MEMORY_ACCOUNT", "adam")
    connections = open_connections()
    conn = connections.writer
    upsert_scheduled_trigger(conn, name="daily", trigger_text="go", next_run_at=0.0, interval_seconds=None)
    calls: list[MessageEvent] = []
    accounts: list[str | None] = []
    monkeypatch.setattr(gateway_dispatch, "handle_inbound", _fake_handle_inbound(calls, accounts=accounts))

    asyncio.run(tick(make_runtime(connections)))

    assert accounts == [memory.owner_account()]
    assert session_key_for(calls[0]) == "schedule:daily"


@pytest.mark.unit
def test_a_scheduled_conversation_speaks_in_the_owners_chosen_voice(monkeypatch: pytest.MonkeyPatch) -> None:
    """Acceptance criterion 2, end to end through the real bridge: the owner
    chooses a character, a trigger fires, and the conversation it started
    was created in that voice. Composition is the whole point of this item —
    the account being the owner's is what makes the voice theirs."""
    monkeypatch.setenv("SADANA_MEMORY_ACCOUNT", "adam")
    monkeypatch.setattr(model_access, "send", lambda request: plain_response("done"))
    character = write_character("working", body="You read the code first.")
    connections = open_connections()
    conn = connections.writer
    persona_store.set_selection(conn, "adam", "working", now=0.0)
    upsert_scheduled_trigger(conn, name="daily", trigger_text="go", next_run_at=0.0, interval_seconds=None)

    asyncio.run(tick(make_runtime(connections)))

    convo = load(conn, "schedule:daily", now=0.0)
    assert convo.stable_prompt == persona.render(
        persona_store.load_character(persona_store.characters_dir_from_config(), "working")
    )
    assert character.exists()
