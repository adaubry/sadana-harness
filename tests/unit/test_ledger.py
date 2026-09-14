"""`ledger.py` — the ordered change feed and the inventory.

The writer half is tested against a bare connection. The inventory half needs
every store's tables to exist, so it lives at the bottom of this file and
opens a real store through `stores.ensure_schemas` — which is what makes the
last test here the mitigation `spec.md` § Concerns names.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from conftest import conversation as build_conversation
from conftest import write_character
from sadana import conversation_store, ids, ledger, memory_store, persona_store, stores
from sadana.conversation_store import write_txn


def _conn(tmp_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(tmp_path / "l.db", isolation_level=None)
    conn.row_factory = sqlite3.Row
    ledger.ensure_schema(conn)
    return conn


def _record(
    conn: sqlite3.Connection, *, noun: str = "conversations", id: str | None = None, kind: str = "created"
) -> str:
    """Record one change and return the id it used. Real ids, not `"conv_x"`:
    `record_change` checks the prefix against the noun."""
    id = id or ids.make_id("conv")
    with write_txn(conn) as c:
        ledger.record_change(c, noun=noun, id=id, kind=kind, state="active", version=1, at=1.0)
    return id


@pytest.mark.unit
def test_a_committed_change_leaves_exactly_one_row(tmp_path: Path) -> None:
    conn = _conn(tmp_path)

    _record(conn)

    assert len(ledger.changes_since(conn, 0, 10)) == 1


@pytest.mark.unit
def test_a_rolled_back_write_leaves_no_change_behind(tmp_path: Path) -> None:
    """The whole point of taking a connection instead of opening a
    transaction: the change row cannot outlive the change it describes."""
    conn = _conn(tmp_path)

    with pytest.raises(RuntimeError), write_txn(conn) as c:
        ledger.record_change(
            c, noun="conversations", id=ids.make_id("conv"), kind="created", state=None, version=1, at=1.0
        )
        raise RuntimeError("the write failed after the ledger row was written")

    assert ledger.changes_since(conn, 0, 10) == ()
    assert ledger.ledger_head(conn) == 0


@pytest.mark.unit
def test_changes_come_back_in_cursor_order_and_page(tmp_path: Path) -> None:
    conn = _conn(tmp_path)
    recorded = [_record(conn) for _ in range(5)]

    first = ledger.changes_since(conn, 0, 2)
    second = ledger.changes_since(conn, first[-1].cursor, 2)

    assert [c.id for c in first] == recorded[:2]
    assert [c.id for c in second] == recorded[2:4]
    assert [c.cursor for c in first + second] == sorted(c.cursor for c in first + second)


@pytest.mark.unit
def test_changes_since_is_strictly_after_the_cursor_given(tmp_path: Path) -> None:
    """A caller passes back the last cursor it applied; handing that change
    over a second time would make every mirror apply it twice."""
    conn = _conn(tmp_path)
    _record(conn)
    second = _record(conn)

    head_of_first = ledger.changes_since(conn, 0, 1)[0].cursor

    assert [c.id for c in ledger.changes_since(conn, head_of_first, 10)] == [second]


@pytest.mark.unit
def test_ledger_head_is_zero_on_an_empty_ledger(tmp_path: Path) -> None:
    """Zero, not `None`, because zero is also what `changes_since` takes to
    mean "from the beginning" — one value, one meaning."""
    assert ledger.ledger_head(_conn(tmp_path)) == 0


@pytest.mark.unit
def test_ledger_head_tracks_the_newest_cursor(tmp_path: Path) -> None:
    conn = _conn(tmp_path)
    _record(conn)
    _record(conn)

    assert ledger.ledger_head(conn) == ledger.changes_since(conn, 0, 10)[-1].cursor


@pytest.mark.unit
def test_an_unknown_noun_or_kind_is_refused(tmp_path: Path) -> None:
    conn = _conn(tmp_path)

    good = ids.make_id("conv")
    with pytest.raises(ValueError), write_txn(conn) as c:
        ledger.record_change(c, noun="conversation", id=good, kind="created", state=None, version=1, at=1.0)
    with pytest.raises(ValueError), write_txn(conn) as c:
        ledger.record_change(c, noun="conversations", id=good, kind="updated", state=None, version=1, at=1.0)


@pytest.mark.unit
def test_an_id_whose_prefix_does_not_match_the_noun_is_refused(tmp_path: Path) -> None:
    """What makes `_Noun.prefix` load-bearing rather than decorative. A
    conversation's id filed under `messages` would name something that can
    never appear under that noun in `inventory()`, and a mirror following the
    feed would chase it forever."""
    conn = _conn(tmp_path)

    with pytest.raises(ValueError), write_txn(conn) as c:
        ledger.record_change(c, noun="messages", id=ids.make_id("conv"), kind="created", state=None, version=1, at=1.0)


@pytest.mark.unit
def test_every_noun_has_a_registered_id_prefix() -> None:
    """A contract between two closed lists. The module already raises on
    import if this is false; asserting it here is what names the rule when
    somebody adds a fourteenth noun."""
    assert all(spec.prefix in ids.PREFIXES for spec in ledger._NOUNS.values())


# ── the inventory, which needs every store's tables ────────────────────────


def _full_store(tmp_path: Path) -> sqlite3.Connection:
    """A store with every table present — which is exactly what
    `stores.Connections` builds, so it builds it."""
    return stores.Connections(tmp_path / "c.db").writer


@pytest.mark.unit
def test_every_nouns_inventory_query_actually_runs(tmp_path: Path) -> None:
    """The mitigation `spec.md` § Concerns names.

    `_NOUNS` holds SQL naming tables and columns that five other modules own,
    and `ledger.py` is forbidden from importing them to check (requirement
    19). A table or column renamed over there breaks `inventory()` at runtime,
    in a module that did not change and with no type checker to see it. This
    runs every query against a store with every schema ensured, so that rename
    fails here, the next time the suite runs, instead of in front of a
    console.
    """
    result = ledger.inventory(_full_store(tmp_path))

    assert set(result) == set(ledger.NOUNS)


@pytest.mark.unit
def test_a_noun_nothing_writes_yet_inventories_as_nothing(tmp_path: Path) -> None:
    """`traces`, `schedules`, `approvals`, `operations` and `harness` are
    registered now and written by H20, H27, H18 and H19. They are keys with
    empty values, never missing keys, so a caller iterating the result needs no
    second list of which nouns are real."""
    result = ledger.inventory(_full_store(tmp_path))

    assert result["schedules"] == ()
    assert result["approvals"] == ()
    assert result["operations"] == ()
    assert result["traces"] == ()
    assert result["harness"] == ()


@pytest.mark.unit
def test_the_inventory_lists_what_was_actually_written(tmp_path: Path) -> None:
    conn = _full_store(tmp_path)
    conversation_store.create(
        conn,
        build_conversation(key="k1"),
        now=0.0,
        account_key="local",  # pragma: allowlist secret
    )
    memory_store.write_entry(conn, "local", "dog_name", "Buddy", now=1.0)

    result = ledger.inventory(conn)

    assert [row.id for row in result["conversations"]] == [
        conn.execute("SELECT id FROM conversations").fetchone()["id"]
    ]
    assert [(row.version, row.updated_at) for row in result["memory_entries"]] == [(1, 1.0)]


@pytest.mark.unit
def test_a_nouns_inventory_unions_every_table_behind_it(tmp_path: Path) -> None:
    """Three nouns are backed by more than one table. What matters is that
    every id the ledger mentions resolves against the inventory — so the union
    is the property, not an implementation detail (`spec.md` § Open
    questions records that a console may want them split)."""
    conn = _full_store(tmp_path)
    persona_store.set_selection(conn, "local", "reviewer", now=1.0)
    write_character("reviewer", characters_dir=tmp_path / "characters")
    persona_store.reconcile_agents(conn, tmp_path / "characters")

    inventoried = {row.id for row in ledger.inventory(conn)["agents"]}
    mentioned = {c.id for c in ledger.changes_since(conn, 0, 50) if c.noun == "agents"}

    assert len(inventoried) == 2
    assert mentioned <= inventoried
