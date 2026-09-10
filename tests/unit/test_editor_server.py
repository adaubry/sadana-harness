"""Tests for sadana.editor_server: handle().

The socket is deliberately absent — `handle()` takes a method, a path and a
body, so the whole API surface is reachable here, and only the byte-shuffling
shell around it waits for `scripts/prove_editor_e2e.py`.

`plugins_root` is passed explicitly on every call rather than read from the
environment, so nothing here can reach a real plugins directory even if the
isolated-state fixture were ever to change.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from sadana.editor_server import handle
from sadana.plugins import _parse_manifest


def _body(response: object) -> dict:
    return json.loads(response.body)  # type: ignore[attr-defined]


def _create(root: Path, name: str = "my-plugin") -> object:
    return handle("POST", "/api/plugins", json.dumps({"name": name}).encode(), plugins_root=root)


# ── creating ─────────────────────────────────────────────────────────────


@pytest.mark.unit
def test_creating_a_plugin_writes_a_manifest_and_its_schema(tmp_path: Path) -> None:
    response = _create(tmp_path)

    assert response.status == 201  # type: ignore[attr-defined]
    assert (tmp_path / "my-plugin" / "plugin.toml").is_file()
    assert (tmp_path / "my-plugin" / "schema" / "my_plugin_entry.json").is_file()


@pytest.mark.unit
def test_a_newly_created_plugin_is_valid_from_its_first_moment(tmp_path: Path) -> None:
    """It starts as something that works and is edited toward what the person
    meant, rather than starting broken."""
    payload = _body(_create(tmp_path))

    assert payload["problems"] == []
    assert payload["waiting"] == []


@pytest.mark.unit
def test_creating_the_same_plugin_twice_is_a_conflict(tmp_path: Path) -> None:
    _create(tmp_path)
    assert _create(tmp_path).status == 409  # type: ignore[attr-defined]


@pytest.mark.unit
def test_creating_without_a_name_is_a_bad_request(tmp_path: Path) -> None:
    response = handle("POST", "/api/plugins", b'{"nope": 1}', plugins_root=tmp_path)
    assert response.status == 400  # type: ignore[attr-defined]


@pytest.mark.unit
def test_creating_with_malformed_json_is_a_bad_request_not_a_crash(tmp_path: Path) -> None:
    response = handle("POST", "/api/plugins", b"{not json", plugins_root=tmp_path)
    assert response.status == 400  # type: ignore[attr-defined]


# ── names that become paths ──────────────────────────────────────────────


@pytest.mark.unit
@pytest.mark.parametrize("name", ["../escape", "a/b", "/etc/passwd", "..", "Upper", "-leading", "with space", ""])
def test_an_unsafe_name_is_refused_and_touches_no_filesystem(tmp_path: Path, name: str) -> None:
    """The allowlist runs before anything reaches a path at all, so the proof
    is that the tree is untouched — not merely that the status was 400."""
    outside = tmp_path.parent / "outside-marker"
    before = sorted(p.name for p in tmp_path.iterdir())

    created = handle("POST", "/api/plugins", json.dumps({"name": name}).encode(), plugins_root=tmp_path)
    fetched = handle("GET", f"/api/plugins/{name}", b"", plugins_root=tmp_path)

    assert created.status == 400  # type: ignore[attr-defined]
    assert fetched.status in (400, 404)  # type: ignore[attr-defined]
    assert sorted(p.name for p in tmp_path.iterdir()) == before
    assert not outside.exists()


# ── reading ──────────────────────────────────────────────────────────────


@pytest.mark.unit
def test_listing_reports_a_status_word_per_plugin(tmp_path: Path) -> None:
    _create(tmp_path, "ready-one")
    payload = _body(handle("GET", "/api/plugins", b"", plugins_root=tmp_path))

    assert payload["plugins"] == [{"name": "ready-one", "status": "ready"}]


@pytest.mark.unit
def test_listing_an_absent_root_is_empty_rather_than_an_error(tmp_path: Path) -> None:
    payload = _body(handle("GET", "/api/plugins", b"", plugins_root=tmp_path / "nothing-here"))
    assert payload["plugins"] == []


@pytest.mark.unit
def test_listing_ignores_a_directory_with_no_manifest(tmp_path: Path) -> None:
    (tmp_path / "not-a-plugin").mkdir()
    payload = _body(handle("GET", "/api/plugins", b"", plugins_root=tmp_path))
    assert payload["plugins"] == []


@pytest.mark.unit
def test_reading_a_plugin_returns_a_position_for_every_step(tmp_path: Path) -> None:
    payload = _body(_create(tmp_path))
    names = {node["name"] for node in payload["manifest"]["nodes"]}
    assert set(payload["positions"]) == names


@pytest.mark.unit
def test_reading_an_unknown_plugin_is_not_found(tmp_path: Path) -> None:
    assert handle("GET", "/api/plugins/absent", b"", plugins_root=tmp_path).status == 404  # type: ignore[attr-defined]


# ── saving ───────────────────────────────────────────────────────────────


def _saved(root: Path, name: str, mutate) -> dict:  # type: ignore[no-untyped-def]
    manifest = _body(handle("GET", f"/api/plugins/{name}", b"", plugins_root=root))["manifest"]
    mutate(manifest)
    return _body(handle("PUT", f"/api/plugins/{name}", json.dumps(manifest).encode(), plugins_root=root))


@pytest.mark.unit
def test_saving_a_changed_step_lands_in_the_file_on_disk(tmp_path: Path) -> None:
    _create(tmp_path)

    def add_a_step(manifest: dict) -> None:
        manifest["nodes"][0]["kind"] = "ask"
        manifest["nodes"][0]["skill"] = "some-skill"
        manifest["nodes"][0]["next"] = "step_2"
        manifest["nodes"].append({"name": "step_2", "kind": "stop", "ports": []})

    _saved(tmp_path, "my-plugin", add_a_step)

    written = _parse_manifest((tmp_path / "my-plugin" / "plugin.toml").read_text(encoding="utf-8"))
    assert [n.name for n in written.nodes] == ["step_1", "step_2"]
    assert written.nodes[0].kind == "ask"
    assert written.nodes[0].skill == "some-skill"


@pytest.mark.unit
def test_an_ask_step_waits_for_its_skill_file_rather_than_being_a_problem(tmp_path: Path) -> None:
    """Found by running the editor for the first time, not by writing this
    test: `intent.md` and `spec.md` both said an `ask` step needs nothing
    from anyone, and it needs a SKILL.md the editor gives no way to write.
    Reporting that as a hard validation problem while a missing body was a
    friendly to-do told one person two stories about one situation."""
    _create(tmp_path)

    def asks_a_skill_nobody_wrote(manifest: dict) -> None:
        manifest["nodes"][0]["kind"] = "ask"
        manifest["nodes"][0]["skill"] = "unwritten-skill"

    payload = _saved(tmp_path, "my-plugin", asks_a_skill_nobody_wrote)

    assert payload["waiting"] == [{"step": "step_1", "for": "a skill file"}]
    assert payload["problems"] == []


@pytest.mark.unit
def test_an_ask_step_with_a_real_skill_file_waits_for_nothing(tmp_path: Path) -> None:
    _create(tmp_path)
    skill = tmp_path / "my-plugin" / "skills" / "written-skill"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text("---\nname: written-skill\ndescription: d\n---\nDo the thing.\n")

    def asks_a_real_skill(manifest: dict) -> None:
        manifest["nodes"][0]["kind"] = "ask"
        manifest["nodes"][0]["skill"] = "written-skill"

    payload = _saved(tmp_path, "my-plugin", asks_a_real_skill)

    assert payload["waiting"] == []
    assert payload["problems"] == []


@pytest.mark.unit
def test_saving_a_step_whose_code_is_missing_succeeds_and_says_what_is_waiting(tmp_path: Path) -> None:
    """Requirement 7: an unfinished plugin is a normal thing to save. If this
    were gated on validate(), it would be refused with UnresolvedBody."""
    _create(tmp_path)

    def needs_a_programmer(manifest: dict) -> None:
        manifest["nodes"][0]["kind"] = "compute"
        manifest["nodes"][0]["body"] = "init:not_written_yet"

    payload = _saved(tmp_path, "my-plugin", needs_a_programmer)

    assert payload["waiting"] == [{"step": "step_1", "for": "code"}]
    assert payload["problems"] == []  # a missing body is not a problem, it is a to-do


@pytest.mark.unit
def test_a_step_whose_code_exists_is_not_reported_as_waiting(tmp_path: Path) -> None:
    _create(tmp_path)
    (tmp_path / "my-plugin" / "init.py").write_text("def written(value):\n    return value\n")

    def points_at_real_code(manifest: dict) -> None:
        manifest["nodes"][0]["kind"] = "compute"
        manifest["nodes"][0]["body"] = "init:written"

    assert _saved(tmp_path, "my-plugin", points_at_real_code)["waiting"] == []


@pytest.mark.unit
def test_a_body_check_never_imports_the_plugins_code(tmp_path: Path) -> None:
    """`_waiting` reads the file with `ast`; importing it would run the
    module's top level. The sentinel proves the difference."""
    _create(tmp_path)
    sentinel = tmp_path / "executed"
    (tmp_path / "my-plugin" / "init.py").write_text(
        "import pathlib\n"
        f"pathlib.Path({str(sentinel)!r}).write_text('ran')\n\n\n"
        "def written(value):\n    return value\n"
    )

    def points_at_real_code(manifest: dict) -> None:
        manifest["nodes"][0]["kind"] = "compute"
        manifest["nodes"][0]["body"] = "init:written"

    assert _saved(tmp_path, "my-plugin", points_at_real_code)["waiting"] == []
    assert not sentinel.exists()


