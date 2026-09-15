"""`sadana enroll` — the box's own side of the console's enrollment handshake.

`docs/tasks/H30-tether-enroll-frames-lifecycle/spec.md`. I/O — network, disk,
key generation. Returns one of a small closed set of outcome dataclasses;
`subcommands/enroll.py` is the only thing that renders one to an exit code
and a message, matching this project's "outcomes are dataclasses, the
subcommand renders" convention (`plugin_install.py`'s
`FetchedTag`/`TagMismatch`/`FetchFailed`).

The enrollment token's claims are read without trusting them (`jwt.decode(
..., options={"verify_signature": False})`) purely to learn `hrn`/`org`/
`iss` before any verification happens. They are then verified against
`iss`'s JWKS via `door.auth.Verifier.verify_signature_only` — the narrower
verification enroll needs, since there is no `BoxIdentity` yet for the
door's own `verify()` to check `hrn`/`org` against (that dataclass is what
*this* function's own success produces). When the token has no `iss`
claim, `--console` is required instead, and the token's claims are trusted
without verification — said plainly, in the outcome, never silently.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass

import jwt

from sadana import __version__
from sadana.door import capabilities
from sadana.door.auth import JwksSource, Verifier, jwks_url
from sadana.tether import identity, keys


@dataclass(frozen=True)
class Enrolled:
    harness_id: str
    verified: bool  # False only when the token had no `iss` and `--console` set console_url unverified


@dataclass(frozen=True)
class TokenRefused:
    reason: str


@dataclass(frozen=True)
class AlreadyEnrolled:
    harness_id: str


@dataclass(frozen=True)
class NetworkFailed:
    detail: str


Outcome = Enrolled | TokenRefused | AlreadyEnrolled | NetworkFailed


def enroll(token: str, relay: str, *, console: str | None = None, replace_key: bool = False) -> Outcome:
    existing = identity.load()
    if existing is not None and not replace_key:
        return AlreadyEnrolled(harness_id=existing.harness_id)

    try:
        claims = jwt.decode(token, options={"verify_signature": False})
    except jwt.InvalidTokenError:
        return TokenRefused("the enrollment token could not be parsed")

    hrn = claims.get("hrn")
    org = claims.get("org")
    iss = claims.get("iss")
    if not isinstance(hrn, str) or not hrn:
        return TokenRefused("the enrollment token has no hrn claim")

    if iss:
        verified_claims = Verifier(JwksSource(url=jwks_url(console or str(iss)))).verify_signature_only(token)
        if verified_claims is None:
            return TokenRefused("the enrollment token's signature did not verify")
        console_url = console or str(iss)
        verified = True
    else:
        if not console:
            return TokenRefused("the enrollment token names no issuer; pass --console")
        console_url = console
        verified = False

    key = keys.generate()
    body = json.dumps(
        {
            "public_key": key.public_pem().decode("ascii"),
            "version": __version__,
            "capabilities": list(capabilities.declared()),
        }
    ).encode("utf-8")
    request = urllib.request.Request(
        relay.rstrip("/") + "/enroll",
        data=body,
        method="POST",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as resp:  # noqa: S310 -- fixed scheme, operator-supplied URL
            status = resp.status
    except urllib.error.HTTPError as exc:
        if exc.code == 401:
            return TokenRefused("the enrollment token was refused; mint a new one in the console")
        return NetworkFailed(f"the relay answered {exc.code}")
    except urllib.error.URLError as exc:
        return NetworkFailed(str(exc.reason))

    if not (200 <= status < 300):
        return NetworkFailed(f"the relay answered {status}")

    keys.save(key, keys.default_path(), replace=replace_key)
    identity.save(
        identity.Identity(harness_id=hrn, org=org, relay_url=relay, console_url=console_url, enrolled_at=time.time())
    )
    return Enrolled(harness_id=hrn, verified=verified)
