"""Shared fixtures.

The autouse fixture below is the one testing-conventions calls the highest-
value fixture in the suite: no test may touch the real state directory. It is
here from the first commit so it can never be retrofitted onto a suite that
has already learned to depend on the developer's machine.
"""

from __future__ import annotations

import sqlite3
import subprocess
from collections.abc import Callable
from pathlib import Path

import pytest

from sadana import client_surface, model_access, observability, persona_store, plugin_dispatch, plugins, stores
from sadana.context import ContextState
from sadana.conversation import (
    Conversation,
    ExitReason,
    IterationBudget,
    Message,
    ToolSpec,
    TurnKey,
    TurnResult,
    WallClockBudget,
    build_surface,
    turn_prompt_hash,
)
from sadana.conversation_store import open_store, store_path_from_config


@pytest.fixture(autouse=True)
def _isolated_state(tmp_path, monkeypatch):  # type: ignore[no-untyped-def]
    """Every test runs against a temporary home. No exceptions."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("XDG_STATE_HOME", str(home / ".local" / "state"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(home / ".config"))
    monkeypatch.setenv("SADANA_STATE_DIR", str(home / ".sadana"))
    return home


SYSTEM_PROMPT = "You are a helpful assistant."


def tool_spec(key: str = "noop", name: str = "noop") -> ToolSpec:
    """A minimal ``ToolSpec`` for building a ``Conversation`` fixture —
    shared by ``test_conversation_store.py`` and
    ``test_subcommands_conversations.py``."""
    return ToolSpec(
        key=key,
        name=name,
        parameters={"type": "object", "properties": {}},
        describe=lambda _resolved: f"{name} does things.",
    )


def conversation(
    *,
    key: str = "k1",
    template_name: str = "t1",
    messages: tuple[Message, ...] = (),
    wall_clock_budget: WallClockBudget | None = None,
    next_turn_seq: int = 0,
    iteration_budget: IterationBudget | None = None,
    next_child_seq: int = 0,
) -> Conversation:
    """A minimal, fully-defaulted ``Conversation`` fixture — shared by
    ``test_conversation_store.py`` and ``test_subcommands_conversations.py``."""
    surface = build_surface([tool_spec()])
    return Conversation(
        key=key,
        template_name=template_name,
        system_prompt=SYSTEM_PROMPT,
        prompt_sha256=turn_prompt_hash(SYSTEM_PROMPT, surface),
        prompt_epoch=0,
        tool_surface=surface,
        messages=messages,
        next_turn_seq=next_turn_seq,
        iteration_budget=iteration_budget or IterationBudget(max_total=10, used=3),
        wall_clock_budget=wall_clock_budget,
        stable_prompt_len=len(SYSTEM_PROMPT),
        context_state=ContextState(),
        next_child_seq=next_child_seq,
    )


def tool_call_response(*names: str) -> model_access.Response:
    """A model response calling each of ``names`` as a tool, no arguments
    — shared by ``test_conversation_store.py`` and
    ``test_subcommands_chat.py``."""
    return model_access.Response(
        content=None,
        tool_calls=tuple({"function": {"name": n, "arguments": "{}"}} for n in names),
        finish_reason="tool_calls",
        usage=model_access.Usage(),
    )


def plain_response(content: str) -> model_access.Response:
    """A model response with no tool calls — the turn-ending sibling of
    ``tool_call_response``."""
    return model_access.Response(content=content, tool_calls=(), finish_reason="stop", usage=model_access.Usage())


def turn_result(
    *, conversation: str = "c1", turn_seq: int = 0, prompt_tokens: int = 10, completion_tokens: int = 5
) -> TurnResult:
    """A minimal, fully-defaulted ``TurnResult`` — shared by
    ``test_observability.py`` and ``test_subcommands_runs.py``."""
    return TurnResult(
        turn_key=TurnKey(conversation=conversation, turn_seq=turn_seq),
        final_text="hi",
        exit_reason=ExitReason.COMPLETED,
        detail=None,
        model_calls=2,
        usage=model_access.Usage(prompt_tokens=prompt_tokens, completion_tokens=completion_tokens),
        appended=range(0),
        context_state=ContextState(),
    )


def dag_result(*, failed_node: str | None = None) -> plugins.DagResult:
    """A minimal, fully-defaulted ``DagResult`` — shared by
    ``test_observability.py`` and ``test_subcommands_runs.py``."""
    return plugins.DagResult(
        plugin="p1",
        entry="do_it",
        text="ok",
        trace=(
            plugins.NodeTrace(node="a", kind="compute", visit=0, ok=True, port=None, detail=None),
            plugins.NodeTrace(node="b", kind="stop", visit=0, ok=True, port=None, detail=None),
        ),
        failed_node=failed_node,
    )


def wait_then_summarize_installed(tmp_path: Path) -> plugins.InstalledPlugin:
    """A minimal `call` -> `wait` -> `compute` plugin, on a real `tmp_path`
    directory — including a real ``plugin.toml`` (not just an in-memory
    ``Manifest``), since ``plugin_manifest.validate()`` parses one fresh
    from disk and ``resume_paused_run()`` calls it directly rather than
    `discover_plugins()` (GATEWAY-DAEMON-02: re-resolving one named plugin
    should not have to scan and validate every other installed one).
    Shared by ``test_gateway_dispatch.py`` and ``test_plugin_dispatch.py``,
    both of which need the identical fixture for GATEWAY-DAEMON-02's own
    pause/resume tests."""
    plugin_dir = tmp_path / "p"
    plugin_dir.mkdir()
    plugin_dir.joinpath("init.py").write_text("def summarize(value):\n    return f'answered: {value}'\n")
    plugin_dir.joinpath("s.json").write_text('{"type": "object"}')
    plugin_dir.joinpath("plugin.toml").write_text(
        '[plugin]\nname = "p"\nversion = "0.1.0"\ndescription = "d"\n\n'
        '[[entry]]\ntool = "do_it"\npurpose = "p"\nparameters = "s.json"\nstart = "future"\n\n'
        '[[node]]\nname = "future"\nkind = "wait"\nnext = "summarize"\n\n'
        '[[node]]\nname = "summarize"\nkind = "compute"\nbody = "init:summarize"\n'
    )
    manifest = plugins.Manifest(
        name="p",
        version="0.1.0",
        description="d",
        entries=(plugins.Entry(tool="do_it", purpose="p", parameters="s.json", start="future"),),
        nodes=(
            plugins.Node(name="future", kind="wait", next="summarize"),
            plugins.Node(name="summarize", kind="compute", body="init:summarize"),
        ),
    )
    return plugins.InstalledPlugin(name="p", directory=plugin_dir, manifest=manifest)


def write_skill(
    tmp_path: Path,
    *,
    plugin: str = "p1",
    skill: str = "s1",
    name: str | None = None,
    description: str | None = "Does one focused thing.",
    body: str = "Do the thing, then stop.",
) -> Path:
    """A ``tmp_path``-rooted ``<plugins_root>/<plugin>/skills/<skill>/SKILL.md``,
    shared by ``test_conversation.py``'s ``run_child`` tests and
    ``test_plugin_manifest.py``'s ``load_skill`` tests — both write the same
    on-disk layout `plugin_blueprint.md §5.1` fixes. Returns the plugins
    root, not the skill directory."""
    skill_dir = tmp_path / "plugins" / plugin / "skills" / skill
    skill_dir.mkdir(parents=True)
    lines = [f"name: {skill if name is None else name}"]
    if description is not None:
        lines.append(f"description: {description}")
    (skill_dir / "SKILL.md").write_text("---\n" + "\n".join(lines) + f"\n---\n{body}")
    return tmp_path / "plugins"


def write_character(
    name: str,
    *,
    body: str = "You read the code first.",
    description: str = "a voice",
    tone: str = "",
    characters_dir: Path | None = None,
) -> Path:
    """One character file, written the way a person writes one — shared by
    ``test_persona_store.py``, ``test_subcommands_persona.py``,
    ``test_client_surface.py`` and ``test_subcommands_chat.py``, which each
    need the same on-disk shape ``persona_store.load_character`` reads.
    Defaults to the configured characters directory, which the isolated-state
    fixture already points at this test's own tmp home. A test that wants a
    *malformed* character writes over the returned path itself; this helper
    only produces valid ones."""
    root = persona_store.characters_dir_from_config() if characters_dir is None else characters_dir
    root.mkdir(parents=True, exist_ok=True)
    header = f"description: {description}" + (f"\ntone: {tone}" if tone else "")
    path = root / f"{name}.md"
    path.write_text(f"---\n{header}\n---\n{body}\n", encoding="utf-8")
    return path


def run_git(args: list[str], *, cwd: Path) -> None:
    """A real ``git`` subprocess call — shared by ``test_plugin_install.py``
    and ``test_subcommands_plugin.py``, both of which exercise
    ``plugin_install.py`` against a real, local repository rather than a
    mocked one (`testing-conventions`: no network, real behavior)."""
    subprocess.run(["git", *args], cwd=str(cwd), check=True, capture_output=True, text=True)


def make_upstream_repo(tmp_path: Path, *, plugin_name: str = "greeter", tag: str = "v1.0.0") -> Path:
    """A real, local git repository — one commit tagged ``tag``, whose
    ``plugin.toml`` declares ``plugin_name`` — cloned in tests via its
    plain filesystem path. Shared by ``test_plugin_install.py`` and
    ``test_subcommands_plugin.py``."""
    repo = tmp_path / "upstream"
    repo.mkdir()
    run_git(["init", "-q", "-b", "main"], cwd=repo)
    run_git(["config", "user.email", "test@example.com"], cwd=repo)
    run_git(["config", "user.name", "Test"], cwd=repo)
    (repo / "plugin.toml").write_text(
        f'[plugin]\nname = "{plugin_name}"\nversion = "{tag}"\ndescription = "a test plugin"\n'
    )
    run_git(["add", "."], cwd=repo)
    run_git(["commit", "-q", "-m", "initial"], cwd=repo)
    run_git(["tag", tag], cwd=repo)
    return repo


EMPTY_PLUGIN_SET = plugin_dispatch.PluginSet(catalog=(), tool_specs=(), by_tool={})


def make_runtime(
    conn: sqlite3.Connection, *, plugin_set: plugin_dispatch.PluginSet = EMPTY_PLUGIN_SET
) -> client_surface.Runtime:
    """A ``client_surface.Runtime`` over a caller-owned connection — shared by
    ``test_client_surface.py``, ``test_gateway_dispatch.py`` and
    ``test_scheduling.py``, all of which drive a turn without going through
    ``open_runtime()``'s own disk assembly.

    No ``persona`` parameter, and no ``Runtime.persona`` to pass one to:
    PERSONA-01 moved the voice onto the account, resolved inside
    ``_create()``. A test that wants a conversation created under a specific
    character writes that character's file and selects it for the account,
    the same way a person does.

    Ensures the ``memory_store`` and ``persona_store`` schemas itself: ``take_turn``'s docstring names
    that as the obligation of whoever builds a ``Runtime`` by hand, the same
    way ``test_gateway_dispatch.py`` already called ``ensure_schema`` for a
    direct bridge call."""
    stores.ensure_schemas(conn)
    return client_surface.Runtime(
        conn=conn,
        plugin_set=plugin_set,
        provider="p",
        model="m",
        recorder=observability.make_recorder(conn),
    )


def tool_then_text(
    tool: str = "do_it", text: str = "okay, I'll wait for it."
) -> Callable[[object], model_access.Response]:
    """A ``model_access.send`` stub for the two-step shape every plugin turn
    has: call ``tool`` first, then answer with ``text`` once a tool result is
    in history. Shared by ``test_client_surface.py`` and
    ``test_subcommands_chat.py``; ``test_gateway_dispatch.py`` still hand-rolls
    its own copy, which is a tidy-up for whoever next edits that file."""

    def _send(request: object) -> model_access.Response:
        messages = request.messages  # type: ignore[attr-defined]
        if any(m.get("role") == "tool" for m in messages):
            return plain_response(text)
        return tool_call_response(tool)

    return _send


def never_send(_request: object) -> model_access.Response:
    """A ``model_access.send`` stub that fails the test if it is ever called —
    for a path whose whole point is that no model call happens (a resume, an
    unknown conversation)."""
    raise AssertionError("the model must not be called on this path")


def open_conn() -> sqlite3.Connection:
    """A fresh connection to this test's own isolated store — shared by
    ``test_plugin_install.py`` and ``test_marketplace.py``, both of which
    open and close a connection per test rather than across a whole
    module."""
    return open_store(store_path_from_config())