@pytest.mark.unit
def test_a_drawing_mistake_is_reported_as_a_problem_but_still_saved(tmp_path: Path) -> None:
    """An arrow to a step that does not exist is exactly what a person does
    mid-draw. They are told; they are not blocked."""
    _create(tmp_path)

    def arrow_to_nowhere(manifest: dict) -> None:
        manifest["nodes"][0]["kind"] = "compute"
        manifest["nodes"][0]["body"] = "init:f"
        manifest["nodes"][0]["next"] = "does-not-exist"

    payload = _saved(tmp_path, "my-plugin", arrow_to_nowhere)

    assert payload["problems"] != []
    assert (tmp_path / "my-plugin" / "plugin.toml").read_text(encoding="utf-8").count("does-not-exist") == 1


@pytest.mark.unit
def test_saving_a_manifest_with_an_undeclared_kind_is_refused(tmp_path: Path) -> None:
    _create(tmp_path)

    def nonsense_kind(manifest: dict) -> None:
        manifest["nodes"][0]["kind"] = "banana"

    manifest = _body(handle("GET", "/api/plugins/my-plugin", b"", plugins_root=tmp_path))["manifest"]
    nonsense_kind(manifest)
    response = handle("PUT", "/api/plugins/my-plugin", json.dumps(manifest).encode(), plugins_root=tmp_path)

    assert response.status == 400  # type: ignore[attr-defined]
    assert "banana" in _body(response)["error"]


