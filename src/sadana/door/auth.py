"""The console's token: ES256 JWT, a JWKS cache, and every refusal.

`docs/tasks/H19-door-framework-token-conformance/spec.md` requirements 28-31.
I/O — its own file (a JWKS fetch is a real network call, or a local file
read for tests and the loopback listener). `algorithms=["ES256"]` is pinned
on every decode; nothing here hand-rolls a signature check —
`console_fit_plan.md` §5(c) closes that door project-wide, and
`gateway/relay/auth.py`'s own hand-rolled HMAC scheme (spec.md § Design) is
the concrete reason why.

Also carries `generate_dev_keypair`/`jwks_document`/`mint_token`: a small,
shared "build a matching keypair and sign a token with it" used both by
`sadana door token` (a later step of this same work item) and by this
module's own test suite — auth.py is already the one place that understands
this token's shape, so a second copy of "how to build one" was not started.
"""

from __future__ import annotations

import base64
import json
import threading
import time
import urllib.request
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec

from sadana import gateway, memory
from sadana.door import problems

#: A JWKS fetch is refreshed at most this often when a `kid` is unknown.
_REFRESH_THROTTLE_SECONDS = 60.0
#: `exp - iat <= 300` plus the 30s leeway: a key is never needed for
#: verification once every token it could have signed has necessarily
#: expired, so it is safe to drop from the cache after this long unused.
_KEY_MAX_AGE_SECONDS = 300.0 + 30.0


@dataclass(frozen=True)
class BoxIdentity:
    """This box's own identity, from config until H30 persists enrollment."""

    harness_id: str
    org: str | None


@dataclass(frozen=True)
class Principal:
    sub: str
    org: str | None
    ws: str
    hrn: str
    scope: frozenset[str]


@dataclass(frozen=True)
class JwksSource:
    """Exactly one of `url`/`path` is set. A URL is fetched with `urllib`
    (this repository's existing HTTP posture); a path is read from disk —
    for tests, the loopback listener's own dev JWKS, and the test relay."""

    url: str | None = None
    path: Path | None = None


