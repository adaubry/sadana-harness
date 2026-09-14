"""`door/grammar.py` — list params, cursors, paging, timestamps."""

from __future__ import annotations

import pytest

from sadana.door import grammar, problems

_FILTERABLE = frozenset({"state", "name"})
_ORDERABLE = frozenset({"created_at", "name"})


def _rows(n: int) -> list[dict]:
    return [
        {"id": f"wgt_{i:04d}", "created_at": f"2026-01-{i + 1:02d}T00:00:00.000Z", "name": f"w{i}"} for i in range(n)
    ]


def _params(**overrides: object) -> grammar.ListParams:
    defaults: dict[str, object] = {
        "filter_ast": None,
        "order_field": "created_at",
        "order_dir": "asc",
        "page_size": 2,
        "page_token": None,
        "count": False,
    }
    defaults.update(overrides)
    return grammar.ListParams(**defaults)  # type: ignore[arg-type]


@pytest.mark.unit
def test_render_and_parse_ts_round_trip() -> None:
    rendered = grammar.render_ts(1_767_225_600.123)
    assert rendered.endswith("Z")
    assert grammar.parse_ts(rendered) == pytest.approx(1_767_225_600.123, abs=0.001)


@pytest.mark.unit
def test_render_ts_is_millisecond_precision_iso8601_z() -> None:
    assert grammar.render_ts(0) == "1970-01-01T00:00:00.000Z"


@pytest.mark.unit
def test_defaults_are_created_at_desc_page_size_50() -> None:
    params = grammar.parse_list_params("", filterable=_FILTERABLE, orderable=_ORDERABLE)
    assert isinstance(params, grammar.ListParams)
    assert (params.order_field, params.order_dir, params.page_size) == ("created_at", "desc", 50)


@pytest.mark.unit
def test_count_true_is_tolerated() -> None:
    params = grammar.parse_list_params("count=true", filterable=_FILTERABLE, orderable=_ORDERABLE)
    assert isinstance(params, grammar.ListParams)
    assert params.count is True


@pytest.mark.unit
def test_a_fifth_unknown_param_is_rejected_by_name() -> None:
    result = grammar.parse_list_params("bogus=1", filterable=_FILTERABLE, orderable=_ORDERABLE)
    assert isinstance(result, problems.Problem)
    assert result.code == "VALIDATION"
    assert "bogus" in (result.detail or "")


@pytest.mark.unit
def test_an_unorderable_field_is_rejected() -> None:
    result = grammar.parse_list_params("order_by=secret+asc", filterable=_FILTERABLE, orderable=_ORDERABLE)
    assert isinstance(result, problems.Problem)


@pytest.mark.unit
def test_page_size_out_of_range_is_rejected() -> None:
    assert isinstance(
        grammar.parse_list_params("page_size=0", filterable=_FILTERABLE, orderable=_ORDERABLE), problems.Problem
    )
    assert isinstance(
        grammar.parse_list_params("page_size=201", filterable=_FILTERABLE, orderable=_ORDERABLE), problems.Problem
    )


@pytest.mark.unit
def test_an_unfilterable_field_is_rejected() -> None:
    result = grammar.parse_list_params('filter=secret+%3D+"x"', filterable=_FILTERABLE, orderable=_ORDERABLE)
    assert isinstance(result, problems.Problem)


@pytest.mark.unit
def test_page_token_minted_under_another_order_by_is_rejected() -> None:
    token = grammar.encode_cursor("name asc", "w0", "wgt_0000")
    result = grammar.parse_list_params(f"page_token={token}", filterable=_FILTERABLE, orderable=_ORDERABLE)
    assert isinstance(result, problems.Problem)


@pytest.mark.unit
def test_paging_walks_every_row_exactly_once() -> None:
    rows = _rows(5)
    params = _params()

    page1 = grammar.page(rows, params)
    assert [r["id"] for r in page1.data] == ["wgt_0000", "wgt_0001"]
    assert page1.next_page_token is not None

    params2 = _params(page_token=page1.next_page_token)
    page2 = grammar.page(rows, params2)
    assert [r["id"] for r in page2.data] == ["wgt_0002", "wgt_0003"]

    params3 = _params(page_token=page2.next_page_token)
    page3 = grammar.page(rows, params3)
    assert [r["id"] for r in page3.data] == ["wgt_0004"]
    assert page3.next_page_token is None


@pytest.mark.unit
def test_keyset_paging_is_stable_across_an_insertion() -> None:
    """A row inserted between page one's last row and page two's first row
    must not shift or duplicate anything already returned — resumption is by
    the cursor's own (sort value, id), never by index."""
    rows = _rows(4)  # wgt_0000..0003, Jan 1..4
    params = _params()
    page1 = grammar.page(rows, params)
    assert [r["id"] for r in page1.data] == ["wgt_0000", "wgt_0001"]

    inserted = {"id": "wgt_0001_5", "created_at": "2026-01-02T12:00:00.000Z", "name": "inserted"}
    rows_with_insert = rows + [inserted]

    params2 = _params(page_token=page1.next_page_token)
    page2 = grammar.page(rows_with_insert, params2)
    assert [r["id"] for r in page2.data] == ["wgt_0001_5", "wgt_0002"]


@pytest.mark.unit
def test_count_true_reports_the_filtered_total_not_the_page_size() -> None:
    rows = _rows(5)
    params = _params(count=True)
    result = grammar.page(rows, params)
    assert result.count == 5
    assert len(result.data) == 2


@pytest.mark.unit
def test_decode_cursor_rejects_garbage() -> None:
    assert grammar.decode_cursor("not-valid-base64!!!") is None
    assert grammar.decode_cursor("") is None
