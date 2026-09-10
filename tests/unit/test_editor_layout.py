"""Tests for sadana.editor_layout: positions()."""

from __future__ import annotations

import pytest

from sadana.editor_layout import positions
from sadana.plugins import Entry, Manifest, Node


def _manifest(*nodes: Node, start: str = "a") -> Manifest:
    return Manifest(
        name="p",
        version="0.1.0",
        description="d",
        entries=(Entry(tool="t", purpose="p", parameters="s.json", start=start),),
        nodes=nodes,
    )


@pytest.mark.unit
def test_every_declared_node_gets_a_position() -> None:
    manifest = _manifest(
        Node(name="a", kind="compute", body="init:f", next="b"),
        Node(name="b", kind="stop"),
    )
    assert set(positions(manifest)) == {"a", "b"}


@pytest.mark.unit
def test_a_successor_sits_to_the_right_of_the_entry_step() -> None:
    manifest = _manifest(
        Node(name="a", kind="compute", body="init:f", next="b"),
        Node(name="b", kind="stop"),
    )
    placed = positions(manifest)
    assert placed["b"][0] > placed["a"][0]


@pytest.mark.unit
def test_both_ports_of_a_route_sit_in_the_same_column_at_different_heights() -> None:
    manifest = _manifest(
        Node(name="a", kind="route", body="init:pick", ports=("left", "right")),
        Node(name="left", kind="stop"),
        Node(name="right", kind="stop"),
    )
    placed = positions(manifest)
    assert placed["left"][0] == placed["right"][0]
    assert placed["left"][1] != placed["right"][1]


@pytest.mark.unit
def test_positions_are_identical_across_calls() -> None:
    """The whole reason nothing is stored: reopening a plugin has to show the
    same picture, and that is this function's promise rather than a file's."""
    manifest = _manifest(
        Node(name="a", kind="route", body="init:pick", ports=("left", "right")),
        Node(name="left", kind="compute", body="init:f", next="right"),
        Node(name="right", kind="stop"),
    )
    assert positions(manifest) == positions(manifest)


@pytest.mark.unit
def test_an_unreachable_node_is_still_placed_and_set_apart() -> None:
    """Validation flags it; the editor still has to draw it, or a person can
    never see what they are being told to fix."""
    manifest = _manifest(
        Node(name="a", kind="compute", body="init:f", next="b"),
        Node(name="b", kind="stop"),
        Node(name="orphan", kind="stop"),
    )
    placed = positions(manifest)
    assert "orphan" in placed
    assert placed["orphan"][0] > placed["b"][0]


@pytest.mark.unit
def test_a_cycle_does_not_hang_the_layout() -> None:
    """A finished plugin is acyclic — validate() refuses otherwise — but a
    plugin halfway through being drawn is cyclic all the time."""
    manifest = _manifest(
        Node(name="a", kind="compute", body="init:f", next="b"),
        Node(name="b", kind="compute", body="init:f", next="a"),
    )
    assert set(positions(manifest)) == {"a", "b"}


@pytest.mark.unit
def test_a_node_two_paths_reach_lands_in_the_shallower_column() -> None:
    manifest = _manifest(
        Node(name="a", kind="route", body="init:pick", ports=("shortcut", "long")),
        Node(name="long", kind="compute", body="init:f", next="shortcut"),
        Node(name="shortcut", kind="stop"),
    )
    placed = positions(manifest)
    assert placed["shortcut"][0] == placed["long"][0]


@pytest.mark.unit
def test_a_manifest_with_no_entries_still_places_its_nodes() -> None:
    manifest = Manifest(
        name="p", version="0.1.0", description="d", entries=(), nodes=(Node(name="lonely", kind="stop"),)
    )
    assert set(positions(manifest)) == {"lonely"}
