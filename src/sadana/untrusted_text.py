"""Making text somebody else wrote safe to put in front of a model.

Extracted from `web_search.py` when `image-gen` became the second consumer:
both put a third-party response in the model's path, and both get their
failure detail from up to 200 bytes of somebody's response body
(`execution.run_http`). A seam earns its cost once a second real member
exists (CLAUDE.md), and this is that moment.

None of this prevents prompt injection, and it must not be read as doing so.
What it does is narrower and decidable: strip the characters whose purpose is
to make what a person reads differ from what a model reads. Prose is
deliberately untouched — there is no decidable way to tell a sentence
instructing from a sentence describing one, and a filter that half-works
invites reliance on it.

The strong claim in this project is structural and lives in CLAUDE.md: text a
plugin fetched from outside reaches the model only as a tool result, never as
system-prompt or skill text.
"""

from __future__ import annotations

import html
import re
import unicodedata

_ANSI = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")

# Unicode controls and format characters — the set the standard library
# already maintains, rather than a hand-list that rots. `Cf` covers the
# U+E0000 tag block, which is how invisible instructions are smuggled.
_INVISIBLE = ("Cc", "Cf")

# Renders as nothing, but is not Cc/Cf, so the category check misses it. Named
# one by one on purpose: the obvious category to reach for is `Mn`, and taking
# all of `Mn` would eat the accents of every decomposed non-English word.
_INVISIBLE_CHARS = frozenset(
    [chr(c) for c in range(0xFE00, 0xFE10)]  # variation selectors 1-16
    + [chr(c) for c in range(0xE0100, 0xE01F0)]  # variation selectors 17-256
    + ["ㅤ", "ﾠ", "⠀", "ᅟ", "ᅠ"]  # hangul fillers, braille blank
)


def strip_invisible(text: str) -> str:
    """The character filter alone, with no HTML decoding.

    Separate from `defang` because a URL must not be HTML-unescaped: entities
    decode without needing a trailing semicolon, so an ordinary query string
    holding `&copy=`, `&not=`, `&times=` or `&reg=` comes back rewritten into
    `©=`, `¬=`, `×=`, `®=` — an address that no longer resolves to the page it
    came from. A URL is percent-encoded, never HTML-encoded.
    """
    text = _ANSI.sub("", text)
    kept = [
        ch for ch in text if (ch in "\t\n" or unicodedata.category(ch) not in _INVISIBLE) and ch not in _INVISIBLE_CHARS
    ]
    return " ".join("".join(kept).split())


def defang(text: str) -> str:
    """Decode, then strip what hides text, then normalise whitespace.

    `html.unescape` runs *before* the strip, and that order is load-bearing
    rather than incidental: `&#x202E;` is a bidi override in disguise, and
    decoding after filtering would let it through. Both live in one function
    so no caller can get the order wrong.
    """
    return strip_invisible(html.unescape(_ANSI.sub("", text)))
