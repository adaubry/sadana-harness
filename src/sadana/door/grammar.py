"""The console's list grammar: params, cursors, paging, timestamps.

`docs/tasks/H19-door-framework-token-conformance/spec.md` requirements 5-10.
Exactly four list params (`filter`, `order_by`, `page_size`, `page_token`),
one tolerated extra (`count=true`); paging is a Python keyset scan over
already-sorted, already-rendered rows — no SQL keyset query — per requirement
9's own note that a SQL keyset is a later maintain item once a store's row
count makes the scan slow.
"""

from __future__ import annotations

import base64
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from urllib.parse import parse_qsl

from sadana.door import filter as door_filter
from sadana.door import problems

_ALLOWED_PARAMS = {"filter", "order_by", "page_size", "page_token", "count"}


@dataclass(frozen=True)
class ListParams:
    filter_ast: door_filter.Node | None
    order_field: str
    order_dir: str  # "asc" | "desc"
    page_size: int
    page_token: str | None
    count: bool


@dataclass(frozen=True)
class ListResponse:
    data: list[Mapping[str, object]]
    next_page_token: str | None
    count: int | None


def render_ts(t: float) -> str:
    """ISO-8601 UTC, millisecond precision, trailing `Z`."""
    dt = datetime.fromtimestamp(t, tz=UTC)
    return f"{dt.strftime('%Y-%m-%dT%H:%M:%S')}.{dt.microsecond // 1000:03d}Z"


def parse_ts(s: str) -> float:
    return datetime.fromisoformat(s).timestamp()


def encode_cursor(order_by: str, sort_value: object, id: str) -> str:
    """`base64url(json([order_by, sort_value, id]))`. `order_by` rides along
    so a token minted under one ordering is detectably wrong under another
    (spec.md requirement 9) — the token is opaque to every caller outside
    this module, so nothing depends on its literal shape beyond that."""
    raw = json.dumps([order_by, sort_value, id]).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def decode_cursor(token: str) -> tuple[str, object, str] | None:
    try:
        padded = token + "=" * (-len(token) % 4)
        raw = base64.urlsafe_b64decode(padded.encode("ascii"))
        decoded = json.loads(raw)
    except (ValueError, TypeError):
        return None
    if not isinstance(decoded, list) or len(decoded) != 3:
        return None
    order_by, sort_value, row_id = decoded
    if not isinstance(order_by, str) or not isinstance(row_id, str):
        return None
    return order_by, sort_value, row_id


def _parse_query(query: str) -> dict[str, str] | problems.Problem:
    seen: dict[str, str] = {}
    for key, value in parse_qsl(query, keep_blank_values=True):
        if key in seen:
            return problems.make("VALIDATION", f"{key!r} may not be repeated")
        seen[key] = value
    unknown = sorted(set(seen) - _ALLOWED_PARAMS)
    if unknown:
        return problems.make("VALIDATION", f"unknown list parameter {unknown[0]!r}")
    return seen


def parse_list_params(
    query: str, *, filterable: frozenset[str], orderable: frozenset[str]
) -> ListParams | problems.Problem:
    parsed = _parse_query(query)
    if isinstance(parsed, problems.Problem):
        return parsed

    order_by = parsed.get("order_by", "created_at desc")
    order_parts = order_by.split()
    if len(order_parts) != 2 or order_parts[1] not in ("asc", "desc"):
        return problems.make("VALIDATION", f"order_by must be '<field> asc|desc', got {order_by!r}")
    order_field, order_dir = order_parts
    if order_field not in orderable:
        return problems.make("VALIDATION", f"{order_field!r} is not an orderable field")
    normalized_order_by = f"{order_field} {order_dir}"

    page_size_raw = parsed.get("page_size", "50")
    try:
        page_size = int(page_size_raw)
    except ValueError:
        return problems.make("VALIDATION", f"page_size must be an integer, got {page_size_raw!r}")
    if not 1 <= page_size <= 200:
        return problems.make("VALIDATION", f"page_size must be between 1 and 200, got {page_size}")

    filter_ast: door_filter.Node | None = None
    if "filter" in parsed:
        try:
            filter_ast = door_filter.parse(parsed["filter"])
        except door_filter._SyntaxError as exc:
            return problems.make("VALIDATION", f"malformed filter: {exc}")
        unknown_fields = sorted(door_filter.fields(filter_ast) - filterable)
        if unknown_fields:
            return problems.make("VALIDATION", f"{unknown_fields[0]!r} is not a filterable field")

    page_token = parsed.get("page_token")
    if page_token is not None:
        decoded = decode_cursor(page_token)
        if decoded is None:
            return problems.make("VALIDATION", "page_token is malformed")
        if decoded[0] != normalized_order_by:
            return problems.make("VALIDATION", "page_token was minted under a different order_by")

    return ListParams(
        filter_ast=filter_ast,
        order_field=order_field,
        order_dir=order_dir,
        page_size=page_size,
        page_token=page_token,
        count=parsed.get("count") == "true",
    )


def page(rows: Sequence[Mapping[str, object]], params: ListParams) -> ListResponse:
    """Filter, sort, and keyset-page an already-rendered row sequence. Rows
    are plain JSON-ready dicts (standard-field timestamps already strings),
    so sorting by any field's own value is also sorting by its wire order."""
    filtered = (
        [r for r in rows if door_filter.evaluate(params.filter_ast, r)] if params.filter_ast is not None else list(rows)
    )
    normalized_order_by = f"{params.order_field} {params.order_dir}"
    ordered = sorted(
        filtered,
        key=lambda r: (r.get(params.order_field), r["id"]),
        reverse=params.order_dir == "desc",
    )

    start = 0
    if params.page_token is not None:
        decoded = decode_cursor(params.page_token)
        if decoded is not None:
            _, sort_value, last_id = decoded
            for idx, row in enumerate(ordered):
                if (row.get(params.order_field), row["id"]) == (sort_value, last_id):
                    start = idx + 1
                    break

    page_rows = ordered[start : start + params.page_size]
    has_more = start + params.page_size < len(ordered)
    next_token = (
        encode_cursor(normalized_order_by, page_rows[-1].get(params.order_field), page_rows[-1]["id"])  # type: ignore[arg-type]
        if has_more and page_rows
        else None
    )
    return ListResponse(
        data=page_rows,
        next_page_token=next_token,
        count=len(filtered) if params.count else None,
    )