# ── the palette, and routing ─────────────────────────────────────────────


@pytest.mark.unit
def test_the_palette_offers_every_declared_kind_and_marks_the_unrunnable_one(tmp_path: Path) -> None:
    kinds = _body(handle("GET", "/api/kinds", b"", plugins_root=tmp_path))["kinds"]
    by_kind = {entry["kind"]: entry for entry in kinds}

    assert by_kind["each"]["runnable"] is False
    assert by_kind["wait"]["runnable"] is True
    assert by_kind["route"]["uses"] == ["body", "ports"]


@pytest.mark.unit
def test_the_page_is_served_at_the_root(tmp_path: Path) -> None:
    response = handle("GET", "/", b"", plugins_root=tmp_path)
    assert response.status == 200  # type: ignore[attr-defined]
    assert response.content_type.startswith("text/html")  # type: ignore[attr-defined]


@pytest.mark.unit
@pytest.mark.parametrize("filename", ["../../secrets.txt", "..%2Fescape.js", "nope.py", "editor.js/../x"])
def test_an_asset_path_that_escapes_is_not_found(tmp_path: Path, filename: str) -> None:
    response = handle("GET", f"/assets/{filename}", b"", plugins_root=tmp_path)
    assert response.status == 404  # type: ignore[attr-defined]


