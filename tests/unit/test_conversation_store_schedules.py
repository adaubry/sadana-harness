"""Tests for the `schedules` table and its accessors (H27):
create/get/list/update/pause-resume/remove, account scoping, the ledger
write on every mutation, and the `scheduled_triggers` migration.

A new file, not an addition to `test_conversation_store.py` (already 1045
lines) — schedules is a distinct table and concern, matching
testing-conventions' "large enough, use a suffix" naming rule.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from sadana import ledger
from sadana.conversation_store import (
    adopt_scheduled_triggers,
    advance_schedule,
    create_schedule,
    due_schedules,
    get_schedule,
    list_schedules,
    open_store,
    remove_schedule,
    set_schedule_state,
    update_schedule,
    upsert_scheduled_trigger,
)

_ACCOUNT = "console:u1"  # pragma: allowlist secret — an account name, not a credential
_OTHER_ACCOUNT = "console:u2"  # pragma: allowlist secret
_OWNER = "console:owner"  # pragma: allowlist secret


def _make(conn: sqlite3.Connection, *, name: str = "digest", account_key: str = _ACCOUNT, **overrides: object):
    fields: dict[str, object] = dict(
        name=name,
        cron="0 9 * * *",
        timezone="UTC",
        interval_seconds=None,
        trigger_text="say good morning",
        plugin=None,
        conversation_key=None,
        account_key=account_key,
        next_run_at=1_000.0,
        now=1.0,
    )
    fields.update(overrides)
    return create_schedule(conn, **fields)  # type: ignore[arg-type]


@pytest.mark.unit
def test_create_then_get_round_trips_every_column(tmp_path: Path) -> None:
    conn = open_store(tmp_path / "c.db")
    created = _make(conn, plugin="p", conversation_key="k1")

    row = get_schedule(conn, account_key=_ACCOUNT, id=created.id)

    assert row is not None
    assert row.name == "digest"
    assert row.cron == "0 9 * * *"
    assert row.timezone == "UTC"
    assert row.interval_seconds is None
    assert row.trigger_text == "say good morning"
    assert row.plugin == "p"
    assert row.conversation_key == "k1"
    assert row.account_key == _ACCOUNT
    assert row.next_run_at == 1_000.0
    assert row.last_run_at is None
    assert row.last_state is None
    assert row.state == "active"
    assert row.version == 1


@pytest.mark.unit
def test_create_writes_a_ledger_created_row(tmp_path: Path) -> None:
    conn = open_store(tmp_path / "c.db")
    created = _make(conn)

    changes = ledger.changes_since(conn, cursor=0, limit=1000)
    schedule_changes = [c for c in changes if c.noun == "schedules" and c.id == created.id]
    assert len(schedule_changes) == 1
    assert schedule_changes[0].kind == "created"
    assert schedule_changes[0].version == 1


@pytest.mark.unit
def test_duplicate_name_raises_integrity_error(tmp_path: Path) -> None:
    conn = open_store(tmp_path / "c.db")
    _make(conn, name="dup")
    with pytest.raises(sqlite3.IntegrityError):
        _make(conn, name="dup")


@pytest.mark.unit
def test_get_and_list_are_scoped_to_account(tmp_path: Path) -> None:
    conn = open_store(tmp_path / "c.db")
    mine = _make(conn, name="mine", account_key=_ACCOUNT)
    _make(conn, name="theirs", account_key=_OTHER_ACCOUNT)

    assert get_schedule(conn, account_key=_OTHER_ACCOUNT, id=mine.id) is None
    assert [r.name for r in list_schedules(conn, account_key=_ACCOUNT)] == ["mine"]
    assert [r.name for r in list_schedules(conn, account_key=_OTHER_ACCOUNT)] == ["theirs"]


@pytest.mark.unit
def test_update_changes_only_the_given_fields(tmp_path: Path) -> None:
    conn = open_store(tmp_path / "c.db")
    created = _make(conn)

    update_schedule(conn, account_key=_ACCOUNT, id=created.id, now=2.0, trigger_text="new text")

    row = get_schedule(conn, account_key=_ACCOUNT, id=created.id)
    assert row is not None
    assert row.trigger_text == "new text"
    assert row.cron == "0 9 * * *"  # untouched
    assert row.version == 2


@pytest.mark.unit
def test_update_against_another_accounts_schedule_touches_nothing(tmp_path: Path) -> None:
    conn = open_store(tmp_path / "c.db")
    created = _make(conn, account_key=_ACCOUNT)

    update_schedule(conn, account_key=_OTHER_ACCOUNT, id=created.id, now=2.0, trigger_text="hijacked")

    row = get_schedule(conn, account_key=_ACCOUNT, id=created.id)
    assert row is not None
    assert row.trigger_text == "say good morning"
    assert row.version == 1


@pytest.mark.unit
def test_paused_schedule_is_excluded_from_due_schedules(tmp_path: Path) -> None:
    conn = open_store(tmp_path / "c.db")
    created = _make(conn, next_run_at=0.0)
    assert [r.id for r in due_schedules(conn, now=1_000_000.0)] == [created.id]

    set_schedule_state(conn, account_key=_ACCOUNT, id=created.id, to_state="paused", now=2.0)

    assert due_schedules(conn, now=1_000_000.0) == ()


@pytest.mark.unit
def test_resume_sets_next_run_at_and_reactivates(tmp_path: Path) -> None:
    conn = open_store(tmp_path / "c.db")
    created = _make(conn, next_run_at=0.0)
    set_schedule_state(conn, account_key=_ACCOUNT, id=created.id, to_state="paused", now=2.0)

    set_schedule_state(conn, account_key=_ACCOUNT, id=created.id, to_state="active", now=3.0, next_run_at=5_000.0)

    row = get_schedule(conn, account_key=_ACCOUNT, id=created.id)
    assert row is not None
    assert row.state == "active"
    assert row.next_run_at == 5_000.0


@pytest.mark.unit
def test_advance_schedule_records_last_run_and_moves_next_run_at(tmp_path: Path) -> None:
    conn = open_store(tmp_path / "c.db")
    created = _make(conn, next_run_at=0.0)

    advance_schedule(conn, id=created.id, next_run_at=100.0, last_run_at=1.0, last_state="completed", now=1.0)

    row = get_schedule(conn, account_key=_ACCOUNT, id=created.id)
    assert row is not None
    assert row.next_run_at == 100.0
    assert row.last_run_at == 1.0
    assert row.last_state == "completed"
    assert row.version == 2


@pytest.mark.unit
def test_remove_writes_a_ledger_deleted_row_then_deletes(tmp_path: Path) -> None:
    conn = open_store(tmp_path / "c.db")
    created = _make(conn)

    remove_schedule(conn, account_key=_ACCOUNT, id=created.id)

    assert get_schedule(conn, account_key=_ACCOUNT, id=created.id) is None
    changes = ledger.changes_since(conn, cursor=0, limit=1000)
    deleted = [c for c in changes if c.noun == "schedules" and c.id == created.id and c.kind == "deleted"]
    assert len(deleted) == 1


@pytest.mark.unit
def test_remove_twice_is_a_silent_no_op(tmp_path: Path) -> None:
    conn = open_store(tmp_path / "c.db")
    created = _make(conn)
    remove_schedule(conn, account_key=_ACCOUNT, id=created.id)

    remove_schedule(conn, account_key=_ACCOUNT, id=created.id)  # must not raise

    assert get_schedule(conn, account_key=_ACCOUNT, id=created.id) is None


@pytest.mark.unit
def test_adopt_scheduled_triggers_copies_every_recurring_legacy_row_once(tmp_path: Path) -> None:
    conn = open_store(tmp_path / "c.db")
    upsert_scheduled_trigger(conn, name="legacy-one", trigger_text="go", next_run_at=42.0, interval_seconds=30.0)
    upsert_scheduled_trigger(conn, name="legacy-two", trigger_text="go2", next_run_at=99.0, interval_seconds=604800.0)

    moved = adopt_scheduled_triggers(conn, owner=_OWNER, now=1.0)

    assert moved == 2
    rows = {r.name: r for r in list_schedules(conn, account_key=_OWNER)}
    assert set(rows) == {"legacy-one", "legacy-two"}
    assert rows["legacy-one"].cron is None
    assert rows["legacy-one"].interval_seconds == 30.0
    assert rows["legacy-one"].next_run_at == 42.0
    assert rows["legacy-two"].interval_seconds == 604800.0


@pytest.mark.unit
def test_adopt_scheduled_triggers_skips_one_shot_legacy_rows(tmp_path: Path) -> None:
    """A one-shot trigger (`interval_seconds IS NULL`) has no home in
    `schedules` — migrating it would land a row with neither `cron` nor
    `interval_seconds`, which `scheduling.next_schedule_run` treats as
    broken. It keeps firing (and getting deleted) through the untouched
    `due_triggers` loop instead; it just never gets a `schedules` row."""
    conn = open_store(tmp_path / "c.db")
    upsert_scheduled_trigger(conn, name="one-shot", trigger_text="go", next_run_at=42.0, interval_seconds=None)

    moved = adopt_scheduled_triggers(conn, owner=_OWNER, now=1.0)

    assert moved == 0
    assert list_schedules(conn, account_key=_OWNER) == ()


@pytest.mark.unit
def test_adopt_scheduled_triggers_is_idempotent_on_a_second_open(tmp_path: Path) -> None:
    conn = open_store(tmp_path / "c.db")
    upsert_scheduled_trigger(conn, name="legacy", trigger_text="go", next_run_at=42.0, interval_seconds=30.0)

    first = adopt_scheduled_triggers(conn, owner=_OWNER, now=1.0)
    second = adopt_scheduled_triggers(conn, owner=_OWNER, now=2.0)

    assert first == 1
    assert second == 0
    assert len(list_schedules(conn, account_key=_OWNER)) == 1
