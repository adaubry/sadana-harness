"""Tests for sadana.persona — the pure half: parsing, rendering, names."""

from __future__ import annotations

import pytest

from sadana.persona import (
    NEUTRAL_NAME,
    Character,
    CharacterError,
    parse,
    render,
    valid_name,
)


def _file(description: str = "how it sounds when I'm working", **fields: str) -> str:
    lines = [f"description: {description}"] + [f"{key}: {value}" for key, value in fields.items()]
    header = "\n".join(lines)
    return f"---\n{header}\n---\nYou have read the code before answering.\n"


@pytest.mark.unit
def test_parse_reads_every_field_and_strips_the_body() -> None:
    character = parse("working", _file(tone="dry, unhurried", style="short paragraphs"))
    assert character.name == "working"
    assert character.description == "how it sounds when I'm working"
    assert character.tone == "dry, unhurried"
    assert character.style == "short paragraphs"
    assert character.body == "You have read the code before answering."


@pytest.mark.unit
def test_parse_leaves_absent_optional_fields_empty() -> None:
    character = parse("working", _file())
    assert character.tone == ""
    assert character.style == ""


@pytest.mark.unit
@pytest.mark.parametrize(
    "text",
    [
        "description: d\n---\nbody\n",  # no opening delimiter
        "---\ndescription: d\nbody with no closing delimiter\n",
        "---\ntone: dry\n---\nbody\n",  # no description
        "---\ndescription: d\n---\n\n",  # nothing to say
    ],
)
def test_parse_refuses_a_file_that_is_not_a_character(text: str) -> None:
    with pytest.raises(CharacterError):
        parse("broken", text)


@pytest.mark.unit
def test_parse_refuses_an_over_long_description() -> None:
    with pytest.raises(CharacterError):
        parse("wordy", _file(description="x" * 1025))


@pytest.mark.unit
def test_parse_accepts_a_crlf_checkout_of_the_same_file() -> None:
    """A character is meant to live in version control and move between
    machines, so CRLF is an ordinary way for one to arrive."""
    unix = parse("working", _file(tone="dry"))
    crlf = parse("working", _file(tone="dry").replace("\n", "\r\n"))
    assert crlf == unix


@pytest.mark.unit
def test_parse_accepts_a_character_that_is_only_tone_and_style() -> None:
    character = parse("terse", "---\ndescription: d\ntone: clipped\nstyle: one line\n---\n")
    assert character.body == ""
    assert render(character) == "Tone: clipped\nStyle: one line"


@pytest.mark.unit
@pytest.mark.parametrize(
    ("tone", "style", "expected"),
    [
        ("", "", "voice"),
        ("dry", "", "voice\nTone: dry"),
        ("", "terse", "voice\nStyle: terse"),
        ("dry", "terse", "voice\nTone: dry\nStyle: terse"),
    ],
)
def test_render_composes_only_the_fields_that_are_there(tone: str, style: str, expected: str) -> None:
    character = Character(name="c", description="d", tone=tone, style=style, body="voice")
    assert render(character) == expected


@pytest.mark.unit
def test_render_never_leaks_the_name_or_the_description_to_the_model() -> None:
    character = Character(name="pirate", description="talks like a buccaneer", tone="", style="", body="voice")
    rendered = render(character)
    assert character.name not in rendered
    assert character.description not in rendered


@pytest.mark.unit
@pytest.mark.parametrize("name", ["working", "a", "my-voice_2", "x" * 64])
def test_valid_name_accepts_a_plain_lowercase_name(name: str) -> None:
    assert valid_name(name)


@pytest.mark.unit
@pytest.mark.parametrize(
    "name",
    [
        "",
        "Working",
        "my voice",
        "my/voice",
        "..",
        "../escape",
        "-leading",
        "_leading",
        "x" * 65,
        "trailing-newline\n",
        NEUTRAL_NAME,
    ],
)
def test_valid_name_refuses_anything_that_could_become_a_path_or_the_reserved_name(name: str) -> None:
    assert not valid_name(name)
