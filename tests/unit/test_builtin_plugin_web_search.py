"""The web-search plugin, exercised as a plugin.

Not the same thing as `test_web_search.py`, which tests the pure functions.
This runs the shipped `plugin.toml` through the real validator and the real
graph walker, with only the HTTP call stubbed — so it is what proves the
manifest parses, the name passes `PLUGIN_NAME_RE`, the schema is valid, the
body resolves, the declared setting is honoured, and the whole thing is a
plugin rather than a module that happens to exist.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from sadana import execution
from sadana.builtin_seed import SOURCE_ROOT
from sadana.plugin_manifest import run_graph, validate
from sadana.plugins import Valid

_KEY_VAR = "SADANA_PLUGIN__WEB_SEARCH__API_KEY"


def _plugin_dir() -> Path:
    return SOURCE_ROOT / "web-search"


async def _approve_ok(_plugin: str, _node: str, _value: object) -> bool:
    return True


async def _ask_unused(_skill: object, _text: str) -> str:
    raise AssertionError("this plugin has no ask node")


def _run(arguments: dict) -> object:
    outcome = validate(_plugin_dir())
    assert isinstance(outcome, Valid)
    return asyncio.run(
        run_graph(
            _plugin_dir(),
            outcome.manifest,
            outcome.manifest.entries[0],
            arguments,
            ask=_ask_unused,  # type: ignore[arg-type]
            approve=_approve_ok,
        )
    )


@pytest.mark.unit
def test_the_shipped_plugin_validates() -> None:
    """Covers more than it looks: an invalid plugin is *silently excluded* by
    `discover_plugins`, so without this the tool would simply not appear and
    nothing would say why."""
    outcome = validate(_plugin_dir())
    assert isinstance(outcome, Valid)
    assert outcome.manifest.name == "web-search"
    assert outcome.manifest.entries[0].tool == "web.search"


@pytest.mark.unit
def test_the_plugin_declares_its_key_as_a_secret() -> None:
    outcome = validate(_plugin_dir())
    assert isinstance(outcome, Valid)
    (setting,) = outcome.manifest.settings
    assert setting.name == "api_key"
    assert setting.secret is True


@pytest.mark.unit
def test_a_run_with_no_key_set_never_reaches_the_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """`PLUGIN-CONFIG-01`'s preflight stops the run before the first node, so
    this plugin writes no check of its own for the common case."""
    monkeypatch.delenv(_KEY_VAR, raising=False)

    def explode(_request: object) -> object:
        raise AssertionError("no HTTP request may be made without a key")

    monkeypatch.setattr(execution, "run_http", explode)
    result = _run({"query": "anything"})

    assert result.failed_node == "entry"  # type: ignore[attr-defined]
    assert "api_key" in result.text  # type: ignore[attr-defined]


@pytest.mark.unit
def test_a_search_renders_what_the_service_returned(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake(_request: execution.HttpRequest) -> execution.Outcome:
        body = json.dumps(
            {"web": {"results": [{"title": "Rust 1.99", "url": "https://e.invalid/r", "description": "Notes."}]}}
        ).encode()
        return execution.Success(status=200, body=body)

    monkeypatch.setenv(_KEY_VAR, "secret-token")  # pragma: allowlist secret
    monkeypatch.setattr(execution, "run_http", fake)

    result = _run({"query": "rust release", "count": 3})

    assert result.failed_node is None  # type: ignore[attr-defined]
    assert "Rust 1.99" in result.text  # type: ignore[attr-defined]
    assert "https://e.invalid/r" in result.text  # type: ignore[attr-defined]
    assert "not instructions" in result.text  # type: ignore[attr-defined]


@pytest.mark.unit
def test_the_key_travels_as_a_header_and_never_in_the_url(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[execution.HttpRequest] = []

    def fake(request: execution.HttpRequest) -> execution.Outcome:
        seen.append(request)
        return execution.Success(status=200, body=b'{"web": {"results": []}}')

    monkeypatch.setenv(_KEY_VAR, "secret-token")  # pragma: allowlist secret
    monkeypatch.setattr(execution, "run_http", fake)

    result = _run({"query": "q"})

    assert seen[0].headers["X-Subscription-Token"] == "secret-token"  # pragma: allowlist secret
    assert "secret-token" not in seen[0].url
    # And it is not echoed back to the model either.
    assert "secret-token" not in result.text  # type: ignore[attr-defined]


@pytest.mark.unit
def test_an_unreachable_service_is_a_sentence_not_a_failed_node(monkeypatch: pytest.MonkeyPatch) -> None:
    """A plugin's outcome crosses back as a returned value; a service being
    down is an answer the person should read, not a broken plugin."""
    monkeypatch.setenv(_KEY_VAR, "secret-token")  # pragma: allowlist secret
    monkeypatch.setattr(execution, "run_http", lambda _r: execution.Failure(detail="HTTP 429: slow down"))

    result = _run({"query": "q"})

    assert result.failed_node is None  # type: ignore[attr-defined]
    assert "could not be completed" in result.text  # type: ignore[attr-defined]


@pytest.mark.unit
def test_an_empty_query_never_reaches_the_network(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(_KEY_VAR, "secret-token")  # pragma: allowlist secret

    def explode(_request: object) -> object:
        raise AssertionError("an empty query must not be sent")

    monkeypatch.setattr(execution, "run_http", explode)
    result = _run({"query": "   "})

    assert "No search query" in result.text  # type: ignore[attr-defined]


@pytest.mark.unit
def test_the_call_node_is_still_gated_by_approval(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every `call` node is asked about before its body runs, and this plugin
    does nothing to exempt itself — the friction `intent.md` raises as the
    owner's open question is real and this pins it."""
    monkeypatch.setenv(_KEY_VAR, "secret-token")  # pragma: allowlist secret

    def explode(_request: object) -> object:
        raise AssertionError("a declined call must not reach the network")

    monkeypatch.setattr(execution, "run_http", explode)

    async def decline(_plugin: str, _node: str, _value: object) -> bool:
        return False

    outcome = validate(_plugin_dir())
    assert isinstance(outcome, Valid)
    result = asyncio.run(
        run_graph(
            _plugin_dir(),
            outcome.manifest,
            outcome.manifest.entries[0],
            {"query": "q"},
            ask=_ask_unused,  # type: ignore[arg-type]
            approve=decline,
        )
    )

    assert result.failed_node == "search"


