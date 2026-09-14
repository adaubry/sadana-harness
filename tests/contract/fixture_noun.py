"""A minimal in-memory `widgets` noun, registered by the conformance test
(and this work item's own first router test) so `router.py`'s framework is
proven against something that is not `harness` — a real state machine, real
optimistic concurrency (`If-Match`/`version`), a real filterable/orderable
field set, and real per-account visibility.

Per-instance storage, never a module global: each test constructs its own
`WidgetsNoun()`, so no reset fixture is needed and no test can leak a widget
into another's.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping

from sadana.door import grammar, problems
from sadana.door.auth import Principal
from sadana.door.nouns import ActionSpec, NounSpec, SearchDoc
from sadana.ids import uuid7


def _make_widget_id() -> str:
    """`wgt_<uuidv7 hex>`, in the real shape, without registering `wgt` in
    `ids.PREFIXES` — that closed registry is for real nouns, and a test
    fixture is deliberately not one (`ids.uuid7` is the pure generator
    underneath `ids.make_id`, with no registry check attached)."""
    return "wgt_" + uuid7().replace("-", "")


class WidgetsNoun:
    spec = NounSpec(
        plural="widgets",
        prefix="wgt",
        filterable=frozenset({"name", "state"}),
        orderable=frozenset({"created_at", "name"}),
        states=frozenset({"draft", "active", "archived"}),
        actions={
            "archive": ActionSpec(from_states=("active",), to_state="archived", capability=None, scope_verb="archive"),
        },
        parent=None,
    )

    def __init__(
        self, *, harness_id: str = "hrn_test", clock: Callable[[], float] = time.time, delay_seconds: float = 0.0
    ) -> None:
        self._rows: dict[str, dict[str, object]] = {}
        self._owners: dict[str, str] = {}
        self._harness_id = harness_id
        self._clock = clock
        #: Only for the conformance test's operation-promotion case: a create
        #: slow enough to cross the router's timeout. Zero in every other
        #: test, which is the overwhelming common case this noun serves.
        self._delay_seconds = delay_seconds

    def _visible(self, principal: Principal, id: str) -> dict[str, object] | None:
        if self._owners.get(id) != principal.sub:
            return None
        return self._rows.get(id)

    def list(
        self, ctx: object, principal: Principal, params: grammar.ListParams, parent_id: str | None = None
    ) -> grammar.ListResponse:
        rows = [row for wid, row in self._rows.items() if self._owners.get(wid) == principal.sub]
        return grammar.page(rows, params)

    def get(
        self, ctx: object, principal: Principal, id: str, parent_id: str | None = None
    ) -> dict[str, object] | problems.Problem:
        row = self._visible(principal, id)
        if row is None:
            return problems.make("NOT_FOUND", f"no widget {id}")
        return dict(row)

    def create(
        self, ctx: object, principal: Principal, body: Mapping[str, object], parent_id: str | None = None
    ) -> dict[str, object]:
        if self._delay_seconds:
            time.sleep(self._delay_seconds)
        widget_id = _make_widget_id()
        now = grammar.render_ts(self._clock())
        row: dict[str, object] = {
            "id": widget_id,
            "created_at": now,
            "updated_at": now,
            "state": "draft",
            "tags": {},
            "harness_id": self._harness_id,
            "version": 1,
            "name": body.get("name", ""),
        }
        self._rows[widget_id] = row
        self._owners[widget_id] = principal.sub
        return dict(row)

    def update(
        self, ctx: object, principal: Principal, id: str, body: Mapping[str, object], if_match: str | None
    ) -> dict[str, object] | problems.Problem:
        row = self._visible(principal, id)
        if row is None:
            return problems.make("NOT_FOUND", f"no widget {id}")
        precondition = self._check_if_match(row, if_match)
        if precondition is not None:
            return precondition
        updated = dict(row)
        if "name" in body:
            updated["name"] = body["name"]
        updated["version"] = row["version"] + 1  # type: ignore[operator]
        updated["updated_at"] = grammar.render_ts(self._clock())
        self._rows[id] = updated
        return dict(updated)

    def remove(self, ctx: object, principal: Principal, id: str, if_match: str | None) -> None | problems.Problem:
        row = self._visible(principal, id)
        if row is None:
            return problems.make("NOT_FOUND", f"no widget {id}")
        precondition = self._check_if_match(row, if_match)
        if precondition is not None:
            return precondition
        del self._rows[id]
        del self._owners[id]
        return None

    def act(
        self,
        ctx: object,
        principal: Principal,
        id: str,
        name: str,
        body: Mapping[str, object],
        if_match: str | None,
    ) -> dict[str, object] | problems.Problem:
        row = self._visible(principal, id)
        if row is None:
            return problems.make("NOT_FOUND", f"no widget {id}")
        if name != "archive":
            return problems.make("HARNESS_CAPABILITY_MISSING", f"widgets.{name} is not available on this harness")
        precondition = self._check_if_match(row, if_match)
        if precondition is not None:
            return precondition
        updated = dict(row)
        updated["state"] = "archived"
        updated["version"] = row["version"] + 1  # type: ignore[operator]
        updated["updated_at"] = grammar.render_ts(self._clock())
        self._rows[id] = updated
        return dict(updated)

    def search_doc(self, row: Mapping[str, object]) -> SearchDoc:
        return SearchDoc(title=str(row.get("name", "")), facets={"state": row.get("state")})

    @staticmethod
    def _check_if_match(row: Mapping[str, object], if_match: str | None) -> problems.Problem | None:
        if if_match is None:
            return problems.make("PRECONDITION_FAILED", "If-Match is required for this request")
        if if_match != str(row["version"]):
            return problems.make("PRECONDITION_FAILED", f"version is {row['version']}, If-Match named {if_match}")
        return None
