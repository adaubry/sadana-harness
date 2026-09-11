"""What a character is, and how one becomes the voice a conversation speaks in.

`docs/tasks/PERSONA-01-characters-you-write/spec.md`. Pure: no disk, no
database, no clock. Reading a character off disk and remembering which one an
account chose both live in `persona_store.py`, per CLAUDE.md's rule that a
module touching real I/O is its own file.

A character is a document its author owns — `description`, `tone` and `style`
in `---` frontmatter, the voice itself as the body. That is the same shape a
SKILL.md has (`plugins._parse_skill_md`), on purpose: a person writing their
first character is meeting a format this project already taught them, not a
second one. The parser below is a deliberate second copy of those eight lines
rather than an import — `plugins.py` is a leaf whose only error type is
`SkillLoadError`, and PERSONA is not a reason to grow it a caller. The copy
covers the description rules too, not only the delimiter split: a description
is required and capped at the same 1024 characters a SKILL.md's is, because
the two are the same kind of thing (one line a person reads in a listing) and
a second ceiling would be a second thing to remember. A third copy of any of
it is where a shared frontmatter module earns its keep.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

#: The voice an account speaks in when it has chosen no character. A
#: constant, never a file: PERSONA-01 removed the seeded `persona.md` that
#: used to hold this text, because a default written to disk on first run is
#: indistinguishable, a year later, from something the user wrote.
NEUTRAL_VOICE = (
    "You are sadana, a plainly-spoken assistant. Answer directly, say "
    "when you're not sure, and only act on something after it has "
    "actually been agreed to.\n"
)

#: The reserved name meaning "no character". `persona use <account> none`
#: clears a selection, and no character file may claim it. One spelling, not
#: hermes's four (`hermes_cli/personality.py:37`): a set of synonyms is a set
#: of names that all have to be checked everywhere one of them is.
NEUTRAL_NAME = "none"

_NAME_RE = re.compile(r"[a-z0-9][a-z0-9_-]{0,63}")
_DESCRIPTION_MAX_CHARS = 1024


class CharacterError(Exception):
    """A character that cannot be read as one: a bad name, absent
    frontmatter, no description, or nothing to say."""


@dataclass(frozen=True)
class Character:
    """One character, as parsed. `name` is the key it is addressed by
    everywhere — what a person types, what an account's selection stores, and
    what a listing shows. `description` is for the person reading that
    listing; neither ever reaches the model (see `render`)."""

    name: str
    description: str
    tone: str
    style: str
    body: str


def valid_name(name: str) -> bool:
    """Whether `name` may become a character — and, in `persona_store`, a
    path segment. Lowercase letters, digits, `_` and `-`, starting with a
    letter or digit, at most 64 characters, and never the reserved
    `NEUTRAL_NAME`. `fullmatch`, not `match` with anchors: `$` also matches
    before a trailing newline, so `"working\n"` would pass and produce a
    character nobody can type back at the tool. The repo's other allowlists
    (`editor_server._SAFE_NAME`, `plugin_install._SHA_RE`) use `fullmatch`
    for the same reason. Checked before a name touches a path, never instead of
    the containment check that follows it (CLAUDE.md: both, never either)."""
    return name != NEUTRAL_NAME and _NAME_RE.fullmatch(name) is not None


def _split_frontmatter(text: str) -> tuple[dict[str, str], str]:
    """`---`-delimited header into flat `key: value` pairs, plus the body.

    Line endings are normalized first, which `plugins._parse_skill_md` does
    not do: a character is a document the intent expects people to keep in
    version control and copy between machines, so a CRLF checkout is an
    ordinary way for one to arrive. Without this it fails as "missing its
    frontmatter delimiter" and `resolve_voice` degrades it, silently, to the
    neutral voice."""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    if not text.startswith("---\n"):
        raise CharacterError("missing its frontmatter delimiter")
    end = text.find("\n---", 4)
    if end == -1:
        raise CharacterError("frontmatter is never closed")
    frontmatter: dict[str, str] = {}
    for line in text[4:end].splitlines():
        if not line.strip():
            continue
        key, _, value = line.partition(":")
        frontmatter[key.strip()] = value.strip()
    return frontmatter, text[end + 4 :].lstrip("\n")


def parse(name: str, text: str) -> Character:
    """Read one character file's text. Raises `CharacterError` when the
    frontmatter is missing or unclosed, the description is absent or over
    1024 characters, or the character has no body, tone and style at all —
    a character that would say nothing is a file its author got wrong, not a
    voice. `name` is carried, not validated here; `persona_store` validates
    it before the file is ever located."""
    frontmatter, body = _split_frontmatter(text)
    description = frontmatter.get("description", "")
    if not description:
        raise CharacterError("has no description")
    if len(description) > _DESCRIPTION_MAX_CHARS:
        raise CharacterError(
            f"has a {len(description)}-char description (over the {_DESCRIPTION_MAX_CHARS}-char limit)"
        )
    character = Character(
        name=name,
        description=description,
        tone=frontmatter.get("tone", ""),
        style=frontmatter.get("style", ""),
        body=body.strip(),
    )
    if not render(character):
        raise CharacterError("has nothing to say: no body, no tone, no style")
    return character


def render(character: Character) -> str:
    """The prompt text of a character: its body, then `Tone:`, then `Style:`,
    empty parts dropped. Adopted from `hermes_cli/personality.py:84-93`,
    which does exactly this and has years behind it. The name and description
    are absent by design — they are how a person finds the character, not
    part of what it says."""
    parts = (
        character.body.strip(),
        f"Tone: {character.tone}" if character.tone else "",
        f"Style: {character.style}" if character.style else "",
    )
    return "\n".join(part for part in parts if part)
