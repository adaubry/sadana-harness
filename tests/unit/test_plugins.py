"""Tests for sadana.plugins: Artifact, NodeTrace, DagResult, Entry, Node,
Manifest, ManifestOutcome, InstalledPlugin."""

from __future__ import annotations

import dataclasses
from pathlib import Path

import pytest

from sadana.plugins import (
    KIND_USES,
    KINDS_NOT_RUNNABLE,
    NODE_KINDS,
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
    _parse_manifest,
    describe_manifest_outcome,
    manifest_from_dict,
    manifest_to_dict,
    manifest_to_toml,
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


# ── manifest_to_dict ─────────────────────────────────────────────────────


@pytest.mark.unit
def test_manifest_to_dict_round_trips_every_field_as_json_safe_primitives() -> None:
    entries = (Entry(tool="do_thing", purpose="p", parameters="s.json", start="fetch"),)
    nodes = (
        Node(name="fetch", kind="compute", body="init:fetch_record", next="route_on_kind"),
        Node(name="route_on_kind", kind="route", body="init:classify", ports=("urgent", "normal")),
    )
    manifest = Manifest(
        name="example-plugin", version="0.3.1", description="One sentence.", entries=entries, nodes=nodes
    )

    result = manifest_to_dict(manifest)

    assert result == {
        "name": "example-plugin",
        "version": "0.3.1",
        "description": "One sentence.",
        "entries": [{"tool": "do_thing", "purpose": "p", "parameters": "s.json", "start": "fetch"}],
        "nodes": [
            {
                "name": "fetch",
                "kind": "compute",
                "body": "init:fetch_record",
                "skill": None,
                "next": "route_on_kind",
                "ports": [],
            },
            {
                "name": "route_on_kind",
                "kind": "route",
                "body": "init:classify",
                "skill": None,
                "next": None,
                "ports": ["urgent", "normal"],
            },
        ],
    }


@pytest.mark.unit
def test_manifest_to_dict_handles_no_entries_and_no_nodes() -> None:
    manifest = Manifest(name="p", version="0.1.0", description="d", entries=(), nodes=())
    assert manifest_to_dict(manifest) == {
        "name": "p",
        "version": "0.1.0",
        "description": "d",
        "entries": [],
        "nodes": [],
    }


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


# ── describe_manifest_outcome ────────────────────────────────────────────


@pytest.mark.unit
def test_describe_manifest_outcome_names_the_actual_problem_for_every_variant() -> None:
    cases = [
        (ManifestParseError(detail="bad toml"), "bad toml"),
        (InvalidSchema(entry="do_thing", path="s.json", detail="not an object"), "do_thing"),
        (UnresolvedSkill(node="interpret", detail="no SKILL.md"), "interpret"),
        (DuplicateNodeName(name="fetch"), "fetch"),
        (DanglingTarget(node="fetch", target="nope"), "nope"),
        (UnresolvedBody(node="fetch", body="init:missing"), "init:missing"),
        (UnreachableNode(node="orphan"), "orphan"),
        (CyclicGraph(node="a"), "a"),
    ]
    for outcome, expected_substring in cases:
        assert expected_substring in describe_manifest_outcome(outcome)


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


# ── the node-kind vocabulary (PLUGIN-EDITOR-01) ──────────────────────────


@pytest.mark.unit
def test_every_declared_kind_has_a_row_in_kind_uses() -> None:
    """The palette a person is offered is derived from `NodeKind`, not typed
    out again. A new kind added to the Literal without a row here is a hole
    this test names rather than a silently-empty box in the editor."""
    assert set(KIND_USES) == set(NODE_KINDS)


@pytest.mark.unit
def test_kind_uses_matches_what_the_node_fields_are_for() -> None:
    assert "ports" not in KIND_USES["compute"]
    assert KIND_USES["route"] == ("body", "ports")  # the only kind with named ports
    assert KIND_USES["ask"] == ("skill", "next")
    assert KIND_USES["stop"] == ()  # terminal: no successor, nothing to configure
    assert frozenset({"each"}) == KINDS_NOT_RUNNABLE


# ── manifest_to_toml / manifest_from_dict (PLUGIN-EDITOR-01) ─────────────

_REAL_FIXTURES = Path(__file__).resolve().parent.parent / "fixtures" / "plugins"


def _real_manifests() -> list[Manifest]:
    return [
        _parse_manifest((_REAL_FIXTURES / name / "plugin.toml").read_text(encoding="utf-8"))
        for name in ("plugin-a", "plugin-b", "plugin-c", "plugin-d")
    ]


@pytest.mark.unit
def test_manifest_to_toml_round_trips_every_real_fixture() -> None:
    """Between them the four fixtures cover call, ask, route, stop, wait and
    compute steps, an entry's every field, named ports, and nodes with no
    successor — so this is the whole emitted vocabulary, not a sample."""
    for manifest in _real_manifests():
        assert _parse_manifest(manifest_to_toml(manifest)) == manifest


@pytest.mark.unit
def test_manifest_to_toml_round_trips_a_hostile_description() -> None:
    """A quote and a backslash break naive quoting; a newline and a tab break
    naive line-joining; the emoji is the one that catches `ensure_ascii`
    being left at its default, which spells it as a surrogate pair TOML
    rejects."""
    manifest = Manifest(
        name="p",
        version="0.1.0",
        description='he said "hi"\\then\na new\tline 😀',
        entries=(Entry(tool="t", purpose='with "quotes"', parameters="s.json", start="n"),),
        nodes=(Node(name="n", kind="stop"),),
    )
    assert _parse_manifest(manifest_to_toml(manifest)) == manifest


@pytest.mark.unit
def test_manifest_to_toml_omits_absent_fields_rather_than_writing_null() -> None:
    manifest = Manifest(name="p", version="0.1.0", description="d", entries=(), nodes=(Node(name="n", kind="stop"),))
    emitted = manifest_to_toml(manifest)
    assert "body" not in emitted
    assert "ports" not in emitted
    assert _parse_manifest(emitted).nodes[0] == Node(name="n", kind="stop")


@pytest.mark.unit
def test_manifest_from_dict_round_trips_every_real_fixture() -> None:
    for manifest in _real_manifests():
        assert manifest_from_dict(manifest_to_dict(manifest)) == manifest  # type: ignore[arg-type]


@pytest.mark.unit
def test_manifest_from_dict_raises_value_error_naming_a_missing_name() -> None:
    with pytest.raises(ValueError, match="'name'"):
        manifest_from_dict({"version": "0.1.0", "description": "d"})


@pytest.mark.unit
def test_manifest_from_dict_raises_value_error_naming_a_missing_kind() -> None:
    data = {"name": "p", "version": "0.1.0", "description": "d", "nodes": [{"name": "n"}]}
    with pytest.raises(ValueError, match="'kind'"):
        manifest_from_dict(data)


@pytest.mark.unit
def test_manifest_from_dict_rejects_a_kind_that_is_not_declared() -> None:
    """The only door a node kind arrives through from outside a hand-written
    file — `_parse_manifest` trusts what a programmer typed, and nothing
    downstream re-checks it."""
    data = {"name": "p", "version": "0.1.0", "description": "d", "nodes": [{"name": "n", "kind": "banana"}]}
    with pytest.raises(ValueError, match="banana"):
        manifest_from_dict(data)


@pytest.mark.unit
def test_manifest_from_dict_raises_value_error_for_non_list_ports() -> None:
    data = {
        "name": "p",
        "version": "0.1.0",
        "description": "d",
        "nodes": [{"name": "n", "kind": "route", "ports": "left"}],
    }
    with pytest.raises(ValueError, match="'ports'"):
        manifest_from_dict(data)