class Verifier:
    """Holds one `JwksSource`'s cache. One instance lives on
    `DoorContext.verifier`, constructed once per process — never per
    request, which is what makes the refresh throttle and the key-retention
    window mean anything."""

    def __init__(self, source: JwksSource, *, clock: Any = time.time) -> None:
        self._source = source
        self._clock = clock
        self._lock = threading.Lock()
        self._keys: dict[str, tuple[jwt.PyJWK, float]] = {}
        self._last_refresh_attempt = 0.0

    def _fetch_jwks(self) -> dict[str, Any]:
        if self._source.path is not None:
            return dict(json.loads(self._source.path.read_text(encoding="utf-8")))
        assert self._source.url is not None
        with urllib.request.urlopen(self._source.url, timeout=5) as resp:  # noqa: S310 -- fixed, config-supplied URL
            return dict(json.loads(resp.read()))

    def _key_for(self, kid: str) -> jwt.PyJWK | None:
        now = self._clock()
        with self._lock:
            self._keys = {k: v for k, v in self._keys.items() if now - v[1] <= _KEY_MAX_AGE_SECONDS}
            if kid in self._keys:
                return self._keys[kid][0]
            if now - self._last_refresh_attempt < _REFRESH_THROTTLE_SECONDS:
                return None
            self._last_refresh_attempt = now
        try:
            document = self._fetch_jwks()
        except Exception:  # noqa: BLE001 -- an unreachable JWKS is "can't verify", never a crash
            return None
        with self._lock:
            for jwk_data in document.get("keys", []):
                key_id = jwk_data.get("kid")
                if not key_id:
                    continue
                try:
                    self._keys[key_id] = (jwt.PyJWK(jwk_data), now)
                except Exception:  # noqa: BLE001 -- one malformed JWKS entry must not sink every key
                    continue
            found = self._keys.get(kid)
            return found[0] if found else None

    def verify(self, headers: Mapping[str, str], *, box: BoxIdentity) -> Principal | problems.Problem:
        auth_header = gateway.header_value(headers, "Authorization")
        scheme, _, token = auth_header.partition(" ")
        if scheme.lower() != "bearer" or not token:
            return problems.make("UNAUTHENTICATED", "missing or malformed Authorization header")

        try:
            unverified = jwt.get_unverified_header(token)
        except jwt.InvalidTokenError:
            return problems.make("UNAUTHENTICATED", "token is not parseable")
        kid = unverified.get("kid")
        if not kid:
            return problems.make("UNAUTHENTICATED", "token has no kid")

        key = self._key_for(kid)
        if key is None:
            return problems.make("UNAUTHENTICATED", "no known signing key for this token")

        try:
            claims = jwt.decode(token, key=key, algorithms=["ES256"], leeway=30)
        except jwt.ExpiredSignatureError:
            return problems.make("UNAUTHENTICATED", "token is expired")
        except jwt.InvalidTokenError:
            return problems.make("UNAUTHENTICATED", "token is not parseable")

        sub, ws, hrn = claims.get("sub"), claims.get("ws"), claims.get("hrn")
        org, scope, iat, exp = claims.get("org"), claims.get("scope"), claims.get("iat"), claims.get("exp")
        if not sub or not ws or not hrn or iat is None or exp is None or not isinstance(scope, list):
            return problems.make("UNAUTHENTICATED", "token is missing a required claim")
        if exp - iat > 300:
            return problems.make("UNAUTHENTICATED", "token ttl exceeds 300 seconds")

        if hrn != box.harness_id:
            return problems.make("FORBIDDEN", "token was minted for a different harness")
        if box.org is not None and org != box.org:
            return problems.make("FORBIDDEN", "token organisation does not match this harness's enrollment")
        if gateway.header_value(headers, "X-Sadana-Harness") != hrn:
            return problems.make("FORBIDDEN", "X-Sadana-Harness header does not match the token's hrn claim")

        return Principal(sub=sub, org=org, ws=ws, hrn=hrn, scope=frozenset(scope))


def require_scope(principal: Principal, scope: str) -> problems.Problem | None:
    if scope not in principal.scope:
        return problems.make("FORBIDDEN", f"requires scope {scope}")
    return None


def account_key_for(principal: Principal) -> memory.AccountKey:
    return f"console:{principal.sub}"


# ── dev keypair / token minting — shared by `sadana door token` and tests ──
def generate_dev_keypair(kid: str) -> tuple[bytes, dict[str, str]]:
    """A fresh EC P-256 keypair: the PEM-encoded private key (for signing)
    and the matching public JWK (for a JWKS document)."""
    private_key = ec.generate_private_key(ec.SECP256R1())
    private_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    numbers = private_key.public_key().public_numbers()
    x = base64.urlsafe_b64encode(numbers.x.to_bytes(32, "big")).decode("ascii").rstrip("=")
    y = base64.urlsafe_b64encode(numbers.y.to_bytes(32, "big")).decode("ascii").rstrip("=")
    jwk_data = {"kty": "EC", "crv": "P-256", "alg": "ES256", "use": "sig", "kid": kid, "x": x, "y": y}
    return private_pem, jwk_data


def jwks_document(*jwks: dict[str, str]) -> dict[str, list[dict[str, str]]]:
    return {"keys": list(jwks)}


def mint_token(
    private_pem: bytes,
    kid: str,
    *,
    sub: str,
    org: str | None,
    ws: str,
    hrn: str,
    scope: Sequence[str],
    ttl: int = 300,
    now: float | None = None,
) -> str:
    now = time.time() if now is None else now
    claims: dict[str, Any] = {
        "sub": sub,
        "ws": ws,
        "hrn": hrn,
        "scope": list(scope),
        "iat": int(now),
        "exp": int(now) + ttl,
    }
    if org is not None:
        claims["org"] = org
    return jwt.encode(claims, private_pem, algorithm="ES256", headers={"kid": kid})
