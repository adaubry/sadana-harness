"""The editor's HTTP surface: read a plugin, draw it, save it back.

`docs/tasks/PLUGIN-EDITOR-01-draw-wire-and-save/spec.md`. I/O: reads and
writes plugin directories, and binds a socket — its own file per CLAUDE.md's
rule that a module touching real I/O is separate from a block's pure ones
(`editor_layout.py` and `plugins.manifest_to_toml` are the pure halves).

Everything interesting lives in `handle()`, which takes a method, a path and a
body and returns a `Response`. It never touches a socket, so the whole API —
routing, name checking, reading, writing, every error shape — is reachable
from `make test`. `make_server()` is the thin shell around it: read the bytes,
call `handle`, write the bytes. That is `channel_webhook.parse_webhook_request`'s
own split, taken one step further because here the part worth testing is the
whole surface rather than one parse.

This module never executes a plugin's code. Not once, on any path. That is
requirement 8 (the editor never writes the code parts, and never runs them
either) and it is also what keeps a hosted version possible later: the
`check_bodies=False` mode PLUGIN-MARKET-01 built for untrusted submissions is
the mode used here, and "does this step still need code" is answered by
reading the file with `ast`, never by importing it.
"""

from __future__ import annotations

import ast
import http.server
import json
import re
import traceback
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from sadana import editor_layout, gateway, plugin_manifest, plugins

_ASSETS = Path(__file__).parent / "editor_assets"
_ASSET_TYPES = {".html": "text/html; charset=utf-8", ".js": "text/javascript", ".css": "text/css"}

# A plugin name becomes a directory. Lowercase letters, digits and dashes
# only, and it may not lead with a dash — which leaves no way to spell `..`,
# a separator, or an absolute path (CLAUDE.md: a caller-supplied name that
# becomes a filesystem path is checked against an allowlist *and* re-checked
# after resolution).
# The length cap is not cosmetic: without it a 400-character name passes
# every check here and then raises `OSError: File name too long` from the
# first `is_file()`, which used to escape `handle()` as a 500 carrying the
# absolute path of the plugins directory.
_SAFE_NAME = re.compile(r"[a-z0-9][a-z0-9-]{0,63}")


@dataclass(frozen=True)
class Response:
    status: int
    body: bytes
    content_type: str


def _json(status: int, payload: dict) -> Response:
    return Response(status=status, body=json.dumps(payload).encode("utf-8"), content_type="application/json")


def _error(status: int, message: str) -> Response:
    return _json(status, {"error": message})


def _plugin_dir(plugins_root: Path, name: str) -> Path | None:
    """The directory `name` refers to, or `None` if the name is not one this
    server will touch. Both halves of the rule, never either: the pattern is
    the intent, the containment check after resolution is the proof."""
    if not _SAFE_NAME.fullmatch(name):
        return None
    candidate = plugins_root / name
    if candidate.resolve().parent != plugins_root.resolve():
        return None
    return candidate


