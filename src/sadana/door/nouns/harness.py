"""The `harness` noun: the box's own status, the change feed, inventory,
and its three remote lifecycle actions.

`docs/tasks/H19-door-framework-token-conformance/spec.md` requirements 37-40;
`docs/tasks/H30-tether-enroll-frames-lifecycle/spec.md` for `upgrade`,
`deregister` and `purge-account`. `harness` is the odd one out among nouns:
it is a singleton (`GET /v1/harness`, never `GET /v1/harnesses`), and its
three read paths and one action path are the "fixed" paths requirement 1
names — `router.py` dispatches to the functions below directly rather than
through the generic `{plural}`/`{plural}/{id}` grammar, so `list`/`create`/
`update`/`remove` below exist only to satisfy `nouns.NounModule`'s shape
uniformly (so `ctx.nouns["harness"]` type-checks like every other noun); none
of the four is ever reachable through this work item's own routing.
"""

from __future__ import annotations

import subprocess
import time
from collections.abc import Mapping, Sequence
from pathlib import Path

from sadana import __version__, config, ledger, plugin_install, stores
from sadana.conversation_store import write_txn
from sadana.door import capabilities, grammar, problems
from sadana.door.auth import Principal
from sadana.door.nouns import ActionSpec, NounSpec, SearchDoc, unavailable
from sadana.door.operations import start_operation
from sadana.door.request import json_response
from sadana.tether import client as tether_client
from sadana.tether import identity as tether_identity
from sadana.tether import keys as tether_keys

#: `.../src/sadana/door/nouns/harness.py` -> the repo root `scripts/upgrade.sh`
#: lives under — the same "find my own checkout" shape `gateway_service.py`'s
#: own `_src_dir()` already uses for a sibling problem, kept local here
#: rather than reaching into that module's private helper for one line.
_REPO_ROOT = Path(__file__).resolve().parents[4]

spec = NounSpec(
    plural="harness",
    prefix="hrn",
    filterable=frozenset(),
    orderable=frozenset(),
    states=frozenset(),
    actions={
        "upgrade": ActionSpec(from_states=(), to_state="", capability="upgrade", scope_verb="upgrade"),
        # Neither is in `capabilities.ALL` — the closed sixteen has no slot
        # for either, so both are scope-gated only, never capability-gated
        # (`capability=None`, matching `agents.py`'s own precedent for an
        # action with nothing to turn on or off).
        "deregister": ActionSpec(from_states=(), to_state="", capability=None, scope_verb="deregister"),
        "purge-account": ActionSpec(from_states=(), to_state="", capability=None, scope_verb="purge"),
    },
    parent=None,
)


def get_harness(ctx: object, principal: Principal) -> dict[str, object]:
    """`GET /v1/harness`. `ctx` is typed `object` here rather than
    `router.DoorContext` to avoid this leaf importing the module that
    imports it (`router.py`) -- the attributes used (`runtime`, `conns`,
    `nouns`) are exactly `DoorContext`'s own, checked by `make typecheck`
    once `router.py` exists and calls this with a real one.

    `tether` reads `tether.client.state()` live (H30) — a plain function
    call, never threaded through `DoorContext`, so a box that has never
    started a tether (every existing unit test's fake `ctx`) still reads
    the same `"disconnected"` default it always did."""
    reader = ctx.conns.reader()  # type: ignore[attr-defined]
    tether_state = tether_client.state()
    result: dict[str, object] = {
        "id": ctx.runtime.harness_id,  # type: ignore[attr-defined]
        "version": __version__,
        "capabilities": capabilities.declared(),
        "tether": tether_state.status,
        "org": ctx.runtime.org,  # type: ignore[attr-defined]
        "ledger_head": ledger.ledger_head(reader),
        "leaves_the_box": tuple(ctx.nouns),  # type: ignore[attr-defined]
        # H14: a sibling field, not a rename of `leaves_the_box` or a type
        # change of `tether` — the latter is H30's, mid-flight on this same
        # response shape. Read live: the console's own per-tenant mirror
        # setting is authoritative, this is only the box's own floor.
        "mirror": config.get("tether.mirror", "full"),
    }
    if tether_state.connected_at is not None:
        result["connected_at"] = grammar.render_ts(tether_state.connected_at)
    return result


def get_changes(ctx: object, principal: Principal, *, since: int, limit: int) -> dict[str, object]:
    """`GET /v1/changes`. Folds the ledger's three-valued `kind`
    (`created`/`changed`/`deleted`) into the console's two-valued one
    (spec.md requirement 32) and derives `search_doc` at render time by
    re-fetching the current row through `ctx.nouns` (requirement 33) --
    never stored on the ledger row itself."""
    reader = ctx.conns.reader()  # type: ignore[attr-defined]
    rows = ledger.changes_since(reader, since, limit)
    events = []
    for row in rows:
        event: dict[str, object] = {
            "kind": "deleted" if row.kind == "deleted" else "changed",
            "harness_id": ctx.runtime.harness_id,  # type: ignore[attr-defined]
            "noun": row.noun,
            "id": row.id,
            "updated_at": grammar.render_ts(row.at),
        }
        if row.state is not None:
            event["state"] = row.state
        if row.version is not None:
            event["version"] = row.version
        if row.kind != "deleted":
            doc = _search_doc_for(ctx, principal, row.noun, row.id)
            if doc is not None:
                event["search_doc"] = doc
        events.append(event)
    next_cursor = rows[-1].cursor if rows else since
    return {"data": events, "next_cursor": next_cursor}


