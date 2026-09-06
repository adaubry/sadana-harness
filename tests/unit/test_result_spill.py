"""Tests for sadana.result_spill: write_and_reference()."""

from __future__ import annotations

import pytest

from sadana import config
from sadana.result_spill import write_and_reference


@pytest.mark.unit
def test_write_and_reference_writes_the_full_content_to_disk() -> None:
    content = "x" * 5000
    write_and_reference("call_1", content)

    spill_dir = config.get_paths().state_dir / "tool_results"
    files = list(spill_dir.iterdir())
    assert len(files) == 1
    assert files[0].read_text(encoding="utf-8") == content


@pytest.mark.unit
def test_write_and_reference_note_names_path_size_and_preview() -> None:
    content = "a" * 2000 + "b" * 2000
    reference = write_and_reference("call_1", content)

    spill_dir = config.get_paths().state_dir / "tool_results"
    written = next(spill_dir.iterdir())

    assert str(written) in reference
    assert str(len(content)) in reference
    assert "no tool exists yet" in reference.lower()
    assert content[:1500] in reference
    assert content[1500:] not in reference


@pytest.mark.unit
def test_write_and_reference_sanitizes_unsafe_tool_call_id() -> None:
    reference = write_and_reference("../../etc/passwd; rm -rf", "content")

    spill_dir = config.get_paths().state_dir / "tool_results"
    files = list(spill_dir.iterdir())
    assert len(files) == 1
    assert files[0].parent == spill_dir  # never escaped the spill directory
    assert str(files[0]) in reference


@pytest.mark.unit
def test_write_and_reference_falls_back_to_content_on_write_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    def raising_write_text(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        raise OSError("disk full")

    monkeypatch.setattr("pathlib.Path.write_text", raising_write_text)

    content = "important data that must not be lost"
    assert write_and_reference("call_1", content) == content


@pytest.mark.unit
def test_write_and_reference_falls_back_to_content_when_the_directory_cant_be_made(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cold review caught that _spill_path (and its mkdir) ran outside the
    try block — an OSError from creating the directory itself (permission
    denied, a stray non-directory file already at that path) would have
    propagated uncaught, breaking this function's own 'never raises'
    contract. Fixed: the whole path computation now happens inside the
    guarded region."""

    def raising_mkdir(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        raise OSError("permission denied")

    monkeypatch.setattr("pathlib.Path.mkdir", raising_mkdir)

    content = "important data that must not be lost"
    assert write_and_reference("call_1", content) == content