@pytest.mark.unit
def test_an_unknown_route_is_not_found(tmp_path: Path) -> None:
    assert handle("DELETE", "/api/plugins/x", b"", plugins_root=tmp_path).status == 404  # type: ignore[attr-defined]


# ── what a self-check found (see review.md) ──────────────────────────────


@pytest.mark.unit
def test_a_string_toml_cannot_carry_is_refused_and_the_file_is_untouched(tmp_path: Path) -> None:
    """A lone surrogate — half a pasted emoji — is exactly what a browser's
    own `JSON.stringify` sends. It used to reach `write_text`, which had
    already truncated the plugin to zero bytes by the time the encode
    failed."""
    _create(tmp_path)
    before = (tmp_path / "my-plugin" / "plugin.toml").read_text(encoding="utf-8")

    def lone_surrogate(manifest: dict) -> None:
        manifest["description"] = "half an emoji: \ud800"

    manifest = _body(handle("GET", "/api/plugins/my-plugin", b"", plugins_root=tmp_path))["manifest"]
    lone_surrogate(manifest)
    response = handle("PUT", "/api/plugins/my-plugin", json.dumps(manifest).encode(), plugins_root=tmp_path)

    assert response.status == 400  # type: ignore[attr-defined]
    assert (tmp_path / "my-plugin" / "plugin.toml").read_text(encoding="utf-8") == before


@pytest.mark.unit
def test_a_delete_character_round_trips_rather_than_bricking_the_plugin(tmp_path: Path) -> None:
    """U+007F is legal in JSON and forbidden raw in TOML, so it used to
    produce a `plugin.toml` that saved and then could never be reopened."""
    _create(tmp_path)

    def with_a_delete_character(manifest: dict) -> None:
        manifest["description"] = "a\x7fb"

    payload = _saved(tmp_path, "my-plugin", with_a_delete_character)

    assert payload["manifest"]["description"] == "a\x7fb"
    reopened = handle("GET", "/api/plugins/my-plugin", b"", plugins_root=tmp_path)
    assert reopened.status == 200  # type: ignore[attr-defined]


@pytest.mark.unit
def test_a_name_too_long_for_the_filesystem_is_refused_cleanly(tmp_path: Path) -> None:
    """It used to pass every check here and then raise OSError from the
    first `is_file()`, escaping as a 500 that quoted the absolute path."""
    response = handle("GET", f"/api/plugins/{'a' * 400}", b"", plugins_root=tmp_path)

    assert response.status == 400  # type: ignore[attr-defined]
    assert str(tmp_path) not in _body(response)["error"]


@pytest.mark.unit
def test_a_plugins_name_cannot_be_made_to_disagree_with_its_folder(tmp_path: Path) -> None:
    """`discover_plugins` keys a plugin's identity off the manifest, so two
    folders could otherwise claim one name."""
    _create(tmp_path)

    def rename_the_plugin(manifest: dict) -> None:
        manifest["name"] = "totally-other"

    manifest = _body(handle("GET", "/api/plugins/my-plugin", b"", plugins_root=tmp_path))["manifest"]
    rename_the_plugin(manifest)
    response = handle("PUT", "/api/plugins/my-plugin", json.dumps(manifest).encode(), plugins_root=tmp_path)

    assert response.status == 400  # type: ignore[attr-defined]
    assert _parse_manifest((tmp_path / "my-plugin" / "plugin.toml").read_text(encoding="utf-8")).name == "my-plugin"


@pytest.mark.unit
@pytest.mark.parametrize(
    "headers",
    [
        {"Host": "plugins.evil.example"},
        {"Host": "127.0.0.1:8770", "Origin": "https://evil.example"},
    ],
)
def test_a_request_from_another_website_is_refused(tmp_path: Path, headers: dict) -> None:
    """Binding to loopback keeps other machines out; it is not access
    control against a browser, which will send any site's form to
    127.0.0.1. A self-check demonstrated both the `text/plain` form shape
    and the DNS-rebinding shape reaching this server."""
    response = handle("POST", "/api/plugins", b'{"name": "evil"}', plugins_root=tmp_path, headers=headers)

    assert response.status == 403  # type: ignore[attr-defined]
    assert not (tmp_path / "evil").exists()


