"""Tests for sadana.persona."""

from __future__ import annotations

from pathlib import Path

import pytest

from sadana.persona import load_or_seed_persona


@pytest.mark.unit
def test_load_or_seed_persona_creates_default_file(tmp_path: Path) -> None:
    path = tmp_path / "persona.md"
    text = load_or_seed_persona(path)
    assert path.read_text(encoding="utf-8") == text
    assert "sadana" in text


@pytest.mark.unit
def test_load_or_seed_persona_reads_existing_file_verbatim(tmp_path: Path) -> None:
    path = tmp_path / "persona.md"
    path.write_text("You are a pirate.\n", encoding="utf-8")
    assert load_or_seed_persona(path) == "You are a pirate.\n"