def _search_doc_for(ctx: object, principal: Principal, noun: str, id: str) -> dict[str, object] | None:
    module = ctx.nouns.get(noun)  # type: ignore[attr-defined]
    if module is None:
        return None
    current = module.get(ctx, principal, id)
    if isinstance(current, problems.Problem) or not isinstance(current, Mapping):
        return None
    doc = module.search_doc(current)
    rendered: dict[str, object] = {"title": doc.title, "facets": dict(doc.facets)}
    if doc.subtitle is not None:
        rendered["subtitle"] = doc.subtitle
    if doc.body is not None:
        rendered["body"] = doc.body
    return rendered


def get_inventory(ctx: object, principal: Principal) -> dict[str, Sequence[dict[str, object]]]:
    """`GET /v1/inventory`."""
    reader = ctx.conns.reader()  # type: ignore[attr-defined]
    inventory = ledger.inventory(reader)
    return {
        noun: [{"id": r.id, "updated_at": grammar.render_ts(r.updated_at), "version": r.version} for r in rows]
        for noun, rows in inventory.items()
    }


# ── the rest of `NounModule`'s shape, unreachable in H19 (see module docstring) ──
def list(ctx: object, principal: Principal, params: object, parent_id: str | None = None) -> problems.Problem:
    return unavailable("harness", "list")


def get(ctx: object, principal: Principal, id: str, parent_id: str | None = None) -> problems.Problem:
    return unavailable("harness", "get")


def create(
    ctx: object, principal: Principal, body: Mapping[str, object], parent_id: str | None = None
) -> problems.Problem:
    return unavailable("harness", "create")


def update(
    ctx: object, principal: Principal, id: str, body: Mapping[str, object], if_match: str | None
) -> problems.Problem:
    return unavailable("harness", "update")


def remove(ctx: object, principal: Principal, id: str, if_match: str | None) -> problems.Problem:
    return unavailable("harness", "remove")


def act(
    ctx: object, principal: Principal, id: str, name: str, body: Mapping[str, object], if_match: str | None
) -> problems.Problem | Mapping[str, object]:
    if name == "upgrade":
        return _upgrade(ctx, body)
    if name == "deregister":
        return _deregister(ctx)
    if name == "purge-account":
        return _purge_account(ctx, body)
    return unavailable("harness", name)


def _upgrade(ctx: object, body: Mapping[str, object]) -> problems.Problem | Mapping[str, object]:
    """Validates the tag with the same check `plugin_install.install()`
    uses, writes the operation `running` with the target already recorded
    (`door/operations.resume_on_start`'s own H30 branch resolves it on the
    *next* boot), then launches `scripts/upgrade.sh` detached —
    `start_new_session=True` so it outlives the process it is about to
    restart. Returns the promoted operation directly as a `202`
    `DoorResponse`: `router.py`'s own promotion path only fires after a
    slow call's timeout, and this is deliberately never slow — it writes
    one row and starts one subprocess, both fast — so a manually-built
    `DoorResponse` is what actually gets a `202` out, the same seam
    `artifacts.download` already uses for a non-JSON result."""
    tag = body.get("version")
    if not isinstance(tag, str) or not tag:
        return problems.make("VALIDATION", "upgrade requires a 'version' tag")
    if not plugin_install.is_valid_tag_syntax(tag):
        return problems.make("VALIDATION", f"{tag!r} is not a valid tag name")

    op = start_operation(ctx.conns, resume_target_version=tag)  # type: ignore[attr-defined]
    log_path = config.get_paths().state_dir / "tether" / "upgrade.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with open(log_path, "ab") as log_file:
        subprocess.Popen(
            ["bash", str(_REPO_ROOT / "scripts" / "upgrade.sh"), tag],
            stdout=log_file,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    return json_response(202, {"operation": op.to_wire()})  # type: ignore[return-value]


def _deregister(ctx: object) -> Mapping[str, object]:
    """Stops the tether, deletes the local identity and private key, and
    tombstones the `harness` noun in the ledger. Every other table is
    untouched — stated here because it is a promise, not an implementation
    detail: the data this box holds is the customer's, deregistering does
    not erase it."""
    harness_id = ctx.runtime.harness_id  # type: ignore[attr-defined]
    tether_client.stop()
    tether_identity.remove()
    tether_keys.default_path().unlink(missing_ok=True)
    now = time.time()
    with write_txn(ctx.conns.writer) as c:  # type: ignore[attr-defined]
        ledger.record_change(c, noun="harness", id=harness_id, kind="deleted", state=None, version=None, at=now)
    return {"id": harness_id, "deregistered": True}


def _purge_account(ctx: object, body: Mapping[str, object]) -> problems.Problem | Mapping[str, object]:
    """`stores.purge_account` does the actual deleting, against
    `stores.PURGE_STEPS` — every table with an `account_key` column,
    mechanically enumerated and tested for completeness in
    `tests/unit/test_stores_purge.py`."""
    account_key = body.get("account_key")
    if not isinstance(account_key, str) or not account_key:
        return problems.make("VALIDATION", "purge-account requires an 'account_key'")
    stores.purge_account(ctx.conns, account_key)  # type: ignore[attr-defined]
    return {"account_key": account_key, "purged": True}


def search_doc(row: Mapping[str, object]) -> SearchDoc:
    return SearchDoc(title=f"Harness {row.get('id', '')}")
