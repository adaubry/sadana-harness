"""Tests for sadana.image_gen — the half of drawing a picture with no I/O.

The round trip and the file write are proved by `scripts/prove_image_gen.py`,
outside `make test`: testing-conventions bars the network here and this work
item does not relax it (CLAUDE.md).
"""

from __future__ import annotations

import base64
import json

import pytest

from sadana.image_gen import (
    DEFAULT_MODEL,
    MAX_NAME_CHARS,
    build_request,
    filename,
    model,
    picture,
    timeout_s,
)

_PNG = b"\x89PNG\r\n\x1a\n" + b"pretend pixels"


def _reply(url: str) -> bytes:
    """A reply shaped like the reference documents — see
    `plugins/image_gen/openrouter/__init__.py:160-178`."""
    return json.dumps({"choices": [{"message": {"images": [{"image_url": {"url": url}}]}}]}).encode()


def _data_uri(media_type: str = "image/png", payload: bytes = _PNG) -> str:
    return f"data:{media_type};base64,{base64.b64encode(payload).decode()}"


# ── build_request / model ────────────────────────────────────────────────


@pytest.mark.unit
def test_build_request_asks_for_an_image_and_not_only_text() -> None:
    _url, body = build_request("a red bicycle")
    payload = json.loads(body)
    assert payload["modalities"] == ["text", "image"]
    assert payload["messages"][0]["content"] == "a red bicycle"


@pytest.mark.unit
def test_build_request_uses_the_configured_model(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SADANA_IMAGE_GEN_MODEL", "some/other-image-model")
    _url, body = build_request("x")
    assert json.loads(body)["model"] == "some/other-image-model"


@pytest.mark.unit
def test_the_model_falls_back_to_a_default_when_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SADANA_IMAGE_GEN_MODEL", raising=False)
    assert model() == DEFAULT_MODEL


# ── picture(): reading the reply ─────────────────────────────────────────


@pytest.mark.unit
def test_picture_reads_the_shape_the_service_returns() -> None:
    """`choices[0].message.images[].image_url.url`, the path the reference
    documents (`plugins/image_gen/openrouter/__init__.py:160-178`)."""
    assert picture(_reply(_data_uri())) == (_PNG, "png")


@pytest.mark.unit
@pytest.mark.parametrize(
    "body",
    [
        b"not json",
        b"[1,2,3]",
        b'{"nothing": true}',
        b'{"choices": []}',
        b'{"choices": [{"message": {}}]}',
        b'{"choices": [{"message": {"images": []}}]}',
        b'{"choices": [{"message": {"images": [{"image_url": {"url": "  "}}]}}]}',
    ],
)
def test_picture_answers_with_a_sentence_rather_than_raising(body: bytes) -> None:
    outcome = picture(body)
    assert outcome.endswith(".")
    assert not outcome.startswith("data:")


@pytest.mark.unit
def test_a_reply_in_words_says_so_rather_than_failing_obscurely() -> None:
    """The likeliest real failure: the model answers about the picture instead
    of drawing it."""
    outcome = picture(json.dumps({"choices": [{"message": {"content": "Here is a description…"}}]}).encode())
    assert "answered in words" in outcome


# ── picture(): decoding the picture out of a reply ───────────────────────


@pytest.mark.unit
@pytest.mark.parametrize(("media_type", "extension"), [("image/png", "png"), ("image/jpeg", "jpg")])
def test_picture_returns_the_bytes_and_the_right_extension(media_type: str, extension: str) -> None:
    assert picture(_reply(_data_uri(media_type))) == (_PNG, extension)


@pytest.mark.unit
def test_picture_is_case_insensitive_about_the_media_type() -> None:
    assert picture(_reply(_data_uri("IMAGE/PNG"))) == (_PNG, "png")


@pytest.mark.unit
@pytest.mark.parametrize(
    "value",
    ["https://example.invalid/x.png", "data:text/plain;base64,AAA", "data:image/png;base64,!!!not base64!!!", ""],
)
def test_picture_answers_with_a_sentence_for_anything_it_cannot_save(value: str) -> None:
    outcome = picture(_reply(value))
    assert isinstance(outcome, str)
    assert outcome.endswith(".")


@pytest.mark.unit
def test_an_unknown_image_type_names_itself_in_the_message() -> None:
    """So a person can tell "not an image" from "an image I can't write"."""
    outcome = picture(_reply(_data_uri("image/avif")))
    assert isinstance(outcome, str)
    assert "image/avif" in outcome


# ── filename ─────────────────────────────────────────────────────────────


@pytest.mark.unit
@pytest.mark.parametrize(
    "prompt",
    ["../../etc/passwd", "a/b", "-leading", "..", "", "   ", "ünïcode ☃", "a" * 500],
)
def test_filename_is_always_one_safe_component(prompt: str) -> None:
    """The prompt is model-authored. An allowlist, never a strip of
    known-bad characters."""
    name = filename(prompt, "png")
    assert "/" not in name
    assert not name.startswith("-")
    assert not name.startswith(".")
    assert name.endswith(".png")
    assert len(name) <= MAX_NAME_CHARS + len(".png")


@pytest.mark.unit
def test_filename_keeps_the_prompt_readable() -> None:
    assert filename("a red bicycle", "png") == "a-red-bicycle.png"


@pytest.mark.unit
def test_a_prompt_with_nothing_usable_still_gets_a_name() -> None:
    assert filename("///", "jpg") == "image.jpg"


@pytest.mark.unit
def test_the_wait_is_a_config_key_not_a_constant(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SADANA_IMAGE_GEN_TIMEOUT_S", "42")
    assert timeout_s() == 42


@pytest.mark.unit
def test_the_wait_is_far_longer_than_a_fetch_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SADANA_IMAGE_GEN_TIMEOUT_S", raising=False)
    assert timeout_s() > 30


@pytest.mark.unit
def test_an_unknown_image_type_cannot_flood_the_transcript() -> None:
    """`media_type` comes out of the reply, so an unbounded echo of it is a
    channel for putting whatever they like in front of the model — the one
    thing this plugin exists to prevent."""
    outcome = picture(_reply(_data_uri("image/" + "a" * 200_000)))
    assert isinstance(outcome, str)
    assert len(outcome) < 200
