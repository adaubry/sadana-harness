"""Tests for sadana.plugin_manifest: load_skill(), validate()."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from conftest import write_skill as _write_skill
from sadana.plugin_manifest import load_skill, validate
from sadana.plugins import (
    DanglingTarget,
    DuplicateNodeName,
    Entry,
    InvalidSchema,
    Manifest,
    ManifestParseError,
    Node,
    SkillLoadError,
    SkillRef,
    UnreachableNode,
    UnresolvedBody,
    UnresolvedSkill,
    Valid,
)

# ── load_skill ───────────────────────────────────────────────────────────


@pytest.mark.unit
def test_load_skill_returns_body_for_valid_skill(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = _write_skill(tmp_path, body="Do the thing, then stop.")
    monkeypatch.setenv("SADANA_PLUGINS_DIR", str(root))
    assert load_skill(SkillRef(plugin="p1", skill="s1")) == "Do the thing, then stop."


@pytest.mark.unit
def test_load_skill_raises_for_missing_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SADANA_PLUGINS_DIR", str(tmp_path / "plugins"))
    with pytest.raises(SkillLoadError):
        load_skill(SkillRef(plugin="nope", skill="nope"))


@pytest.mark.unit
def test_load_skill_raises_for_missing_frontmatter_delimiter(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    skill_dir = tmp_path / "plugins" / "p1" / "skills" / "s1"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("no frontmatter here")
    monkeypatch.setenv("SADANA_PLUGINS_DIR", str(tmp_path / "plugins"))
    with pytest.raises(SkillLoadError):
        load_skill(SkillRef(plugin="p1", skill="s1"))


@pytest.mark.unit
def test_load_skill_raises_for_unclosed_frontmatter(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    skill_dir = tmp_path / "plugins" / "p1" / "skills" / "s1"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("---\nname: s1\ndescription: x\nno closing delimiter")
    monkeypatch.setenv("SADANA_PLUGINS_DIR", str(tmp_path / "plugins"))
    with pytest.raises(SkillLoadError):
        load_skill(SkillRef(plugin="p1", skill="s1"))


@pytest.mark.unit
def test_load_skill_raises_for_name_mismatch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = _write_skill(tmp_path, name="a-different-name")
    monkeypatch.setenv("SADANA_PLUGINS_DIR", str(root))
    with pytest.raises(SkillLoadError):
        load_skill(SkillRef(plugin="p1", skill="s1"))


@pytest.mark.unit
def test_load_skill_raises_for_missing_description(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = _write_skill(tmp_path, description=None)
    monkeypatch.setenv("SADANA_PLUGINS_DIR", str(root))
    with pytest.raises(SkillLoadError):
        load_skill(SkillRef(plugin="p1", skill="s1"))


@pytest.mark.unit
def test_load_skill_raises_for_description_too_long(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = _write_skill(tmp_path, description="x" * 1025)
    monkeypatch.setenv("SADANA_PLUGINS_DIR", str(root))
    with pytest.raises(SkillLoadError):
        load_skill(SkillRef(plugin="p1", skill="s1"))


# ── validate() ───────────────────────────────────────────────────────────

_VALID_TOML = """
[plugin]
name = "example-plugin"
version = "0.3.1"
description = "One sentence, rendered verbatim into the catalog."

[[entry]]
tool = "example_do_thing"
purpose = "One sentence the model reads."
parameters = "schema/do_thing.json"
start = "fetch"

[[node]]
name = "fetch"
kind = "call"
body = "init:fetch_record"
next = "interpret"

[[node]]
name = "interpret"
kind = "ask"
skill = "example-skill"
next = "route_on_kind"

[[node]]
name = "route_on_kind"
kind = "route"
body = "init:classify"
ports = ["urgent", "normal"]

[[node]]
name = "urgent"
kind = "call"
body = "init:send_alert"

