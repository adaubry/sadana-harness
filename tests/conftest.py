"""Shared fixtures.

The autouse fixture below is the one testing-conventions calls the highest-
value fixture in the suite: no test may touch the real state directory. It is
here from the first commit so it can never be retrofitted onto a suite that
has already learned to depend on the developer's machine.
"""

from __future__ import annotations

from pathlib import Path

import pytest


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
