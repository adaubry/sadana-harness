"""`spans` noun contract test — spec.md requirement 26.

`list` always empty, `get` always 404 — no table exists until H21.
"""

# ruff: noqa: F401, F811 -- `keys`/`schema` are pytest fixtures imported from
# `_scaffold.py` (not a `conftest.py` -- see that file's own docstring) and
# used by name as a parameter in every test function below; ruff's static
# analysis reads each repeated parameter as redefining an "unused" import,
# which is exactly the documented pytest cross-module-fixture idiom, not a
# real redefinition.

from __future__ import annotations

import json
from pathlib import Path

import pytest
from _scaffold import keys, make_ctx, mint, req, schema, validate

from sadana import stores
from sadana.door.nouns import spans
from sadana.door.router import handle


@pytest.mark.contract
def test_list_is_always_empty(keys, tmp_path: Path, schema: dict) -> None:
    conns = stores.Connections(tmp_path / "door.db")
    ctx = make_ctx(keys, conns, {"spans": spans})
    token = mint(keys, scope=["spans:read"])
    resp = handle(req("GET", "/v1/spans", token=token), ctx=ctx)
    assert resp.status == 200
    body = json.loads(resp.body)
    assert body == {"data": [], "next_page_token": None}
    validate(schema, body, "ListResponse")


@pytest.mark.contract
def test_get_is_always_not_found(keys, tmp_path: Path) -> None:
    conns = stores.Connections(tmp_path / "door.db")
    ctx = make_ctx(keys, conns, {"spans": spans})
    token = mint(keys, scope=["spans:read"])
    resp = handle(req("GET", "/v1/spans/spn_doesnotexist", token=token), ctx=ctx)
    assert resp.status == 404


@pytest.mark.contract
def test_create_is_capability_missing(keys, tmp_path: Path) -> None:
    conns = stores.Connections(tmp_path / "door.db")
    ctx = make_ctx(keys, conns, {"spans": spans})
    token = mint(keys, scope=["spans:write"])
    resp = handle(req("POST", "/v1/spans", token=token, body=b"{}"), ctx=ctx)
    assert resp.status == 501
