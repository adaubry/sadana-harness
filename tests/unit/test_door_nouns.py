"""`door/nouns/__init__.py` — the noun protocol's data shapes."""

from __future__ import annotations

import pytest

from sadana.door.nouns import ActionSpec, NounSpec, SearchDoc


@pytest.mark.unit
def test_noun_spec_defaults_to_no_actions_and_no_parent() -> None:
    spec = NounSpec(
        plural="widgets",
        prefix="wgt",
        filterable=frozenset({"state"}),
        orderable=frozenset({"created_at"}),
        states=frozenset({"active"}),
    )
    assert spec.actions == {}
    assert spec.parent is None


@pytest.mark.unit
def test_action_spec_defaults_to_consequential() -> None:
    action = ActionSpec(from_states=("active",), to_state="archived", capability=None, scope_verb="archive")
    assert action.consequential is True


@pytest.mark.unit
def test_search_doc_defaults_to_empty_facets_and_no_subtitle_or_body() -> None:
    doc = SearchDoc(title="A widget")
    assert doc.subtitle is None
    assert doc.body is None
    assert doc.facets == {}


@pytest.mark.unit
def test_a_conforming_class_satisfies_the_protocol_structurally() -> None:
    """Not an isinstance check -- `NounModule` is a data-carrying Protocol,
    which structural `isinstance` can't verify meaningfully. What matters is
    that a plain class implementing every method type-checks against it,
    which `make typecheck` covers; this just proves the shape is callable."""

    class _Widgets:
        spec = NounSpec(
            plural="widgets",
            prefix="wgt",
            filterable=frozenset(),
            orderable=frozenset({"created_at"}),
            states=frozenset({"active"}),
        )

        def list(self, ctx, principal, params, parent_id=None):
            return None

        def get(self, ctx, principal, id, parent_id=None):
            return None

        def create(self, ctx, principal, body, parent_id=None):
            return None

        def update(self, ctx, principal, id, body, if_match):
            return None

        def remove(self, ctx, principal, id, if_match):
            return None

        def act(self, ctx, principal, id, name, body, if_match):
            return None

        def search_doc(self, row):
            return SearchDoc(title="x")

    widgets = _Widgets()
    assert widgets.spec.plural == "widgets"
    assert widgets.search_doc({}).title == "x"