def _defined_functions(source: str) -> set[str]:
    """Top-level function names in `source`, read rather than run. `ast.parse`
    executes nothing, which is the whole reason it is used here instead of the
    import `plugin_manifest._check_body` performs."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return set()
    return {node.name for node in tree.body if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)}


def _waiting(directory: Path, manifest: plugins.Manifest) -> list[dict[str, str]]:
    """Every step pointing at something that does not exist yet, and what it
    is waiting for. Never imports anything.

    Two things land here, and running the editor for the first time is what
    showed they belong together. A ``compute``/``call``/``route`` step waits
    on code. An ``ask`` step waits on a skill file — which ``intent.md`` and
    this spec's first draft both got wrong, describing ``ask`` as needing
    nothing from anyone: it needs a ``SKILL.md`` somebody writes, and the
    editor offers no way to write one. Reporting the first as a friendly
    to-do and the second as a hard validation problem told the same person
    two different stories about the same situation."""
    sources: dict[str, set[str]] = {}
    waiting: list[dict[str, str]] = []
    for node in manifest.nodes:
        if node.body is not None:
            module, _, function = node.body.partition(":")
            if module not in sources:
                path = directory / f"{module}.py"
                sources[module] = _defined_functions(path.read_text(encoding="utf-8")) if path.is_file() else set()
            if function not in sources[module]:
                waiting.append({"step": node.name, "for": "code"})
        if node.kind == "ask" and node.skill and not (directory / "skills" / node.skill / "SKILL.md").is_file():
            waiting.append({"step": node.name, "for": "a skill file"})
    return waiting


def _read_manifest(directory: Path) -> plugins.Manifest | str:
    """The parsed manifest, or a one-line description of why it would not
    parse — the same sentence `describe_manifest_outcome` gives everywhere
    else in this project."""
    path = directory / "plugin.toml"
    if not path.is_file():
        return f"no plugin.toml in {directory.name}"
    try:
        return plugins._parse_manifest(path.read_text(encoding="utf-8"))
    except Exception as exc:  # tomllib error, missing key, bad UTF-8 — all "it doesn't parse"
        return plugins.describe_manifest_outcome(plugins.ManifestParseError(detail=str(exc)))


def _assess(directory: Path) -> tuple[plugins.Manifest, list[str], list[dict[str, str]]] | str:
    """One read of a plugin: the manifest, what is wrong with it, and what it
    is waiting for — or a sentence saying it would not parse.

    One function rather than two so `plugin.toml` is read once per request
    instead of once by the parse and again by `validate()`, and so the status
    word and the full payload cannot drift into disagreeing about the same
    plugin.

    `problems` holds at most one entry because `validate()` stops at its
    first failure — reporting them one at a time is that function's own
    contract, not a shortcut taken here. `check_bodies=False` is deliberate
    twice over: it is the mode that never executes the plugin's code, and
    what a step is still waiting for is reported separately, because a step
    waiting on someone is a normal state for a plugin being drawn.

    An `UnresolvedSkill` outcome is deliberately not a problem, for the same
    reason a missing body isn't: it means an `ask` step names a skill file
    nobody has written yet, which `waiting` already says more kindly. The
    consequence to know about is that `validate()` stops there, so a
    structural mistake further down its sequence stays hidden until the skill
    exists — the same one-at-a-time reveal it already gives for everything
    else, not a new wart."""
    manifest = _read_manifest(directory)
    if isinstance(manifest, str):
        return manifest
    outcome = plugin_manifest.validate(directory, check_bodies=False)
    settled = isinstance(outcome, plugins.Valid | plugins.UnresolvedSkill)
    problems = [] if settled else [plugins.describe_manifest_outcome(outcome)]
    return manifest, problems, _waiting(directory, manifest)


def _plugin_payload(directory: Path) -> Response:
    """What the page needs to draw one plugin: the manifest, where each box
    goes, what is wrong with it, and what each step is still waiting for."""
    assessed = _assess(directory)
    if isinstance(assessed, str):
        return _error(400, assessed)
    manifest, problems, waiting = assessed
    return _json(
        200,
        {
            "name": directory.name,
            "manifest": plugins.manifest_to_dict(manifest),
            "positions": {name: list(xy) for name, xy in editor_layout.positions(manifest).items()},
            "problems": problems,
            "waiting": waiting,
        },
    )


def _status_word(directory: Path) -> str:
    assessed = _assess(directory)
    if isinstance(assessed, str):
        return "unreadable"
    _manifest, problems, waiting = assessed
    if problems:
        return "has problems"
    return "unfinished" if waiting else "ready"


def _list_plugins(plugins_root: Path) -> Response:
    """Every directory holding a `plugin.toml`, with a word about each.

    Deliberately not `plugin_manifest.discover_plugins()`, which returns only
    plugins that fully validate — that is right for deciding what an agent may
    call, and exactly wrong here, because it would hide the half-finished
    plugins this editor exists to finish."""
    if not plugins_root.is_dir():
        return _json(200, {"plugins": []})
    found = [
        {"name": child.name, "status": _status_word(child)}
        for child in sorted(plugins_root.iterdir())
        if child.is_dir() and (child / "plugin.toml").is_file()
    ]
    return _json(200, {"plugins": found})


def _kinds() -> Response:
    """The vocabulary the palette is built from, derived from the declared
    `NodeKind` rather than typed out again in JavaScript — which is what
    keeps "adding a kind is a work item, not a field" true on both sides."""
    return _json(
        200,
        {
            "kinds": [
                {
                    "kind": kind,
                    "uses": list(plugins.KIND_USES[kind]),
                    "runnable": kind not in plugins.KINDS_NOT_RUNNABLE,
                }
                for kind in plugins.NODE_KINDS
            ]
        },
    )


# A node's `body` is `module:function`, and `plugin_manifest._load_body_module`
# turns the module half into `plugin_dir / f"{module}.py"` and *executes* it.
# Both halves are therefore restricted to plain Python identifiers: no
# separators, no dots, nothing that could climb out of the plugin's directory.
_SAFE_BODY = re.compile(r"[A-Za-z_][A-Za-z0-9_]*:[A-Za-z_][A-Za-z0-9_]*")
# A skill name is one directory segment under `skills/`. No dots at all, so
# `..` cannot be spelled.
_SAFE_SKILL = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]*")


def _outside_the_plugin(directory: Path, manifest: plugins.Manifest) -> str | None:
    """Whichever of a manifest's path-forming strings would reach outside the
    plugin's own directory, or `None` if none would.

    A plugin name gets an allowlist and a containment check before it becomes
    a directory; `body`, `skill` and `parameters` are three more
    caller-supplied strings that become paths, and a cold review found them
    unchecked. The consequence was not confined to this editor: the editor
    itself never executes anything, but it *persists* what it is given, and
    `plugin_manifest._load_body_module` later resolves a node's body against
    the plugin directory and calls `exec_module` on it. So a single save with
    a body of `../../elsewhere/module:function` planted code for the next
    `discover_plugins()` to run. Checked here, at the one place every write
    passes through, rather than at each caller."""
    for entry in manifest.entries:
        candidate = directory / entry.parameters
        if Path(entry.parameters).is_absolute() or ".." in Path(entry.parameters).parts:
            return f"entry {entry.tool!r}: {entry.parameters!r} points outside the plugin"
        if not candidate.resolve().is_relative_to(directory.resolve()):
            return f"entry {entry.tool!r}: {entry.parameters!r} points outside the plugin"
    for node in manifest.nodes:
        if node.body is not None and not _SAFE_BODY.fullmatch(node.body):
            return f"step {node.name!r}: {node.body!r} must be written as module:function, both plain names"
        if node.skill is not None and not _SAFE_SKILL.fullmatch(node.skill):
            return f"step {node.name!r}: {node.skill!r} is not a skill name"
    return None


def _write_manifest(directory: Path, manifest: plugins.Manifest) -> None:
    """Emit, read the emitted text back, and only then replace the file.

    The round trip is not a belt-and-braces flourish, it is the whole
    safety property: a self-check found two strings the emitter got wrong
    (a lone surrogate, and U+007F), and in both cases the damage was not the
    bad text — it was that `write_text` had already truncated a person's
    working plugin before the failure surfaced. Checking first turns every
    such bug, including ones nobody has found yet, from "the plugin is
    gone" into "the save was refused and the file is untouched".

    Raises `ValueError`, which every caller already turns into a 400."""
    escapes = _outside_the_plugin(directory, manifest)
    if escapes is not None:
        raise ValueError(escapes)
    text = plugins.manifest_to_toml(manifest)
    if plugins._parse_manifest(text) != manifest:
        raise ValueError("this plugin could not be written back faithfully; nothing was changed")
    (directory / "plugin.toml").write_text(text, encoding="utf-8")


def _create(plugins_root: Path, body: bytes) -> Response:
    """A new plugin that is complete and valid from its first moment: one
    entry, one step that ends the run, and the schema file the entry points
    at. Starting from something that works and editing toward what you meant
    beats starting from something broken."""
    try:
        payload = json.loads(body)
    except json.JSONDecodeError as exc:
        return _error(400, f"invalid JSON: {exc}")
    if not isinstance(payload, dict) or not isinstance(payload.get("name"), str):
        return _error(400, "'name' is required and must be a string")
    name = payload["name"]
    directory = _plugin_dir(plugins_root, name)
    if directory is None:
        return _error(400, "a plugin name may hold only lowercase letters, digits and dashes")
    if directory.exists():
        return _error(409, f"{name} already exists")

    tool = f"{name.replace('-', '_')}_entry"
    manifest = plugins.Manifest(
        name=name,
        version="0.1.0",
        description="",
        entries=(plugins.Entry(tool=tool, purpose="", parameters=f"schema/{tool}.json", start="step_1"),),
        nodes=(plugins.Node(name="step_1", kind="stop"),),
    )
    (directory / "schema").mkdir(parents=True)
    (directory / "schema" / f"{tool}.json").write_text(
        json.dumps({"type": "object", "properties": {}}, indent=2) + "\n", encoding="utf-8"
    )
    try:
        _write_manifest(directory, manifest)
    except ValueError as exc:  # pragma: no cover - a scaffold this module builds itself cannot fail this
        return _error(400, str(exc))
    response = _plugin_payload(directory)
    return Response(status=201, body=response.body, content_type=response.content_type)


def _edit_step(directory: Path, body: bytes) -> Response:
    """Add, rename or delete one step, and hand back the whole plugin as it
    now stands.

    These three are graph rewrites, not field edits: renaming a step has to
    follow every arrow that pointed at it, every route port naming it, and
    every entry starting there, and deleting one has to clear the arrows that
    would otherwise dangle. That is why they live behind an endpoint calling
    pure functions in `plugins.py` rather than in the page's JavaScript, where
    nothing in this repository could test them (CLAUDE.md: a browser surface
    holds no logic that can be held in Python)."""
    assessed = _assess(directory)
    if isinstance(assessed, str):
        return _error(400, assessed)
    manifest = assessed[0]
    try:
        payload = json.loads(body)
    except json.JSONDecodeError as exc:
        return _error(400, f"invalid JSON: {exc}")
    if not isinstance(payload, dict):
        return _error(400, "the request body must be an object")

    operation = payload.get("op")
    try:
        match operation:
            case "add":
                manifest, _name = plugins.add_node(manifest, str(payload.get("kind", "")))
            case "rename":
                manifest = plugins.rename_node(manifest, str(payload.get("step", "")), str(payload.get("to", "")))
            case "delete":
                manifest = plugins.remove_node(manifest, str(payload.get("step", "")))
            case _:
                return _error(400, f"{operation!r} is not one of add, rename, delete")
    except ValueError as exc:
        return _error(400, str(exc))

    try:
        _write_manifest(directory, manifest)
    except ValueError as exc:
        return _error(400, str(exc))
    return _plugin_payload(directory)


def _save(directory: Path, body: bytes) -> Response:
    try:
        payload = json.loads(body)
    except json.JSONDecodeError as exc:
        return _error(400, f"invalid JSON: {exc}")
    try:
        manifest = plugins.manifest_from_dict(payload)
    except ValueError as exc:
        return _error(400, str(exc))
    if manifest.name != directory.name:
        # `discover_plugins` keys a plugin's identity off the manifest, not
        # the directory, so letting these diverge would let two directories
        # claim one plugin name.
        return _error(
            400, f"this plugin's folder is {directory.name!r}; its name cannot be changed to {manifest.name!r}"
        )
    try:
        _write_manifest(directory, manifest)
    except ValueError as exc:
        return _error(400, str(exc))
    return _plugin_payload(directory)


def _asset(filename: str) -> Response:
    """A file from this package's own `editor_assets/`, by exact name. The
    same two-part rule the plugin names get: an allowlist first, containment
    after resolution second."""
    if not re.fullmatch(r"[a-z0-9_-]+\.(html|js|css)", filename):
        return _error(404, "no such file")
    path = _ASSETS / filename
    if path.resolve().parent != _ASSETS.resolve() or not path.is_file():
        return _error(404, "no such file")
    return Response(status=200, body=path.read_bytes(), content_type=_ASSET_TYPES[path.suffix])


def _from_this_machine(headers: Mapping[str, str]) -> bool:
    """Whether this request came from the page this server serves, rather
    than from some other website in the same browser.

    Binding to loopback keeps other *machines* out; it is not access control
    against a *browser*, which will happily send a page from anywhere to
    127.0.0.1. A self-check demonstrated both halves of that live: an
    ordinary HTML form with `enctype="text/plain"` on any site reaches this
    server as a CORS "simple request" and created a plugin, and a request
    carrying a foreign `Host` was answered too — the DNS-rebinding shape,
    which turns a local editor into a website's read/write access to
    someone's plugins.

    So: the `Host` must be a loopback name, and an `Origin`, when the
    browser sends one, must be this same server. Both are cheap, and this is
    the function a future hosted version replaces with a real session
    check."""
    host = gateway.header_value(headers, "Host").split(":")[0].strip("[]")
    if host not in ("localhost", "127.0.0.1", "::1"):
        return False
    origin = gateway.header_value(headers, "Origin")
    if origin:
        without_scheme = origin.split("://", 1)[-1].split(":")[0].strip("[]")
        return without_scheme in ("localhost", "127.0.0.1", "::1")
    return True


def handle(
    method: str, path: str, body: bytes, *, plugins_root: Path, headers: Mapping[str, str] | None = None
) -> Response:
    """Every request this server answers. Never raises; an unexpected shape
    comes back as a status and a sentence, the same returned-outcome
    discipline `channel_webhook.py` already keeps.

    `headers` defaults to `None` for this module's own tests, which reach
    `handle` directly rather than through a browser; `make_server` always
    passes the real ones, and a request that did not come from this
    machine's own page is refused before anything is read or written."""
    if headers is not None and not _from_this_machine(headers):
        return _error(403, "this editor answers only its own page, on this machine")
    if method == "GET" and path in ("/", "/index.html"):
        return _asset("index.html")
    if method == "GET" and path.startswith("/assets/"):
        return _asset(path[len("/assets/") :])
    if method == "GET" and path == "/api/kinds":
        return _kinds()
    if method == "GET" and path == "/api/plugins":
        return _list_plugins(plugins_root)
    if method == "POST" and path == "/api/plugins":
        return _create(plugins_root, body)

    if path.startswith("/api/plugins/"):
        name, _, tail = path[len("/api/plugins/") :].partition("/")
        directory = _plugin_dir(plugins_root, name)
        if directory is None:
            return _error(400, "a plugin name may hold only lowercase letters, digits and dashes")
        if not (directory / "plugin.toml").is_file():
            return _error(404, f"no plugin named {name}")
        if method == "GET" and not tail:
            return _plugin_payload(directory)
        if method == "PUT" and not tail:
            return _save(directory, body)
        if method == "POST" and tail == "steps":
            return _edit_step(directory, body)

    return _error(404, f"no route for {method} {path}")


