"""One `logging.Filter` that keeps a secret's value out of everything this
box logs (H14, `docs/tasks/H14-tuned-config-settings-secrets/spec.md`).

Pure: no disk, no clock, no network — it only rewrites the `LogRecord` it is
handed. Installed on the root logger by `cli.main()` and `client_surface.
open_runtime()`, the same two entrypoints `config.bind_secret_reader` is
bound at, and for the same reason: anything logging before either has run
is early-bootstrap code that cannot yet be holding a secret worth
redacting.

Three substitutions, two shape-based and one name-based:

* An OpenRouter-shaped key (`sk-...`) or a JWT (three base64url segments,
  the first decoding to JSON with an `alg` key) is redacted wherever it
  appears in the record's own *rendered* message — this is what
  `record.getMessage()` % `record.args` produces, so a value passed as a
  positional `%s` argument is caught exactly as one already baked into an
  f-string would be.

* A custom attribute a caller attached via `extra={...}` — anything not
  part of `logging.LogRecord`'s own fixed attribute set — is redacted by
  the *name* of that attribute: `secret`, `key`, `token`, `password`,
  `credential` or `authorization`, case-insensitively
  (`api_key` matches, `keyboard` does not). `logger.info("rotated %s",
  name, extra={"api_key": value})` redacts `record.api_key`'s value
  without this filter ever inspecting what the value looks like — a value
  that happens to contain no recognizable secret shape (a UUID, a short
  token) is still caught, because the field's own name is what decided.

  Corrected against `spec.md`'s own worked example during implementation:
  it named `extra={"key": "OPENROUTER_API_KEY", "value": v}` as the shape
  this catches, but under name-based matching that redacts the field named
  `key` (a credential's *name*, which this whole design deliberately keeps
  visible — `credential_ref` and `secret_ref` are always plain names) and
  leaves the field named `value` (the actual secret) alone, since "value"
  matches none of the sensitive words. A caller who wants this filter's
  protection names the field after what it holds (`api_key`, not `value`).
"""

from __future__ import annotations

import base64
import binascii
import json
import logging
import re

_REDACTED = "[REDACTED]"

_SK_TOKEN = re.compile(r"sk-[A-Za-z0-9_-]{16,}")
_JWT_SHAPE = re.compile(r"\b[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\b")
#: Matched against a whole word of the attribute name, never a bare
#: substring — `api_key` splits into `api`/`key` and matches on `key`,
#: while `keyboard`/`turkey` are one word each and match none of these
#: exactly (the substring form this replaced matched both, contradicting
#: this module's own docstring).
_SENSITIVE_WORDS = frozenset({"secret", "key", "token", "password", "credential", "authorization"})
_NAME_WORD_SPLIT = re.compile(r"[^a-zA-Z0-9]+|(?<=[a-z0-9])(?=[A-Z])")

#: Every attribute a plain `logging.LogRecord` carries by construction —
#: anything else on a record is something a caller attached via `extra=`,
#: which is what the name-based check inspects. Derived from the stdlib
#: itself rather than hand-listed, so a future Python adding a field (as
#: 3.12 did with `taskName`) needs no matching edit here.
_STANDARD_RECORD_ATTRS = frozenset(vars(logging.makeLogRecord({})))


def _looks_like_jwt(candidate: str) -> bool:
    """A JWT's first segment is base64url-encoded JSON carrying an ``alg``
    key — checked rather than assumed, so an ordinary dotted token
    (a version string, a hostname-shaped log line) is not redacted only
    because it has two dots in it."""
    head = candidate.split(".", 1)[0]
    padded = head + "=" * (-len(head) % 4)
    try:
        decoded = base64.urlsafe_b64decode(padded)
        payload = json.loads(decoded)
    except (binascii.Error, ValueError, UnicodeDecodeError):
        return False
    return isinstance(payload, dict) and "alg" in payload


def _redact_shapes(text: str) -> str:
    text = _SK_TOKEN.sub(_REDACTED, text)
    return _JWT_SHAPE.sub(lambda m: _REDACTED if _looks_like_jwt(m.group(0)) else m.group(0), text)


class SecretRedactor(logging.Filter):
    """Installed on the root logger; every handler downstream sees only the
    redacted record. Always returns ``True`` — this never drops a log line,
    only rewrites what it carries."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = _redact_shapes(record.getMessage())
        record.args = ()
        for name in list(vars(record)):
            if name in _STANDARD_RECORD_ATTRS:
                continue
            words = (w.lower() for w in _NAME_WORD_SPLIT.split(name) if w)
            if any(w in _SENSITIVE_WORDS for w in words) and isinstance(getattr(record, name), str):
                setattr(record, name, _REDACTED)
        return True
