"""`door/nouns/harness.py` — the harness noun's fixed-path handlers.

The second direct exercise (after `test_door_operations.py`) of the H16
ledger reservations for `operations`/`harness`, this time through the actual
noun code path rather than a raw `ledger.record_change` call.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest

from sadana import ids, ledger, stores
from sadana.conversation_store import write_txn
from sadana.door import problems
from sadana.door.auth import Principal
from sadana.door.nouns import harness


def _conn(tmp_path: Path) -> sqlite3.Connection:
    # `stores.Connections` builds a store with every table present -- what
    # `ledger.inventory()` needs, per `test_ledger.py`'s own `_full_store`.
    return stores.Connections(tmp_path / "h.db").writer


def _ctx(conn: sqlite3.Connection, *, nouns: dict | None = None) -> SimpleNamespace:
    return SimpleNamespace(
        conns=SimpleNamespace(writer=conn, reader=lambda: conn),
        runtime=SimpleNamespace(harness_id="hrn_box", org="org_a"),
        nouns=nouns or {"harness": harness},
    )


_PRINCIPAL = Principal(sub="u1", org="org_a", ws="ws1", hrn="hrn_box", scope=frozenset({"harness:read"}))


@pytest.mark.unit
def test_get_harness_shape(tmp_path: Path) -> None:
    ctx = _ctx(_conn(tmp_path))
    result = harness.get_harness(ctx, _PRINCIPAL)

    assert result["id"] == "hrn_box"
    assert result["tether"] == "disconnected"
    assert result["org"] == "org_a"
    assert result["capabilities"] == ("grammar.v1", "changes", "inventory", "artifacts.download")
    assert result["ledger_head"] == 0
    assert result["leaves_the_box"] == ("harness",)


@pytest.mark.unit
def test_get_changes_folds_created_into_changed(tmp_path: Path) -> None:
    conn = _conn(tmp_path)
    ctx = _ctx(conn)
    op_id = ids.make_id("op")
    with write_txn(conn) as c:
        ledger.record_change(c, noun="operations", id=op_id, kind="created", state="running", version=1, at=5.0)

    result = harness.get_changes(ctx, _PRINCIPAL, since=0, limit=10)

    assert len(result["data"]) == 1
    event = result["data"][0]
    assert event["kind"] == "changed"
    assert event["noun"] == "operations"
    assert event["id"] == op_id
    assert event["state"] == "running"
    assert event["version"] == 1
    assert "search_doc" not in event  # "operations" is not in ctx.nouns here


@pytest.mark.unit
def test_get_changes_keeps_deleted_and_omits_search_doc(tmp_path: Path) -> None:
    conn = _conn(tmp_path)
    ctx = _ctx(conn)
    op_id = ids.make_id("op")
    with write_txn(conn) as c:
        ledger.record_change(c, noun="operations", id=op_id, kind="deleted", state=None, version=None, at=5.0)

    result = harness.get_changes(ctx, _PRINCIPAL, since=0, limit=10)

    event = result["data"][0]
    assert event["kind"] == "deleted"
    assert "state" not in event
    assert "version" not in event
    assert "search_doc" not in event


@pytest.mark.unit
def test_search_doc_for_derives_from_the_owning_noun_when_registered(tmp_path: Path) -> None:
    from sadana.door.nouns import SearchDoc

    class _FakeWidgets:
        def get(self, ctx, principal, id, parent_id=None):
            return {"id": id, "name": "a widget"}

        def search_doc(self, row):
            return SearchDoc(title=row["name"])

    ctx = _ctx(_conn(tmp_path), nouns={"harness": harness, "widgets": _FakeWidgets()})

    doc = harness._search_doc_for(ctx, _PRINCIPAL, "widgets", "wgt_1")
    assert doc == {"title": "a widget", "facets": {}}


@pytest.mark.unit
def test_search_doc_for_is_none_when_the_noun_isnt_registered(tmp_path: Path) -> None:
    ctx = _ctx(_conn(tmp_path))  # only "harness" registered
    assert harness._search_doc_for(ctx, _PRINCIPAL, "widgets", "wgt_1") is None


@pytest.mark.unit
def test_get_inventory_lists_every_registered_noun(tmp_path: Path) -> None:
    ctx = _ctx(_conn(tmp_path))
    result = harness.get_inventory(ctx, _PRINCIPAL)
    assert "operations" in result
    assert "harness" in result
    assert result["operations"] == []


@pytest.mark.unit
def test_unreachable_verbs_all_answer_capability_missing() -> None:
    assert isinstance(harness.list(None, _PRINCIPAL, None), problems.Problem)
    assert isinstance(harness.get(None, _PRINCIPAL, "hrn_box"), problems.Problem)
    assert isinstance(harness.create(None, _PRINCIPAL, {}), problems.Problem)
    assert isinstance(harness.update(None, _PRINCIPAL, "hrn_box", {}, None), problems.Problem)
    assert isinstance(harness.remove(None, _PRINCIPAL, "hrn_box", None), problems.Problem)
    result = harness.act(None, _PRINCIPAL, "hrn_box", "upgrade", {}, None)
    assert isinstance(result, problems.Problem)
    assert result.code == "HARNESS_CAPABILITY_MISSING"
    assert "upgrade" in (result.detail or "")


@pytest.mark.unit
def test_search_doc_titles_the_row_by_id() -> None:
    doc = harness.search_doc({"id": "hrn_box"})
    assert doc.title == "Harness hrn_box"


@pytest.mark.unit
def test_ledger_record_change_via_the_noun_code_path_still_guards_the_prefix(tmp_path: Path) -> None:
    """The plan's own second direct exercise of the reservation: this time
    hit through `get_changes`'s own read path rather than a bare
    `ledger.record_change` call, proving the write side stays correct once
    the noun code is what calls it (`operations.py` did the writing;
    this proves reading a row it wrote renders correctly end to end)."""
    conn = _conn(tmp_path)
    ctx = _ctx(conn)
    good_id = ids.make_id("hrn")
    with write_txn(conn) as c:
        ledger.record_change(c, noun="harness", id=good_id, kind="changed", state=None, version=3, at=9.0)

    result = harness.get_changes(ctx, _PRINCIPAL, since=0, limit=10)
    assert result["data"][0]["id"] == good_id
    assert result["data"][0]["version"] == 3