[[node]]
name = "normal"
kind = "stop"
"""


def _write_plugin(tmp_path: Path, *, toml: str = _VALID_TOML, dirname: str = "example-plugin") -> Path:
    """A well-formed plugin directory, matching `plugin_blueprint.md
    §5.1`'s layout and this test file's ``_VALID_TOML``'s graph. Callers
    overwrite one piece to produce each check's failure case. No
    ``SADANA_PLUGINS_DIR`` is set — ``validate()`` must answer for
    ``plugin_dir`` alone, never through the installed-plugins root
    ``load_skill()`` uses (that's the bug the regression test below
    covers)."""
    plugin_dir = tmp_path / dirname
    (plugin_dir / "schema").mkdir(parents=True)
    (plugin_dir / "schema" / "do_thing.json").write_text(json.dumps({"type": "object"}))
    skill_dir = plugin_dir / "skills" / "example-skill"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: example-skill\ndescription: Interprets a fetched record.\n---\nInterpret the record."
    )
    (plugin_dir / "init.py").write_text(
        "def fetch_record():\n"
        "    return {}\n\n\n"
        "def classify():\n"
        "    return 'urgent'\n\n\n"
        "def send_alert():\n"
        "    pass\n"
    )
    (plugin_dir / "plugin.toml").write_text(toml)
    return plugin_dir


@pytest.mark.unit
def test_validate_returns_valid_for_a_well_formed_plugin(tmp_path: Path) -> None:
    plugin_dir = _write_plugin(tmp_path)
    outcome = validate(plugin_dir)
    assert isinstance(outcome, Valid)
    assert outcome.manifest == Manifest(
        name="example-plugin",
        version="0.3.1",
        description="One sentence, rendered verbatim into the catalog.",
        entries=(
            Entry(
                tool="example_do_thing",
                purpose="One sentence the model reads.",
                parameters="schema/do_thing.json",
                start="fetch",
            ),
        ),
        nodes=(
            Node(name="fetch", kind="call", body="init:fetch_record", next="interpret"),
            Node(name="interpret", kind="ask", skill="example-skill", next="route_on_kind"),
            Node(name="route_on_kind", kind="route", body="init:classify", ports=("urgent", "normal")),
            Node(name="urgent", kind="call", body="init:send_alert"),
            Node(name="normal", kind="stop"),
        ),
    )


@pytest.mark.unit
def test_validate_returns_manifest_parse_error_for_missing_plugin_toml(tmp_path: Path) -> None:
    plugin_dir = _write_plugin(tmp_path)
    (plugin_dir / "plugin.toml").unlink()
    assert isinstance(validate(plugin_dir), ManifestParseError)


@pytest.mark.unit
def test_validate_returns_manifest_parse_error_for_malformed_toml(tmp_path: Path) -> None:
    plugin_dir = _write_plugin(tmp_path, toml="not valid [[[ toml")
    assert isinstance(validate(plugin_dir), ManifestParseError)


@pytest.mark.unit
def test_validate_returns_invalid_schema_for_missing_schema_file(tmp_path: Path) -> None:
    plugin_dir = _write_plugin(tmp_path)
    (plugin_dir / "schema" / "do_thing.json").unlink()
    outcome = validate(plugin_dir)
    assert isinstance(outcome, InvalidSchema)
    assert outcome.entry == "example_do_thing"


@pytest.mark.unit
def test_validate_returns_invalid_schema_for_a_structurally_invalid_schema(tmp_path: Path) -> None:
    plugin_dir = _write_plugin(tmp_path)
    (plugin_dir / "schema" / "do_thing.json").write_text(json.dumps({"type": "not-a-real-type"}))
    assert isinstance(validate(plugin_dir), InvalidSchema)


@pytest.mark.unit
def test_validate_returns_unresolved_skill_for_a_missing_skill(tmp_path: Path) -> None:
    toml = _VALID_TOML.replace('skill = "example-skill"', 'skill = "no-such-skill"')
    plugin_dir = _write_plugin(tmp_path, toml=toml)
    outcome = validate(plugin_dir)
    assert isinstance(outcome, UnresolvedSkill)
    assert outcome.node == "interpret"


@pytest.mark.unit
def test_validate_returns_duplicate_node_name_for_two_nodes_sharing_a_name(tmp_path: Path) -> None:
    toml = _VALID_TOML.replace('name = "normal"\nkind = "stop"', 'name = "urgent"\nkind = "stop"')
    plugin_dir = _write_plugin(tmp_path, toml=toml)
    outcome = validate(plugin_dir)
    assert isinstance(outcome, DuplicateNodeName)
    assert outcome.name == "urgent"


@pytest.mark.unit
def test_validate_returns_dangling_target_for_a_next_with_no_such_node(tmp_path: Path) -> None:
    toml = _VALID_TOML.replace(
        'next = "interpret"\n\n[[node]]\nname = "interpret"', 'next = "nope"\n\n[[node]]\nname = "interpret"', 1
    )
    plugin_dir = _write_plugin(tmp_path, toml=toml)
    outcome = validate(plugin_dir)
    assert isinstance(outcome, DanglingTarget)
    assert outcome.node == "fetch"
    assert outcome.target == "nope"


@pytest.mark.unit
def test_validate_returns_unresolved_body_for_a_missing_function(tmp_path: Path) -> None:
    toml = _VALID_TOML.replace('body = "init:fetch_record"', 'body = "init:no_such_function"')
    plugin_dir = _write_plugin(tmp_path, toml=toml)
    outcome = validate(plugin_dir)
    assert isinstance(outcome, UnresolvedBody)
    assert outcome.node == "fetch"
    assert outcome.body == "init:no_such_function"


@pytest.mark.unit
def test_validate_returns_unreachable_node_for_a_node_no_edge_reaches(tmp_path: Path) -> None:
    toml = _VALID_TOML + '\n[[node]]\nname = "orphan"\nkind = "stop"\n'
    plugin_dir = _write_plugin(tmp_path, toml=toml)
    outcome = validate(plugin_dir)
    assert isinstance(outcome, UnreachableNode)
    assert outcome.node == "orphan"


@pytest.mark.unit
def test_validate_returns_dangling_target_for_an_entry_start_with_no_such_node(
    tmp_path: Path,
) -> None:
    toml = _VALID_TOML.replace('start = "fetch"', 'start = "no-such-node"')
    plugin_dir = _write_plugin(tmp_path, toml=toml)
    outcome = validate(plugin_dir)
    assert isinstance(outcome, DanglingTarget)
    assert outcome.node == "example_do_thing"
    assert outcome.target == "no-such-node"


@pytest.mark.unit
def test_validate_resolves_skill_against_plugin_dir_not_the_installed_plugins_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression: `_check_skill` must resolve a SKILL.md against the
    `plugin_dir` `validate()` was actually called with, never through
    `SADANA_PLUGINS_DIR`/`_plugins_root()` — that's load_skill()'s own
    installed-plugin resolution, a different root `validate()` must not
    depend on. `plugin_dir`'s basename ("not-the-manifest-name") deliberately
    differs from `_VALID_TOML`'s `[plugin].name` ("example-plugin"), and
    `SADANA_PLUGINS_DIR` is pointed at an unrelated, empty directory."""
    plugin_dir = _write_plugin(tmp_path, dirname="not-the-manifest-name")
    monkeypatch.setenv("SADANA_PLUGINS_DIR", str(tmp_path / "unrelated-empty-root"))
    outcome = validate(plugin_dir)
    assert isinstance(outcome, Valid)


