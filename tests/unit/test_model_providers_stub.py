"""Tests for sadana.model_providers.stub.provider: the network-free dev
provider H21 adds so streaming can be exercised with no cost and no
network. Goes through the real, auto-discovered registry — same convention
`test_model_providers_openrouter.py` uses for its own wired provider."""

from __future__ import annotations

import pytest

from sadana.model_access import Request, get_provider


@pytest.mark.unit
def test_stream_fn_deltas_concatenate_to_request_fns_own_content() -> None:
    manifest = get_provider("stub")
    assert manifest.request_fn is not None and manifest.stream_fn is not None
    request = Request(messages=(), provider="stub", model="x")

    deltas: list[str] = []
    _, streamed_body = manifest.stream_fn(request, deltas.append)
    _, plain_body = manifest.request_fn(request)

    assert "".join(deltas) == plain_body["choices"][0]["message"]["content"]
    assert streamed_body == plain_body


@pytest.mark.unit
def test_stream_fn_emits_more_than_one_delta() -> None:
    manifest = get_provider("stub")
    assert manifest.stream_fn is not None
    request = Request(messages=(), provider="stub", model="x")

    deltas: list[str] = []
    manifest.stream_fn(request, deltas.append)

    assert len(deltas) >= 2
    assert all(deltas)  # no empty fragments


@pytest.mark.unit
def test_registered_manifest_needs_no_credential() -> None:
    manifest = get_provider("stub")
    assert manifest.env_vars == ()
