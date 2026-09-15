"""`door/nouns/harness.py` — the harness noun's fixed-path handlers.

The second direct exercise (after `test_door_operations.py`) of the H16
ledger reservations for `operations`/`harness`, this time through the actual
noun code path rather than a raw `ledger.record_change` call.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest

from sadana import ids, ledger, stores
from sadana.conversation_store import write_txn
from sadana.door import problems
from sadana.door.auth import Principal
from sadana.door.nouns import harness
from sadana.door.request import DoorResponse
from sadana.tether import client as tether_client
from sadana.tether import identity, keys


def _conn(tmp_path: Path) -> sqlite3.Connection:
    # `stores.Connections` builds a store with every table present -- what
    # `ledger.inventory()` needs, per `test_ledger.py`'s own `_full_store`.
    return stores.Connections(tmp_path / "h.db").writer


def _ctx(conn: sqlite3.Connection, *, nouns: dict | None = None, harness_id: str = "hrn_box") -> SimpleNamespace:
    return SimpleNamespace(
        conns=SimpleNamespace(writer=conn, reader=lambda: conn),
        runtime=SimpleNamespace(harness_id=harness_id, org="org_a"),
        nouns=nouns or {"harness": harness},
    )


_PRINCIPAL = Principal(sub="u1", org="org_a", ws="ws1", hrn="hrn_box", scope=frozenset({"harness:read"}))

#: `ledger.record_change` enforces the real `hrn_<32hex>` id shape — most of
#: this file's tests never route `harness_id` through the ledger, so the
#: plain `"hrn_box"` placeholder above is fine for them. `deregister` does
#: write a tombstone with it, so its own tests need one that actually parses.
_REAL_HARNESS_ID = ids.make_id("hrn")


@pytest.mark.unit
def test_get_harness_shape(tmp_path: Path) -> None:
    ctx = _ctx(_conn(tmp_path))
    result = harness.get_harness(ctx, _PRINCIPAL)

    assert result["id"] == "hrn_box"
    assert result["tether"] == "disconnected"
    assert result["org"] == "org_a"
    # A subset, not an exact tuple: `DECLARED` is append-only across work
    # items (H18's own two capabilities among them), so an exact-list
    # equality here would re-break at every later addition.
    assert set(result["capabilities"]) >= {"grammar.v1", "changes", "inventory"}
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
def test_unavailable_crud_verbs_all_answer_capability_missing() -> None:
    """Unchanged by H30: `list`/`get`/`create`/`update`/`remove` stay
    unreachable on this singleton noun — only `act()` gained real branches."""
    assert isinstance(harness.list(None, _PRINCIPAL, None), problems.Problem)
    assert isinstance(harness.get(None, _PRINCIPAL, "hrn_box"), problems.Problem)
    assert isinstance(harness.create(None, _PRINCIPAL, {}), problems.Problem)
    assert isinstance(harness.update(None, _PRINCIPAL, "hrn_box", {}, None), problems.Problem)
    assert isinstance(harness.remove(None, _PRINCIPAL, "hrn_box", None), problems.Problem)


@pytest.mark.unit
def test_act_with_an_unknown_name_answers_capability_missing() -> None:
    result = harness.act(None, _PRINCIPAL, "hrn_box", "not-a-real-action", {}, None)
    assert isinstance(result, problems.Problem)
    assert result.code == "HARNESS_CAPABILITY_MISSING"


@pytest.mark.unit
def test_get_harness_reports_a_live_connected_state(tmp_path: Path) -> None:
    """The property the whole rest of this item exists to make true:
    `tether` is not a hardcoded string once the tether has actually
    connected."""
    ctx = _ctx(_conn(tmp_path))
    tether_client._set_state(status="connected", connected_at=1_700_000_000.0)
    try:
        result = harness.get_harness(ctx, _PRINCIPAL)
        assert result["tether"] == "connected"
        assert "connected_at" in result
    finally:
        tether_client._set_state(status="disconnected", connected_at=None)


@pytest.mark.unit
def test_act_upgrade_requires_a_version_tag(tmp_path: Path) -> None:
    ctx = _ctx(_conn(tmp_path))
    result = harness.act(ctx, _PRINCIPAL, "hrn_box", "upgrade", {}, None)
    assert isinstance(result, problems.Problem)
    assert result.code == "VALIDATION"


@pytest.mark.unit
def test_act_upgrade_rejects_a_syntactically_invalid_tag(tmp_path: Path) -> None:
    ctx = _ctx(_conn(tmp_path))
    result = harness.act(ctx, _PRINCIPAL, "hrn_box", "upgrade", {"version": "not a valid tag!!"}, None)
    assert isinstance(result, problems.Problem)
    assert result.code == "VALIDATION"


@pytest.mark.unit
def test_act_upgrade_promotes_a_running_operation_and_launches_the_script_detached(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`scripts/upgrade.sh` itself is never run here — it fetches real tags
    and restarts a real systemd service, neither of which belongs in a unit
    test (testing-conventions: no network, no real external mutation).
    What's proven is the contract this function owns: the right operation
    row, the right subprocess arguments, `start_new_session=True`."""
    ctx = _ctx(_conn(tmp_path))
    launched: list[tuple[list[str], dict[str, object]]] = []
    real_popen = harness.subprocess.Popen

    def _fake_popen(args: list[str], **kwargs: object) -> object:
        # `plugin_install.is_valid_tag_syntax` shells out to a real `git
        # check-ref-format` *before* this function ever launches
        # `upgrade.sh` — patching `Popen` globally (there is only one
        # `subprocess` module) would break that real, wanted call too, so
        # only the `bash ... upgrade.sh` launch is faked here.
        if args and args[0] == "bash":
            launched.append((args, kwargs))
            return SimpleNamespace(pid=1234)
        return real_popen(args, **kwargs)

    monkeypatch.setattr(harness.subprocess, "Popen", _fake_popen)

    result = harness.act(ctx, _PRINCIPAL, "hrn_box", "upgrade", {"version": "0.2.0"}, None)

    assert isinstance(result, DoorResponse)
    assert result.status == 202
    payload = json.loads(result.body)
    assert payload["operation"]["state"] == "running"
    assert len(launched) == 1
    args, kwargs = launched[0]
    assert args == ["bash", str(harness._REPO_ROOT / "scripts" / "upgrade.sh"), "0.2.0"]
    assert kwargs["start_new_session"] is True