@pytest.mark.unit
def test_a_request_from_this_machines_own_page_is_served(tmp_path: Path) -> None:
    response = handle(
        "GET",
        "/api/plugins",
        b"",
        plugins_root=tmp_path,
        headers={"Host": "127.0.0.1:8770", "Origin": "http://127.0.0.1:8770"},
    )
    assert response.status == 200  # type: ignore[attr-defined]


# ── graph edits, moved out of the browser ────────────────────────────────


def _step_op(root: Path, name: str, payload: dict) -> object:
    return handle("POST", f"/api/plugins/{name}/steps", json.dumps(payload).encode(), plugins_root=root)


@pytest.mark.unit
def test_adding_a_step_mints_a_free_name(tmp_path: Path) -> None:
    _create(tmp_path)
    payload = _body(_step_op(tmp_path, "my-plugin", {"op": "add", "kind": "stop"}))
    assert [n["name"] for n in payload["manifest"]["nodes"]] == ["step_1", "step_2"]


@pytest.mark.unit
def test_renaming_a_step_follows_every_arrow_that_pointed_at_it(tmp_path: Path) -> None:
    """The reason this is not in the page's JavaScript: a rename is a graph
    rewrite across successors, route ports and the entry's own start, and
    missing one of the three leaves a dangling arrow nobody drew."""
    _create(tmp_path)
    _step_op(tmp_path, "my-plugin", {"op": "add", "kind": "stop"})

    def route_to_it(manifest: dict) -> None:
        manifest["nodes"][0].update({"kind": "route", "body": "init:pick", "ports": ["step_2"]})

    _saved(tmp_path, "my-plugin", route_to_it)
    payload = _body(_step_op(tmp_path, "my-plugin", {"op": "rename", "step": "step_2", "to": "finished"}))

    written = _parse_manifest((tmp_path / "my-plugin" / "plugin.toml").read_text(encoding="utf-8"))
    assert written.nodes[0].ports == ("finished",)
    assert [n.name for n in written.nodes] == ["step_1", "finished"]
    assert payload["problems"] == []


@pytest.mark.unit
def test_renaming_the_entry_step_moves_the_entry_with_it(tmp_path: Path) -> None:
    _create(tmp_path)
    _step_op(tmp_path, "my-plugin", {"op": "rename", "step": "step_1", "to": "begin"})

    written = _parse_manifest((tmp_path / "my-plugin" / "plugin.toml").read_text(encoding="utf-8"))
    assert written.entries[0].start == "begin"


@pytest.mark.unit
def test_deleting_a_step_clears_the_arrows_that_pointed_at_it(tmp_path: Path) -> None:
    _create(tmp_path)
    _step_op(tmp_path, "my-plugin", {"op": "add", "kind": "stop"})

    def point_at_it(manifest: dict) -> None:
        manifest["nodes"][0].update({"kind": "wait", "next": "step_2"})

    _saved(tmp_path, "my-plugin", point_at_it)
    _step_op(tmp_path, "my-plugin", {"op": "delete", "step": "step_2"})

    written = _parse_manifest((tmp_path / "my-plugin" / "plugin.toml").read_text(encoding="utf-8"))
    assert [n.name for n in written.nodes] == ["step_1"]
    assert written.nodes[0].next is None


@pytest.mark.unit
def test_renaming_onto_an_existing_name_is_refused(tmp_path: Path) -> None:
    _create(tmp_path)
    _step_op(tmp_path, "my-plugin", {"op": "add", "kind": "stop"})
    response = _step_op(tmp_path, "my-plugin", {"op": "rename", "step": "step_2", "to": "step_1"})
    assert response.status == 400  # type: ignore[attr-defined]


@pytest.mark.unit
def test_an_unknown_step_operation_is_refused(tmp_path: Path) -> None:
    _create(tmp_path)
    assert _step_op(tmp_path, "my-plugin", {"op": "explode"}).status == 400  # type: ignore[attr-defined]


