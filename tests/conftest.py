"""Shared fixtures.

The autouse fixture below is the one testing-conventions calls the highest-
value fixture in the suite: no test may touch the real state directory. It is
here from the first commit so it can never be retrofitted onto a suite that
has already learned to depend on the developer's machine.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from sadana import model_access
from sadana.context import ContextState
from sadana.conversation import (
    Conversation,
    IterationBudget,
    Message,
    ToolSpec,
    WallClockBudget,
    build_surface,
    turn_prompt_hash,
)


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