def make_server(host: str, port: int, *, plugins_root: Path) -> http.server.ThreadingHTTPServer:
    """`daemon_threads` left at the stdlib default (`False`), matching
    `channel_webhook.make_server`'s own reasoning: `gateway_daemon.run()`
    relies on interpreter shutdown waiting for non-daemon threads, so an
    in-flight save finishes rather than being cut off mid-write."""

    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: object) -> None:
            pass  # ponytail: quiet by default, same as channel_webhook.py

        def _run(self, method: str) -> None:
            length = int(self.headers.get("Content-Length", "0"))
            payload = self.rfile.read(length) if length else b""
            try:
                response = handle(method, self.path, payload, plugins_root=plugins_root, headers=dict(self.headers))
            except OSError as exc:  # a path the filesystem itself refused; never echo it back
                response = _error(400, f"the filesystem refused that: {exc.strerror}")
            except Exception:  # genuinely unexpected — a bug; say so without leaking the machine's paths
                response = _error(500, "the editor hit an unexpected error; see the terminal it is running in")
                traceback.print_exc()
            self.send_response(response.status)
            self.send_header("Content-Type", response.content_type)
            self.send_header("Content-Length", str(len(response.body)))
            self.end_headers()
            self.wfile.write(response.body)

        def do_GET(self) -> None:
            self._run("GET")

        def do_POST(self) -> None:
            self._run("POST")

        def do_PUT(self) -> None:
            self._run("PUT")

    return http.server.ThreadingHTTPServer((host, port), Handler)
