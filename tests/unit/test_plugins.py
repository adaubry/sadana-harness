"""Tests for sadana.plugins: Artifact, NodeTrace, DagResult."""

from __future__ import annotations

import dataclasses

import pytest

from sadana.plugins import Artifact, DagResult, NodeTrace

# ── Artifact ─────────────────────────────────────────────────────────────


@pytest.mark.unit
def test_artifact_constructs_with_its_documented_fields() -> None:
    artifact = Artifact(kind="link", name="report", ref="https://example.com/report")
    assert artifact.kind == "link"
    assert artifact.name == "report"
    assert artifact.ref == "https://example.com/report"


@pytest.mark.unit
def test_artifact_is_frozen() -> None:
    artifact = Artifact(kind="file", name="out", ref="/tmp/out.txt")
    with pytest.raises(dataclasses.FrozenInstanceError):
        artifact.name = "changed"  # type: ignore[misc]


# ── NodeTrace ────────────────────────────────────────────────────────────


@pytest.mark.unit
def test_node_trace_constructs_with_its_documented_fields() -> None:
    trace = NodeTrace(node="fetch", kind="compute", visit=0, ok=True, port=None, detail="fetched")
    assert trace.node == "fetch"
    assert trace.kind == "compute"
    assert trace.visit == 0
    assert trace.ok is True
    assert trace.port is None
    assert trace.detail == "fetched"


@pytest.mark.unit
def test_node_trace_is_frozen() -> None:
    trace = NodeTrace(node="route_on_kind", kind="route", visit=0, ok=True, port="urgent", detail=None)
    with pytest.raises(dataclasses.FrozenInstanceError):
        trace.ok = False  # type: ignore[misc]


# ── DagResult ────────────────────────────────────────────────────────────


@pytest.mark.unit
def test_dag_result_constructs_with_its_documented_fields() -> None:
    trace = (NodeTrace(node="fetch", kind="compute", visit=0, ok=True, port=None, detail=None),)
    artifacts = (Artifact(kind="link", name="report", ref="https://example.com"),)
    result = DagResult(
        plugin="plugin-a",
        entry="plugin_a_entry",
        text="done",
        artifacts=artifacts,
        trace=trace,
        failed_node=None,
    )
    assert result.plugin == "plugin-a"
    assert result.entry == "plugin_a_entry"
    assert result.text == "done"
    assert result.artifacts == artifacts
    assert result.trace == trace
    assert result.failed_node is None


@pytest.mark.unit
def test_dag_result_carries_a_failed_node_as_data_not_a_raise() -> None:
    result = DagResult(
        plugin="plugin-a",
        entry="plugin_a_entry",
        text="the run did not reach a terminal step",
        artifacts=(),
        trace=(),
        failed_node="fetch",
    )
    assert result.failed_node == "fetch"
    assert result.text  # always present, even on a failure path


@pytest.mark.unit
def test_dag_result_is_frozen() -> None:
    result = DagResult(plugin="p", entry="e", text="t", artifacts=(), trace=(), failed_node=None)
    with pytest.raises(dataclasses.FrozenInstanceError):
        result.text = "changed"  # type: ignore[misc]
