"""Tests for sadana.plugin_manifest: load_skill(), validate(),
discover_plugins(), run_graph()."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from conftest import write_skill as _write_skill
from sadana.plugin_manifest import _default_approve, discover_plugins, load_skill, run_graph, validate
from sadana.plugins import (
    CyclicGraph,
    DanglingTarget,
    DuplicateNodeName,
    Entry,
    InvalidSchema,
    Manifest,
    ManifestParseError,
    Node,
    NodeTrace,
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
def test_validate_returns_cyclic_graph_for_a_route_back_edge(tmp_path: Path) -> None:
    """§8's original seven checks prove every node is reachable; none of
    them prove the graph is acyclic. `normal` routing back to
    `route_on_kind` (instead of `_VALID_TOML`'s own terminal `stop`) is
    reachable from the entry and reaches every node, same as before — the
    only thing wrong with it is that it never ends."""
    toml = _VALID_TOML.replace(
        'name = "normal"\nkind = "stop"',
        'name = "normal"\nkind = "route"\nbody = "init:classify"\nports = ["route_on_kind"]',
    )
    plugin_dir = _write_plugin(tmp_path, toml=toml)
    outcome = validate(plugin_dir)
    assert isinstance(outcome, CyclicGraph)
    assert outcome.node in ("normal", "route_on_kind")


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


# ── discover_plugins ─────────────────────────────────────────────────────


@pytest.mark.unit
def test_discover_plugins_keeps_only_valid_plugins(tmp_path: Path) -> None:
    root = tmp_path / "plugins"
    root.mkdir()
    _write_plugin(root, dirname="good-plugin")
    (root / "broken-plugin").mkdir()  # no plugin.toml at all — not Valid
    installed = discover_plugins(root)
    assert [p.name for p in installed] == ["example-plugin"]
    assert installed[0].directory == root / "good-plugin"
    assert installed[0].manifest.name == "example-plugin"


@pytest.mark.unit
def test_discover_plugins_ignores_non_directory_entries(tmp_path: Path) -> None:
    root = tmp_path / "plugins"
    root.mkdir()
    (root / "not-a-plugin.txt").write_text("stray file")
    assert discover_plugins(root) == ()


@pytest.mark.unit
def test_discover_plugins_returns_empty_tuple_for_a_missing_root(tmp_path: Path) -> None:
    assert discover_plugins(tmp_path / "does-not-exist") == ()


@pytest.mark.unit
def test_discover_plugins_defaults_to_the_configured_plugins_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "plugins"
    root.mkdir()
    _write_plugin(root, dirname="good-plugin")
    monkeypatch.setenv("SADANA_PLUGINS_DIR", str(root))
    assert [p.name for p in discover_plugins()] == ["example-plugin"]


# ── run_graph ────────────────────────────────────────────────────────────

# run_graph() takes an already-parsed Manifest — it never reads
# plugin.toml itself, so these fixtures write only whatever init.py a
# test's compute/route nodes need, not a full plugin directory.


def _write_init_py(tmp_path: Path, body: str, *, dirname: str = "graph-plugin") -> Path:
    plugin_dir = tmp_path / dirname
    plugin_dir.mkdir()
    (plugin_dir / "init.py").write_text(body)
    return plugin_dir


def _entry(start: str) -> Entry:
    return Entry(tool="do_thing", purpose="p", parameters="schema/do_thing.json", start=start)


# ── F1: _default_approve ─────────────────────────────────────────────────


@pytest.mark.unit
@pytest.mark.parametrize("answer", ["y", "yes", "Y", "YES"])
def test_default_approve_accepts_yes(monkeypatch: pytest.MonkeyPatch, answer: str) -> None:
    monkeypatch.setattr("builtins.input", lambda _prompt: answer)
    assert asyncio.run(_default_approve("p", "n", {"a": 1})) is True


@pytest.mark.unit
@pytest.mark.parametrize("answer", ["", "n", "no", "maybe"])
def test_default_approve_rejects_anything_else(monkeypatch: pytest.MonkeyPatch, answer: str) -> None:
    monkeypatch.setattr("builtins.input", lambda _prompt: answer)
    assert asyncio.run(_default_approve("p", "n", {"a": 1})) is False


def _manifest(*nodes: Node, name: str = "p") -> Manifest:
    return Manifest(name=name, version="0.1.0", description="d", entries=(), nodes=nodes)


async def _stub_ask_ok(_skill: SkillRef, text: str) -> str:
    return f"child said: {text}"


async def _stub_ask_fails(_skill: SkillRef, _text: str) -> str | None:
    return None


async def _stub_approve_ok(_plugin: str, _node: str, _value: object) -> bool:
    return True


async def _stub_approve_denied(_plugin: str, _node: str, _value: object) -> bool:
    return False


@pytest.mark.unit
def test_run_graph_stop_node_ends_the_walk(tmp_path: Path) -> None:
    plugin_dir = _write_init_py(tmp_path, "")
    manifest = _manifest(Node(name="done", kind="stop"))
    result = asyncio.run(run_graph(plugin_dir, manifest, _entry("done"), {"a": 1}, ask=_stub_ask_ok))
    assert result.failed_node is None
    assert result.text == json.dumps({"a": 1})
    assert result.trace == (NodeTrace(node="done", kind="stop", visit=0, ok=True, port=None, detail=None),)


@pytest.mark.unit
def test_run_graph_compute_chain_gets_only_the_immediate_predecessor(tmp_path: Path) -> None:
    """Requirement 6: a node's body is given only what the step
    immediately before it produced. Each function below raises if handed
    anything other than exactly its own predecessor's return value — an
    accidental "pass everything" implementation fails this test, not
    merely looks different."""
    plugin_dir = _write_init_py(
        tmp_path,
        "def step_one(value):\n"
        "    assert value == {'raw': True}, value\n"
        "    return 'one'\n\n"
        "def step_two(value):\n"
        "    assert value == 'one', value\n"
        "    return 'two'\n\n"
        "def step_three(value):\n"
        "    assert value == 'two', value\n"
        "    return 'three'\n",
    )
    nodes = (
        Node(name="a", kind="compute", body="init:step_one", next="b"),
        Node(name="b", kind="compute", body="init:step_two", next="c"),
        Node(name="c", kind="compute", body="init:step_three"),
    )
    manifest = _manifest(*nodes)
    result = asyncio.run(run_graph(plugin_dir, manifest, _entry("a"), {"raw": True}, ask=_stub_ask_ok))
    assert result.failed_node is None
    assert result.text == "three"
    assert [t.node for t in result.trace] == ["a", "b", "c"]
    assert all(t.ok for t in result.trace)


@pytest.mark.unit
def test_run_graph_route_follows_the_named_port(tmp_path: Path) -> None:
    plugin_dir = _write_init_py(tmp_path, "def pick(value):\n    return 'left' if value['flag'] else 'right'\n")
    nodes = (
        Node(name="branch", kind="route", body="init:pick", ports=("left", "right")),
        Node(name="left", kind="stop"),
        Node(name="right", kind="stop"),
    )
    manifest = _manifest(*nodes)
    result = asyncio.run(run_graph(plugin_dir, manifest, _entry("branch"), {"flag": True}, ask=_stub_ask_ok))
    assert result.failed_node is None
    assert result.text == json.dumps({"flag": True})  # route decides, it doesn't transform
    assert [t.node for t in result.trace] == ["branch", "left"]
    assert result.trace[0].port == "left"


@pytest.mark.unit
def test_run_graph_route_returning_an_undeclared_port_fails_closed(tmp_path: Path) -> None:
    plugin_dir = _write_init_py(tmp_path, "def pick(value):\n    return 'nowhere'\n")
    manifest = _manifest(Node(name="branch", kind="route", body="init:pick", ports=("left", "right")))
    result = asyncio.run(run_graph(plugin_dir, manifest, _entry("branch"), {}, ask=_stub_ask_ok))
    assert result.failed_node == "branch"
    assert result.trace[-1].ok is False


@pytest.mark.unit
def test_run_graph_a_raising_body_fails_closed_without_leaking_the_exception(tmp_path: Path) -> None:
    plugin_dir = _write_init_py(tmp_path, "def boom(value):\n    raise KeyError('super secret internal detail')\n")
    manifest = _manifest(Node(name="explode", kind="compute", body="init:boom"))
    result = asyncio.run(run_graph(plugin_dir, manifest, _entry("explode"), {}, ask=_stub_ask_ok))
    assert result.failed_node == "explode"
    assert "super secret internal detail" not in result.text
    assert "Traceback" not in result.text
    assert result.trace[-1].ok is False


@pytest.mark.unit
def test_run_graph_call_node_runs_its_body_and_threads_the_result(tmp_path: Path) -> None:
    plugin_dir = _write_init_py(tmp_path, "def do_call(value):\n    return {'called': value}\n")
    nodes = (
        Node(name="reach_out", kind="call", body="init:do_call", next="done"),
        Node(name="done", kind="stop"),
    )
    manifest = _manifest(*nodes)
    result = asyncio.run(
        run_graph(plugin_dir, manifest, _entry("reach_out"), {"a": 1}, ask=_stub_ask_ok, approve=_stub_approve_ok)
    )
    assert result.failed_node is None
    assert result.text == json.dumps({"called": {"a": 1}})
    assert [t.node for t in result.trace] == ["reach_out", "done"]


@pytest.mark.unit
def test_run_graph_call_node_raising_body_fails_closed(tmp_path: Path) -> None:
    plugin_dir = _write_init_py(tmp_path, "def boom(value):\n    raise KeyError('super secret internal detail')\n")
    manifest = _manifest(Node(name="explode", kind="call", body="init:boom"))
    result = asyncio.run(
        run_graph(plugin_dir, manifest, _entry("explode"), {}, ask=_stub_ask_ok, approve=_stub_approve_ok)
    )
    assert result.failed_node == "explode"
    assert "super secret internal detail" not in result.text
    assert "Traceback" not in result.text
    assert result.trace[-1].ok is False


@pytest.mark.unit
def test_run_graph_call_node_declined(tmp_path: Path) -> None:
    plugin_dir = _write_init_py(tmp_path, "")
    manifest = _manifest(Node(name="future", kind="call", body="init:whatever"))
    result = asyncio.run(
        run_graph(plugin_dir, manifest, _entry("future"), {}, ask=_stub_ask_ok, approve=_stub_approve_denied)
    )
    assert result.failed_node == "future"
    assert result.trace[-1].detail == "declined"


@pytest.mark.unit
def test_run_graph_call_node_records_what_was_asked(tmp_path: Path) -> None:
    plugin_dir = _write_init_py(tmp_path, "")
    seen: list[tuple[str, str, object]] = []

    async def _capturing_approve(plugin: str, node: str, value: object) -> bool:
        seen.append((plugin, node, value))
        return False

    manifest = _manifest(Node(name="future", kind="call", body="init:whatever"), name="my-plugin")
    asyncio.run(
        run_graph(plugin_dir, manifest, _entry("future"), {"a": 1}, ask=_stub_ask_ok, approve=_capturing_approve)
    )
    assert seen == [("my-plugin", "future", {"a": 1})]


@pytest.mark.unit
def test_run_graph_refuses_each_nodes(tmp_path: Path) -> None:
    plugin_dir = _write_init_py(tmp_path, "")
    manifest = _manifest(Node(name="future", kind="each"))
    result = asyncio.run(run_graph(plugin_dir, manifest, _entry("future"), {}, ask=_stub_ask_ok))
    assert result.failed_node == "future"


@pytest.mark.unit
def test_run_graph_refuses_wait_nodes(tmp_path: Path) -> None:
    plugin_dir = _write_init_py(tmp_path, "")
    manifest = _manifest(Node(name="future", kind="wait"))
    result = asyncio.run(run_graph(plugin_dir, manifest, _entry("future"), {}, ask=_stub_ask_ok))
    assert result.failed_node == "future"


@pytest.mark.unit
def test_run_graph_ask_success_becomes_the_next_predecessor_value(tmp_path: Path) -> None:
    plugin_dir = _write_init_py(tmp_path, "")
    nodes = (Node(name="interpret", kind="ask", skill="example-skill", next="done"), Node(name="done", kind="stop"))
    manifest = _manifest(*nodes)
    result = asyncio.run(run_graph(plugin_dir, manifest, _entry("interpret"), {"raw": True}, ask=_stub_ask_ok))
    assert result.failed_node is None
    assert result.text == "child said: " + json.dumps({"raw": True})
    assert [t.node for t in result.trace] == ["interpret", "done"]


@pytest.mark.unit
def test_run_graph_ask_returning_none_fails_closed(tmp_path: Path) -> None:
    plugin_dir = _write_init_py(tmp_path, "")
    manifest = _manifest(Node(name="interpret", kind="ask", skill="example-skill"))
    result = asyncio.run(run_graph(plugin_dir, manifest, _entry("interpret"), {}, ask=_stub_ask_fails))
    assert result.failed_node == "interpret"


@pytest.mark.unit
def test_run_graph_ask_is_given_the_nodes_declared_skill(tmp_path: Path) -> None:
    plugin_dir = _write_init_py(tmp_path, "")
    seen: list[SkillRef] = []

    async def _capturing_ask(skill: SkillRef, _text: str) -> str:
        seen.append(skill)
        return "ok"

    manifest = _manifest(Node(name="interpret", kind="ask", skill="example-skill"), name="my-plugin")
    asyncio.run(run_graph(plugin_dir, manifest, _entry("interpret"), {}, ask=_capturing_ask))
    assert seen == [SkillRef(plugin="my-plugin", skill="example-skill")]


@pytest.mark.unit
def test_run_graph_walks_compute_ask_route_and_stop_together(tmp_path: Path) -> None:
    """Also covers spec's own acceptance criterion that compute/ask/route/
    stop never call `approve`: `approve` here raises if called at all, so a
    regression that reaches it for any of these four kinds fails this test
    rather than passing by omission."""
    plugin_dir = _write_init_py(
        tmp_path,
        "def fetch(value):\n    return {'from_fetch': value}\n\ndef classify(value):\n    return 'urgent'\n",
    )
    nodes = (
        Node(name="fetch", kind="compute", body="init:fetch", next="interpret"),
        Node(name="interpret", kind="ask", skill="example-skill", next="route_on_kind"),
        Node(name="route_on_kind", kind="route", body="init:classify", ports=("urgent", "normal")),
        Node(name="urgent", kind="stop"),
        Node(name="normal", kind="stop"),
    )
    manifest = _manifest(*nodes)

    async def _approve_must_not_be_called(_plugin: str, _node: str, _value: object) -> bool:
        raise AssertionError("approve must not be called for compute/ask/route/stop nodes")

    result = asyncio.run(
        run_graph(
            plugin_dir, manifest, _entry("fetch"), {"raw": True}, ask=_stub_ask_ok, approve=_approve_must_not_be_called
        )
    )
    assert result.failed_node is None
    assert [t.node for t in result.trace] == ["fetch", "interpret", "route_on_kind", "urgent"]
    assert all(t.ok for t in result.trace)
    assert result.trace[2].port == "urgent"
    assert result.text == "child said: " + json.dumps({"from_fetch": {"raw": True}})
