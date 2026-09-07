"""Tests for sadana.plugins: Artifact, NodeTrace, DagResult, Entry, Node,
Manifest, ManifestOutcome, InstalledPlugin."""

from __future__ import annotations

import dataclasses
from pathlib import Path

import pytest

from sadana.plugins import (
    Artifact,
    CyclicGraph,
    DagResult,
    DanglingTarget,
    DuplicateNodeName,
    Entry,
    InstalledPlugin,
    InvalidSchema,
    Manifest,
    ManifestParseError,
    Node,
    NodeTrace,
    UnreachableNode,
    UnresolvedBody,
    UnresolvedSkill,
    Valid,
)

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


# ── Entry ────────────────────────────────────────────────────────────────


@pytest.mark.unit
def test_entry_constructs_with_its_documented_fields() -> None:
    entry = Entry(tool="do_thing", purpose="does the thing", parameters="schemas/do_thing.json", start="fetch")
    assert entry.tool == "do_thing"
    assert entry.purpose == "does the thing"
    assert entry.parameters == "schemas/do_thing.json"
    assert entry.start == "fetch"


@pytest.mark.unit
def test_entry_is_frozen() -> None:
    entry = Entry(tool="do_thing", purpose="p", parameters="s.json", start="fetch")
    with pytest.raises(dataclasses.FrozenInstanceError):
        entry.tool = "changed"  # type: ignore[misc]


# ── Node ─────────────────────────────────────────────────────────────────


@pytest.mark.unit
def test_node_constructs_with_its_documented_fields() -> None:
    node = Node(name="route_on_kind", kind="route", body=None, skill=None, next=None, ports=("urgent", "normal"))
    assert node.name == "route_on_kind"
    assert node.kind == "route"
    assert node.body is None
    assert node.skill is None
    assert node.next is None
    assert node.ports == ("urgent", "normal")


@pytest.mark.unit
def test_node_defaults_have_no_body_skill_next_and_empty_ports() -> None:
    node = Node(name="fetch", kind="compute")
    assert node.body is None
    assert node.skill is None
    assert node.next is None
    assert node.ports == ()


@pytest.mark.unit
def test_node_is_frozen() -> None:
    node = Node(name="fetch", kind="compute", body="init:fetch", next="interpret")
    with pytest.raises(dataclasses.FrozenInstanceError):
        node.next = "changed"  # type: ignore[misc]


# ── Manifest ─────────────────────────────────────────────────────────────


@pytest.mark.unit
def test_manifest_constructs_with_its_documented_fields() -> None:
    entries = (Entry(tool="do_thing", purpose="p", parameters="s.json", start="fetch"),)
    nodes = (Node(name="fetch", kind="compute", next="stop"), Node(name="stop", kind="stop"))
    manifest = Manifest(name="example-plugin", version="0.1.0", description="An example.", entries=entries, nodes=nodes)
    assert manifest.name == "example-plugin"
    assert manifest.version == "0.1.0"
    assert manifest.description == "An example."
    assert manifest.entries == entries
    assert manifest.nodes == nodes


@pytest.mark.unit
def test_manifest_is_frozen() -> None:
    manifest = Manifest(name="p", version="0.1.0", description="d", entries=(), nodes=())
    with pytest.raises(dataclasses.FrozenInstanceError):
        manifest.name = "changed"  # type: ignore[misc]


# ── ManifestOutcome ──────────────────────────────────────────────────────


@pytest.mark.unit
def test_valid_carries_a_manifest() -> None:
    manifest = Manifest(name="p", version="0.1.0", description="d", entries=(), nodes=())
    outcome = Valid(manifest=manifest)
    assert outcome.manifest == manifest


@pytest.mark.unit
def test_manifest_parse_error_carries_a_detail() -> None:
    assert ManifestParseError(detail="unexpected character at line 3").detail == "unexpected character at line 3"


@pytest.mark.unit
def test_invalid_schema_carries_entry_path_and_detail() -> None:
    outcome = InvalidSchema(entry="do_thing", path="schemas/do_thing.json", detail="not an object")
    assert outcome.entry == "do_thing"
    assert outcome.path == "schemas/do_thing.json"
    assert outcome.detail == "not an object"


@pytest.mark.unit
def test_unresolved_skill_carries_node_and_detail() -> None:
    outcome = UnresolvedSkill(node="interpret", detail="no SKILL.md at .../skills/interpret")
    assert outcome.node == "interpret"
    assert outcome.detail == "no SKILL.md at .../skills/interpret"


@pytest.mark.unit
def test_duplicate_node_name_carries_the_name() -> None:
    assert DuplicateNodeName(name="fetch").name == "fetch"


@pytest.mark.unit
def test_dangling_target_carries_node_and_target() -> None:
    outcome = DanglingTarget(node="fetch", target="nope")
    assert outcome.node == "fetch"
    assert outcome.target == "nope"


@pytest.mark.unit
def test_unresolved_body_carries_node_and_body() -> None:
    outcome = UnresolvedBody(node="fetch", body="init:missing_function")
    assert outcome.node == "fetch"
    assert outcome.body == "init:missing_function"


@pytest.mark.unit
def test_unreachable_node_carries_the_node() -> None:
    assert UnreachableNode(node="orphan").node == "orphan"


@pytest.mark.unit
def test_cyclic_graph_carries_a_node_on_the_cycle() -> None:
    assert CyclicGraph(node="a").node == "a"


# ── InstalledPlugin ──────────────────────────────────────────────────────


@pytest.mark.unit
def test_installed_plugin_constructs_with_its_documented_fields() -> None:
    manifest = Manifest(name="example-plugin", version="0.1.0", description="d", entries=(), nodes=())
    directory = Path("/plugins/example-plugin")
    installed = InstalledPlugin(name="example-plugin", directory=directory, manifest=manifest)
    assert installed.name == "example-plugin"
    assert installed.directory == directory
    assert installed.manifest == manifest


@pytest.mark.unit
def test_installed_plugin_is_frozen() -> None:
    manifest = Manifest(name="p", version="0.1.0", description="d", entries=(), nodes=())
    installed = InstalledPlugin(name="p", directory=Path("/plugins/p"), manifest=manifest)
    with pytest.raises(dataclasses.FrozenInstanceError):
        installed.name = "changed"  # type: ignore[misc]