@pytest.mark.unit
def test_a_true_count_does_not_become_a_one_result_search(monkeypatch: pytest.MonkeyPatch) -> None:
    """`isinstance(True, int)` is True, so a schema-invalid `count` of `true`
    would have clamped to 1 rather than falling back to the documented 5."""
    seen: list[execution.HttpRequest] = []

    def fake(request: execution.HttpRequest) -> execution.Outcome:
        seen.append(request)
        return execution.Success(status=200, body=b'{"web": {"results": []}}')

    monkeypatch.setenv(_KEY_VAR, "secret-token")  # pragma: allowlist secret
    monkeypatch.setattr(execution, "run_http", fake)

    _run({"query": "q", "count": True})

    assert "count=5" in seen[0].url


@pytest.mark.unit
def test_an_error_body_from_the_service_is_defanged_too(monkeypatch: pytest.MonkeyPatch) -> None:
    """`execution.run_http` builds its detail from up to 200 bytes of the
    response body — a second path by which somebody else's bytes reach the
    model, and it gets the same filter the results do."""
    monkeypatch.setenv(_KEY_VAR, "secret-token")  # pragma: allowlist secret
    monkeypatch.setattr(execution, "run_http", lambda _r: execution.Failure(detail="HTTP 403: \x1b[31mdenied​‮"))

    result = _run({"query": "q"})

    assert "\x1b" not in result.text  # type: ignore[attr-defined]
    assert "​" not in result.text  # type: ignore[attr-defined]
    assert "‮" not in result.text  # type: ignore[attr-defined]
