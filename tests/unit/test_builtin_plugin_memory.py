"""Tests for the first-party `memory` plugin shipped at
`src/sadana/builtin_plugins/memory/` — proves the plugin end to end via
`run_graph()`, not by reading its source (testing-conventions)."""

from __future__ import annotations

import asyncio

import pytest

from conftest import open_conn
from sadana.memory_store import DispatchContext, ensure_schema, list_entries
from sadana.plugin_manifest import run_graph, validate
from sadana.plugins import SkillRef, Valid


async def _stub_ask(_skill: SkillRef, _text: str) -> str | None:
    return None


async def _approve_ok(_plugin: str, _node: str, _value: object) -> bool:
    return True


def _builtin_dir():  # type: ignore[no-untyped-def]
    from sadana.memory_store import _BUILTIN_PLUGIN_SOURCE

    return _BUILTIN_PLUGIN_SOURCE


@pytest.mark.unit
def test_builtin_memory_plugin_validates() -> None:
    outcome = validate(_builtin_dir())
    assert isinstance(outcome, Valid)
    assert outcome.manifest.name == "memory"


@pytest.mark.unit
def test_builtin_memory_plugin_writes_through_run_graph() -> None:
    conn = open_conn()
    ensure_schema(conn)
    outcome = validate(_builtin_dir())
    assert isinstance(outcome, Valid)
    entry = outcome.manifest.entries[0]
    ctx = DispatchContext(account_key="a1", conn=conn)  # pragma: allowlist secret
    arguments = {"entry_key": "dog_name", "content": "Buddy", "_sadana_memory_ctx": ctx}

    result = asyncio.run(
        run_graph(_builtin_dir(), outcome.manifest, entry, arguments, ask=_stub_ask, approve=_approve_ok)
    )

    assert result.failed_node is None
    entries = list_entries(conn, "a1")
    assert len(entries) == 1
    assert entries[0].entry_key == "dog_name"
    assert entries[0].content == "Buddy"


@pytest.mark.unit
def test_builtin_memory_plugin_fails_safely_without_context() -> None:
    conn = open_conn()
    ensure_schema(conn)
    outcome = validate(_builtin_dir())
    assert isinstance(outcome, Valid)
    entry = outcome.manifest.entries[0]
    # No `_sadana_memory_ctx` — the shape `build_dispatch()` produces when
    # `memory_context` was never passed in (spec.md's untouched-by-default
    # posture). The plugin must fail the node, never raise out of run_graph.
    arguments = {"entry_key": "dog_name", "content": "Buddy"}

    result = asyncio.run(
        run_graph(_builtin_dir(), outcome.manifest, entry, arguments, ask=_stub_ask, approve=_approve_ok)
    )

    assert result.failed_node == "write"
    assert list_entries(conn, "a1") == ()