# ── paths that reach outside the plugin (deploy-stage cold review) ───────


@pytest.mark.unit
@pytest.mark.parametrize(
    "body",
    ["../../elsewhere/module:function", "/tmp/elsewhere/module:function", "sub/dir/module:function", "init:a.b"],
)
def test_a_body_reaching_outside_the_plugin_is_refused(tmp_path: Path, body: str) -> None:
    """The editor never executes a plugin's code — but it persists what it is
    given, and `plugin_manifest._load_body_module` later resolves a body
    against the plugin directory and calls `exec_module` on it. A cold review
    demonstrated a single save planting code for the next `discover_plugins()`
    to run from anywhere on the filesystem."""
    _create(tmp_path)

    def points_outside(manifest: dict) -> None:
        manifest["nodes"][0]["kind"] = "compute"
        manifest["nodes"][0]["body"] = body

    manifest = _body(handle("GET", "/api/plugins/my-plugin", b"", plugins_root=tmp_path))["manifest"]
    points_outside(manifest)
    response = handle("PUT", "/api/plugins/my-plugin", json.dumps(manifest).encode(), plugins_root=tmp_path)

    assert response.status == 400  # type: ignore[attr-defined]
    assert body not in (tmp_path / "my-plugin" / "plugin.toml").read_text(encoding="utf-8")


@pytest.mark.unit
@pytest.mark.parametrize("skill", ["../../elsewhere", "..", "sub/dir", "/etc"])
def test_a_skill_name_reaching_outside_the_plugin_is_refused(tmp_path: Path, skill: str) -> None:
    _create(tmp_path)

    def points_outside(manifest: dict) -> None:
        manifest["nodes"][0]["kind"] = "ask"
        manifest["nodes"][0]["skill"] = skill

    manifest = _body(handle("GET", "/api/plugins/my-plugin", b"", plugins_root=tmp_path))["manifest"]
    points_outside(manifest)
    response = handle("PUT", "/api/plugins/my-plugin", json.dumps(manifest).encode(), plugins_root=tmp_path)

    assert response.status == 400  # type: ignore[attr-defined]


@pytest.mark.unit
@pytest.mark.parametrize("parameters", ["../../../../etc/passwd", "/etc/passwd", "schema/../../../secrets.json"])
def test_a_schema_path_reaching_outside_the_plugin_is_refused(tmp_path: Path, parameters: str) -> None:
    """`plugin_manifest._check_schema` reads whatever this names, so an
    unchecked value is an arbitrary-file oracle over a socket."""
    _create(tmp_path)

    def points_outside(manifest: dict) -> None:
        manifest["entries"][0]["parameters"] = parameters

    manifest = _body(handle("GET", "/api/plugins/my-plugin", b"", plugins_root=tmp_path))["manifest"]
    points_outside(manifest)
    response = handle("PUT", "/api/plugins/my-plugin", json.dumps(manifest).encode(), plugins_root=tmp_path)

    assert response.status == 400  # type: ignore[attr-defined]


@pytest.mark.unit
def test_the_ordinary_body_skill_and_schema_shapes_still_save(tmp_path: Path) -> None:
    """The guard must not refuse what every real plugin already uses:
    `init:function`, a dashed skill name, and a schema in a subdirectory."""
    _create(tmp_path)
    (tmp_path / "my-plugin" / "init.py").write_text("def fetch(value):\n    return value\n")

    def ordinary(manifest: dict) -> None:
        manifest["entries"][0]["parameters"] = "schema/my_plugin_entry.json"
        manifest["nodes"][0].update({"kind": "compute", "body": "init:fetch", "next": "asked"})
        manifest["nodes"].append({"name": "asked", "kind": "ask", "skill": "some-skill-name", "ports": []})

    payload = _saved(tmp_path, "my-plugin", ordinary)

    assert payload["problems"] == []
    assert (
        _parse_manifest((tmp_path / "my-plugin" / "plugin.toml").read_text(encoding="utf-8")).nodes[0].body
        == "init:fetch"
    )
