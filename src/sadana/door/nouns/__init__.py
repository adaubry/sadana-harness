"""The noun protocol: what a noun module is, and what it must implement.

`docs/tasks/H19-door-framework-token-conformance/spec.md` requirement 43. A
noun module never imports another noun module — `router.py` is the only
module that imports every `nouns/*.py`, and this file (the shared protocol
every one of them structurally satisfies) is the one thing under `nouns/`
that isn't itself a noun.

`DoorContext`, `Principal`, `ListParams`/`ListResponse` and `Problem` are
imported only under `TYPE_CHECKING`: at runtime, `router.py` imports this
module (for `NounSpec`/`NounModule`), so a real import the other way would
be the exact cycle `CLAUDE.md`'s acyclic-leaf rule exists to prevent. A
`Protocol` is structural — a noun module satisfies it by shape, never by
registering itself, which is also why this is a `Protocol` and not an ABC
with a registration step.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Protocol

from sadana.door import problems

if TYPE_CHECKING:
    from sadana.door.auth import Principal
    from sadana.door.grammar import ListParams, ListResponse
    from sadana.door.problems import Problem
    from sadana.door.router import DoorContext


@dataclass(frozen=True)
class ActionSpec:
    from_states: tuple[str, ...]
    to_state: str
    capability: str | None
    scope_verb: str
    consequential: bool = True


@dataclass(frozen=True)
class NounSpec:
    plural: str
    prefix: str
    filterable: frozenset[str]
    orderable: frozenset[str]
    states: frozenset[str]
    actions: Mapping[str, ActionSpec] = field(default_factory=dict)
    parent: str | None = None


@dataclass(frozen=True)
class SearchDoc:
    title: str
    subtitle: str | None = None
    body: str | None = None
    facets: Mapping[str, object] = field(default_factory=dict)


class NounModule(Protocol):
    spec: NounSpec

    def list(
        self, ctx: DoorContext, principal: Principal, params: ListParams, parent_id: str | None = None
    ) -> ListResponse | Problem: ...

    def get(
        self, ctx: DoorContext, principal: Principal, id: str, parent_id: str | None = None
    ) -> Mapping[str, object] | Problem: ...

    def create(
        self, ctx: DoorContext, principal: Principal, body: Mapping[str, object], parent_id: str | None = None
    ) -> Mapping[str, object] | Problem: ...

    def update(
        self, ctx: DoorContext, principal: Principal, id: str, body: Mapping[str, object], if_match: str | None
    ) -> Mapping[str, object] | Problem: ...

    def remove(self, ctx: DoorContext, principal: Principal, id: str, if_match: str | None) -> None | Problem: ...

    def act(
        self,
        ctx: DoorContext,
        principal: Principal,
        id: str,
        name: str,
        body: Mapping[str, object],
        if_match: str | None,
    ) -> Mapping[str, object] | Problem: ...

    def search_doc(self, row: Mapping[str, object]) -> SearchDoc: ...


def unavailable(plural: str, verb: str) -> problems.Problem:
    """The shared `HARNESS_CAPABILITY_MISSING` shape for a noun that answers
    none of the generic CRUD verbs itself — `harness.py`'s own six stubs use
    this, and any later fixed-path-only noun gets the same fallback instead
    of re-typing the string template."""
    return problems.make("HARNESS_CAPABILITY_MISSING", f"{plural}.{verb} is not available on this harness")
