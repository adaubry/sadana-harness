"""Tests for sadana.memory — the pure half of MEMORY-01."""

from __future__ import annotations

import pytest

from sadana.memory import (
    MemoryEntry,
    account_key_for,
    default_rubric,
    render_capture_guidance,
    render_recall,
    system_message_for,
)


@pytest.mark.unit
def test_render_recall_empty_is_empty_string() -> None:
    assert render_recall(()) == ""


@pytest.mark.unit
def test_render_recall_renders_one_line_per_entry() -> None:
    entries = (
        MemoryEntry(
            account_key="a1",  # pragma: allowlist secret
            entry_key="dog_name",
            content="Their dog is named Buddy.",
            updated_at=1.0,
        ),
        MemoryEntry(
            account_key="a1",  # pragma: allowlist secret
            entry_key="tz",
            content="They're in UTC+2.",
            updated_at=2.0,
        ),
    )
    text = render_recall(entries)
    assert "- Their dog is named Buddy." in text
    assert "- They're in UTC+2." in text
    assert text.count("\n") == 2  # header line + one newline per entry after it


@pytest.mark.unit
def test_render_capture_guidance_includes_default_always() -> None:
    guidance = render_capture_guidance("the default rubric", "")
    assert "the default rubric" in guidance
    assert "memory.remember" in guidance


@pytest.mark.unit
def test_render_capture_guidance_adds_override_on_top_of_default() -> None:
    guidance = render_capture_guidance("the default rubric", "also remember their timezone")
    assert "the default rubric" in guidance
    assert "also remember their timezone" in guidance


@pytest.mark.unit
def test_account_key_for_combines_platform_and_chat_id() -> None:
    assert account_key_for("webhook", "u123") == "webhook:u123"


@pytest.mark.unit
def test_account_key_for_ignores_thread_component() -> None:
    # Two different threads for the same sender must produce the same
    # account key — that's the entire point of this function existing
    # separately from gateway.session_key_for, which does include the
    # thread id.
    assert account_key_for("webhook", "u123") == account_key_for("webhook", "u123")


@pytest.mark.unit
def test_default_rubric_falls_back_when_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SADANA_MEMORY_DEFAULT_RUBRIC", raising=False)
    assert default_rubric() == "whatever helps you know this person better"


@pytest.mark.unit
def test_default_rubric_honors_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SADANA_MEMORY_DEFAULT_RUBRIC", "remember only work-related facts")
    assert default_rubric() == "remember only work-related facts"


@pytest.mark.unit
def test_system_message_for_combines_recall_and_guidance() -> None:
    entries = (
        MemoryEntry(
            account_key="a1",  # pragma: allowlist secret
            entry_key="dog_name",
            content="Their dog is named Buddy.",
            updated_at=1.0,
        ),
    )
    message = system_message_for(entries, "the default rubric", "")
    assert "Their dog is named Buddy." in message
    assert "the default rubric" in message


@pytest.mark.unit
def test_system_message_for_with_no_entries_still_carries_guidance() -> None:
    message = system_message_for((), "the default rubric", "")
    assert "Their dog" not in message
    assert "the default rubric" in message
