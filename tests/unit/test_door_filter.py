"""`door/filter.py` — the console's filter grammar."""

from __future__ import annotations

import pytest

from sadana.door import filter as door_filter


def _ev(expr: str, row: dict) -> bool:
    return door_filter.evaluate(door_filter.parse(expr), row)


@pytest.mark.unit
@pytest.mark.parametrize(
    "expr,row,expected",
    [
        ('state = "active"', {"state": "active"}, True),
        ('state = "active"', {"state": "paused"}, False),
        ('state != "active"', {"state": "paused"}, True),
        ("count < 5", {"count": 3}, True),
        ("count <= 5", {"count": 5}, True),
        ("count > 5", {"count": 3}, False),
        ("count >= 5", {"count": 5}, True),
        ('name : "wid"', {"name": "widget"}, True),
        ('name : "zzz"', {"name": "widget"}, False),
        ("active = true", {"active": True}, True),
        ("active = false", {"active": True}, False),
    ],
)
def test_every_operator(expr: str, row: dict, expected: bool) -> None:
    assert _ev(expr, row) is expected


@pytest.mark.unit
def test_and_or_not_precedence_is_not_over_and_over_or() -> None:
    """`NOT > AND > OR`: `a OR b AND NOT c` parses as `a OR (b AND (NOT c))`."""
    row_a_only = {"a": True, "b": False, "c": True}
    row_b_and_not_c = {"a": False, "b": True, "c": False}
    row_b_and_c = {"a": False, "b": True, "c": True}

    expr = "a = true OR b = true AND NOT c = true"
    assert _ev(expr, row_a_only) is True
    assert _ev(expr, row_b_and_not_c) is True
    assert _ev(expr, row_b_and_c) is False


@pytest.mark.unit
def test_parentheses_override_precedence() -> None:
    row = {"a": True, "b": False, "c": False}
    assert _ev("(a = true OR b = true) AND c = true", row) is False
    assert _ev("a = true AND (b = true OR c = true)", {"a": True, "b": False, "c": True}) is True


@pytest.mark.unit
def test_dotted_field_reads_a_nested_object() -> None:
    row = {"tags": {"team": "infra"}}
    assert _ev('tags.team = "infra"', row) is True
    assert _ev('tags.team = "other"', row) is False


@pytest.mark.unit
def test_quoted_string_escapes() -> None:
    row = {"name": 'say "hi"'}
    assert _ev(r'name = "say \"hi\""', row) is True


@pytest.mark.unit
def test_number_literal_types() -> None:
    assert _ev("count = 5", {"count": 5}) is True
    assert _ev("ratio = 0.5", {"ratio": 0.5}) is True


@pytest.mark.unit
def test_date_literal_compares_numerically_not_lexicographically() -> None:
    """A bare-date literal and a full millisecond `Z` row value must compare
    by instant, not by string length — a naive lexicographic compare gets
    this backwards for some inputs."""
    row = {"created_at": "2026-01-02T00:00:00.000Z"}
    assert _ev("created_at > 2026-01-01", row) is True
    assert _ev("created_at < 2026-01-01", row) is False
    assert _ev("created_at = 2026-01-02T00:00:00.000Z", row) is True


@pytest.mark.unit
def test_fields_collects_every_referenced_field() -> None:
    ast = door_filter.parse('state = "active" AND (count > 1 OR name : "x")')
    assert door_filter.fields(ast) == {"state", "count", "name"}


@pytest.mark.unit
@pytest.mark.parametrize(
    "bad_expr",
    [
        'state = "unterminated',
        "state = = 1",
        "(state = 1",
    ],
)
def test_three_malformed_inputs_raise(bad_expr: str) -> None:
    with pytest.raises(door_filter._SyntaxError):
        door_filter.parse(bad_expr)


@pytest.mark.unit
def test_contains_operator_rejects_a_non_string_value() -> None:
    with pytest.raises(door_filter._SyntaxError):
        door_filter.parse("count : 5")


@pytest.mark.unit
def test_unresolvable_dotted_field_evaluates_false_not_raise() -> None:
    assert _ev('tags.team = "infra"', {"tags": "not-an-object"}) is False
    assert _ev('missing.field = "x"', {}) is False