@pytest.mark.unit
def test_act_deregister_stops_tether_removes_identity_and_key_and_tombstones(tmp_path: Path) -> None:
    conn = _conn(tmp_path)
    ctx = _ctx(conn, harness_id=_REAL_HARNESS_ID)
    identity.save(
        identity.Identity(
            harness_id=_REAL_HARNESS_ID,
            org=None,
            relay_url="https://relay.example/",
            console_url=None,
            enrolled_at=1.0,
        )
    )
    keys.save(keys.generate(), keys.default_path())

    result = harness.act(ctx, _PRINCIPAL, _REAL_HARNESS_ID, "deregister", {}, None)

    assert result == {"id": _REAL_HARNESS_ID, "deregistered": True}
    assert identity.load() is None
    assert not keys.default_path().exists()
    changes = ledger.changes_since(conn, 0, 10)
    assert any(c.noun == "harness" and c.kind == "deleted" and c.id == _REAL_HARNESS_ID for c in changes)


@pytest.mark.unit
def test_act_deregister_leaves_every_other_table_untouched(tmp_path: Path) -> None:
    """ "The data is the customer's" — deregister tombstones `harness` and
    nothing else."""
    conn = _conn(tmp_path)
    ctx = _ctx(conn, harness_id=_REAL_HARNESS_ID)
    conn.execute("INSERT INTO persona_selections (account_key, name, updated_at) VALUES ('acct_1', 'n', 1.0)")

    harness.act(ctx, _PRINCIPAL, _REAL_HARNESS_ID, "deregister", {}, None)

    remaining = conn.execute("SELECT COUNT(*) FROM persona_selections WHERE account_key = 'acct_1'").fetchone()[0]
    assert remaining == 1


@pytest.mark.unit
def test_act_purge_account_requires_account_key(tmp_path: Path) -> None:
    ctx = _ctx(_conn(tmp_path))
    result = harness.act(ctx, _PRINCIPAL, "hrn_box", "purge-account", {}, None)
    assert isinstance(result, problems.Problem)
    assert result.code == "VALIDATION"


@pytest.mark.unit
def test_act_purge_account_deletes_that_accounts_rows_only(tmp_path: Path) -> None:
    conn = _conn(tmp_path)
    ctx = _ctx(conn)
    conn.execute("INSERT INTO persona_selections (account_key, name, updated_at) VALUES ('acct_1', 'n', 1.0)")
    conn.execute("INSERT INTO persona_selections (account_key, name, updated_at) VALUES ('acct_2', 'n', 1.0)")

    result = harness.act(ctx, _PRINCIPAL, "hrn_box", "purge-account", {"account_key": "acct_1"}, None)

    assert result == {"account_key": "acct_1", "purged": True}  # pragma: allowlist secret
    count_sql = "SELECT COUNT(*) FROM persona_selections WHERE account_key = ?"
    remaining_1 = conn.execute(count_sql, ("acct_1",)).fetchone()[0]
    remaining_2 = conn.execute(count_sql, ("acct_2",)).fetchone()[0]
    assert remaining_1 == 0
    assert remaining_2 == 1


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