@pytest.mark.unit
def test_validate_returns_manifest_parse_error_for_invalid_utf8(tmp_path: Path) -> None:
    plugin_dir = _write_plugin(tmp_path)
    (plugin_dir / "plugin.toml").write_bytes(b'[plugin]\nname = "bad: \xff\xfe"\n')
    assert isinstance(validate(plugin_dir), ManifestParseError)


@pytest.mark.unit
def test_validate_returns_invalid_schema_for_invalid_utf8_schema_file(tmp_path: Path) -> None:
    plugin_dir = _write_plugin(tmp_path)
    (plugin_dir / "schema" / "do_thing.json").write_bytes(b"\xff\xfe not json either")
    outcome = validate(plugin_dir)
    assert isinstance(outcome, InvalidSchema)
    assert outcome.entry == "example_do_thing"


@pytest.mark.unit
def test_validate_returns_unresolved_skill_for_invalid_utf8_skill_md(tmp_path: Path) -> None:
    plugin_dir = _write_plugin(tmp_path)
    (plugin_dir / "skills" / "example-skill" / "SKILL.md").write_bytes(b"---\nname: \xff\xfe\n---\nbody")
    outcome = validate(plugin_dir)
    assert isinstance(outcome, UnresolvedSkill)
    assert outcome.node == "interpret"
