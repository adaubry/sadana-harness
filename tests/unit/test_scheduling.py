"""Tests for sadana.scheduling: _is_due(), _advance(), tick()."""

from __future__ import annotations

import asyncio
import threading

import pytest

from conftest import make_runtime, open_conn, plain_response, write_character
from sadana import client_surface, gateway_dispatch, memory, model_access, persona, persona_store, stores
from sadana.conversation_store import due_triggers, load, upsert_scheduled_trigger
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
    conn = open_conn()
    upsert_scheduled_trigger(conn, name="due", trigger_text="go", next_run_at=0.0, interval_seconds=None)
    upsert_scheduled_trigger(
        conn, name="not-due", trigger_text="go", next_run_at=9_999_999_999.0, interval_seconds=None
    )
    calls: list[MessageEvent] = []
    monkeypatch.setattr(gateway_dispatch, "handle_inbound", _fake_handle_inbound(calls))

    fired = asyncio.run(tick(make_runtime(conn)))

    assert fired == 1
    assert [c.chat_id for c in calls] == ["due"]
    assert calls[0].platform == "schedule"
    assert calls[0].text == "go"


@pytest.mark.unit
def test_tick_deletes_a_one_shot_trigger_after_firing(monkeypatch: pytest.MonkeyPatch) -> None:
    conn = open_conn()
    upsert_scheduled_trigger(conn, name="once", trigger_text="go", next_run_at=0.0, interval_seconds=None)
    monkeypatch.setattr(gateway_dispatch, "handle_inbound", _fake_handle_inbound([]))

    asyncio.run(tick(make_runtime(conn)))

    assert due_triggers(conn, now=9_999_999_999.0) == ()


@pytest.mark.unit
def test_tick_advances_a_recurring_trigger_to_now_plus_interval(monkeypatch: pytest.MonkeyPatch) -> None:
    conn = open_conn()
    upsert_scheduled_trigger(conn, name="weekly", trigger_text="go", next_run_at=0.0, interval_seconds=604800.0)
    monkeypatch.setattr(gateway_dispatch, "handle_inbound", _fake_handle_inbound([]))

    asyncio.run(tick(make_runtime(conn)))

    (row,) = due_triggers(conn, now=9_999_999_999.0)
    assert row.next_run_at > 604800.0 - 1  # advanced from *now*, not from the old next_run_at


@pytest.mark.unit
def test_tick_one_failing_trigger_does_not_stop_the_others_and_stays_due(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conn = open_conn()
    upsert_scheduled_trigger(conn, name="broken", trigger_text="go", next_run_at=0.0, interval_seconds=None)
    upsert_scheduled_trigger(conn, name="fine", trigger_text="go", next_run_at=0.0, interval_seconds=None)
    calls: list[MessageEvent] = []
    monkeypatch.setattr(gateway_dispatch, "handle_inbound", _fake_handle_inbound(calls, raise_for="broken"))

    fired = asyncio.run(tick(make_runtime(conn)))

    assert fired == 1
    assert {c.chat_id for c in calls} == {"broken", "fine"}
    remaining = due_triggers(conn, now=0.0)
    assert [t.name for t in remaining] == ["broken"]


class _RecordingLock:
    """A real `threading.Lock`, wrapped to record every `with` acquisition
    — deploy-stage cold review: `tick()`'s own direct `conn` touches raced
    every webhook request's own thread with no lock at all. This proves
    the wiring, not real concurrency (that's `client_surface.conn_lock`'s
    own single, project-wide lock object, already relied on for real by
    `take_turn`)."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.acquisitions = 0

    def __enter__(self) -> None:
        self._lock.acquire()
        self.acquisitions += 1

    def __exit__(self, *_exc: object) -> None:
        self._lock.release()


@pytest.mark.unit
def test_tick_wraps_its_own_conn_access_in_the_shared_conn_lock(monkeypatch: pytest.MonkeyPatch) -> None:
    conn = open_conn()
    upsert_scheduled_trigger(conn, name="due", trigger_text="go", next_run_at=0.0, interval_seconds=None)
    monkeypatch.setattr(gateway_dispatch, "handle_inbound", _fake_handle_inbound([]))
    recording_lock = _RecordingLock()
    monkeypatch.setattr(client_surface, "conn_lock", recording_lock)

    asyncio.run(tick(make_runtime(conn)))

    # once for the due_triggers() read, once for the post-fire delete —
    # handle_inbound() is called *outside* both, so this also proves tick()
    # never nests an acquisition inside its own (which would deadlock the
    # real, non-reentrant threading.Lock).
    assert recording_lock.acquisitions == 2


# ── PERSONA-02: a trigger the owner wrote runs as the owner ──────────────


@pytest.mark.unit
def test_a_fired_trigger_runs_as_the_owner_not_as_itself(monkeypatch: pytest.MonkeyPatch) -> None:
    """Whose the work is, and which thread it continues, are different
    questions: the account becomes the owner's while the conversation key
    stays the trigger's own."""
    monkeypatch.setenv("SADANA_MEMORY_ACCOUNT", "adam")
    conn = open_conn()
    upsert_scheduled_trigger(conn, name="daily", trigger_text="go", next_run_at=0.0, interval_seconds=None)
    calls: list[MessageEvent] = []
    accounts: list[str | None] = []
    monkeypatch.setattr(gateway_dispatch, "handle_inbound", _fake_handle_inbound(calls, accounts=accounts))

    asyncio.run(tick(make_runtime(conn)))

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
    conn = open_conn()
    stores.ensure_schemas(conn)
    persona_store.set_selection(conn, "adam", "working", now=0.0)
    upsert_scheduled_trigger(conn, name="daily", trigger_text="go", next_run_at=0.0, interval_seconds=None)

    asyncio.run(tick(make_runtime(conn)))

    convo = load(conn, "schedule:daily", now=0.0)
    assert convo.stable_prompt == persona.render(
        persona_store.load_character(persona_store.characters_dir_from_config(), "working")
    )
    assert character.exists()
