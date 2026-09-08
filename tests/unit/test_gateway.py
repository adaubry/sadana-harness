"""Tests for sadana.gateway."""

from __future__ import annotations

import pytest

from sadana.gateway import MessageEvent, session_key_for


@pytest.mark.unit
def test_session_key_for_same_chat_id_and_thread_id_matches() -> None:
    a = MessageEvent(platform="webhook", chat_id="c1", thread_id="t1", text="hi")
    b = MessageEvent(platform="webhook", chat_id="c1", thread_id="t1", text="different text")
    assert session_key_for(a) == session_key_for(b)


@pytest.mark.unit
def test_session_key_for_different_chat_id_differs() -> None:
    a = MessageEvent(platform="webhook", chat_id="c1", thread_id=None, text="hi")
    b = MessageEvent(platform="webhook", chat_id="c2", thread_id=None, text="hi")
    assert session_key_for(a) != session_key_for(b)


@pytest.mark.unit
def test_session_key_for_no_thread_id_omits_trailing_segment() -> None:
    event = MessageEvent(platform="webhook", chat_id="c1", thread_id=None, text="hi")
    assert session_key_for(event) == "webhook:c1"


@pytest.mark.unit
def test_session_key_for_with_thread_id_appends_it() -> None:
    event = MessageEvent(platform="webhook", chat_id="c1", thread_id="t1", text="hi")
    assert session_key_for(event) == "webhook:c1:t1"
