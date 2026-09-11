"""Everything about drawing a picture that does not touch the network or disk.

`docs/tasks/IMAGE-GEN-01-the-first-plugin-that-makes-a-file/spec.md`. The
plugin's `init.py` beside it makes one HTTP call and writes one file, and
delegates every decision here — CLAUDE.md's I/O-module rule, and what lets
every branch below be tested without a socket.

The service is OpenRouter's `/chat/completions` with `modalities: ["text",
"image"]`, which answers with the picture as a base64 data URI at
`choices[0].message.images[].image_url.url` — the shape hermes's own backend
documents (`plugins/image_gen/openrouter/__init__.py:4-8, 160-178`), copied
rather than rediscovered.

Nothing here ever returns the picture's bytes to a caller that might put them
in a conversation: `decode` hands back bytes for a file, and the plugin turns
that into a path. A 1MB PNG is ~1.4MB of base64, and a body that returned it
would work perfectly while silently consuming most of the room a conversation
had left.
"""

from __future__ import annotations

import base64
import binascii
import json
import re
from typing import Any

from sadana import config

URL = "https://openrouter.ai/api/v1/chat/completions"

DEFAULT_MODEL = "google/gemini-2.5-flash-image"

# The media types this plugin will write, and the extension each gets. A
# closed map rather than splitting the media type on "/": an unknown type is a
# reply this plugin does not understand, and guessing an extension from one is
# how a file ends up named for something it is not.
_EXTENSIONS = {"image/png": "png", "image/jpeg": "jpg", "image/webp": "webp", "image/gif": "gif"}

_DATA_URI = re.compile(r"^data:([\w.+-]+/[\w.+-]+);base64,(.+)$", re.DOTALL)

# What a filename may contain. Same posture as `plugins.PLUGIN_NAME_RE`: an
# allowlist, because the alternative is enumerating what a path may not hold.
_UNSAFE_NAME = re.compile(r"[^a-z0-9]+")

MAX_NAME_CHARS = 60


def model() -> str:
    """The image model, resolved fresh per call — a behaviour, so a config key
    rather than a secret (CLAUDE.md). One id, no chain and no fallback: a
    fallback is a second backend wearing a hat (capability blueprint §6
    Rule 1)."""
    return config.env("SADANA_IMAGE_GEN_MODEL", DEFAULT_MODEL)


def timeout_s() -> int:
    """How long to wait for a picture. A behaviour, so a config key rather
    than a constant in the plugin body (CLAUDE.md). Drawing takes tens of
    seconds where every other caller of `run_http` returns in under one."""
    return config.env_int("SADANA_IMAGE_GEN_TIMEOUT_S", 180)


def build_request(prompt: str) -> tuple[str, bytes]:
    """The URL and the encoded JSON body. The key is not here — it travels as
    an `Authorization` header, so it cannot reach a log or a referrer."""
    body: dict[str, Any] = {
        "model": model(),
        "modalities": ["text", "image"],
        "messages": [{"role": "user", "content": prompt}],
    }
    return URL, json.dumps(body).encode("utf-8")


def picture(body: bytes) -> tuple[bytes, str] | str:
    """The picture's bytes and the extension its file should carry, or one
    plain sentence saying what was wrong.

    One function rather than an extract-then-decode pair: the two were never
    called apart, and splitting them made the first hand its outcome back as
    a string the caller had to sniff for a `data:` prefix — a string
    convention where its own sibling used a typed one.

    A string return is a real outcome the person reads, not an error channel.
    """
    try:
        payload = json.loads(body)
    except (ValueError, UnicodeDecodeError):
        return "The image service replied with something that is not JSON."
    if not isinstance(payload, dict):
        return "The image service replied with JSON that is not an object."
    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices:
        return "The image service's reply had no choices in it."
    message = choices[0].get("message") if isinstance(choices[0], dict) else None
    images = message.get("images") if isinstance(message, dict) else None
    if not isinstance(images, list) or not images:
        return "The image service replied without an image. It may have answered in words instead."
    first = images[0]
    image_url = first.get("image_url") if isinstance(first, dict) else None
    url = image_url.get("url") if isinstance(image_url, dict) else None
    if not isinstance(url, str) or not url.strip():
        return "The image service's reply carried an image with no data in it."
    return _decode(url.strip())


def _decode(data_uri: str) -> tuple[bytes, str] | str:
    matched = _DATA_URI.match(data_uri)
    if matched is None:
        return "The image service returned something that is not an image."
    media_type, payload = matched.group(1).lower(), matched.group(2)
    extension = _EXTENSIONS.get(media_type)
    if extension is None:
        # Sliced: `media_type` comes out of the reply, so an unbounded echo
        # of it is a channel for putting whatever they like in the transcript
        # — which is the one thing this plugin exists to prevent.
        return f"The image service returned a {media_type[:40]} image, which this plugin cannot save."
    try:
        # No `validate=True`: a provider that line-wraps its base64 is not
        # the same thing as a corrupt reply, and the message cannot tell
        # a reader which it was.
        return base64.b64decode(payload), extension
    except (binascii.Error, ValueError):
        return "The image service's reply could not be decoded."


def filename(prompt: str, extension: str) -> str:
    """One safe, readable filename derived from what was asked for.

    The prompt is model-authored, so this is an allowlist and never a strip of
    known-bad characters. It cannot produce `..`, a separator, a leading dash
    or an empty name — and `run_graph`'s own containment check catches an
    escape regardless, which makes this the readable half of a guarantee the
    runtime already enforces."""
    slug = _UNSAFE_NAME.sub("-", prompt.lower()).strip("-")[:MAX_NAME_CHARS].strip("-")
    return f"{slug or 'image'}.{extension}"
