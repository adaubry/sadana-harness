"""`door/auth.py` — the console's ES256 token, and every refusal in order."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from sadana.door import auth, problems


def _write_jwks(path: Path, *jwks: dict) -> None:
    path.write_text(json.dumps(auth.jwks_document(*jwks)))


def _box(harness_id: str = "hrn_box", org: str | None = None) -> auth.BoxIdentity:
    return auth.BoxIdentity(harness_id=harness_id, org=org)


def _headers(token: str, hrn: str = "hrn_box") -> dict[str, str]:
    return {"Authorization": f"Bearer {token}", "X-Sadana-Harness": hrn}


@pytest.fixture
def keypair() -> tuple[bytes, dict]:
    return auth.generate_dev_keypair("k1")


@pytest.fixture
def verifier(tmp_path: Path, keypair: tuple[bytes, dict]) -> auth.Verifier:
    _, jwk = keypair
    jwks_path = tmp_path / "jwks.json"
    _write_jwks(jwks_path, jwk)
    return auth.Verifier(auth.JwksSource(path=jwks_path))


def _token(keypair: tuple[bytes, dict], **overrides) -> str:
    private_pem, jwk = keypair
    kwargs = dict(sub="u1", org=None, ws="ws1", hrn="hrn_box", scope=["harness:read"], ttl=300)
    kwargs.update(overrides)
    return auth.mint_token(private_pem, jwk["kid"], **kwargs)


@pytest.mark.unit
def test_a_valid_token_yields_a_principal(verifier: auth.Verifier, keypair) -> None:
    token = _token(keypair)
    result = verifier.verify(_headers(token), box=_box())

    assert isinstance(result, auth.Principal)
    assert result.sub == "u1"
    assert result.hrn == "hrn_box"
    assert result.scope == frozenset({"harness:read"})


@pytest.mark.unit
def test_missing_authorization_header_is_401(verifier: auth.Verifier) -> None:
    result = verifier.verify({"X-Sadana-Harness": "hrn_box"}, box=_box())
    assert isinstance(result, problems.Problem)
    assert result.code == "UNAUTHENTICATED"


@pytest.mark.unit
def test_unparseable_token_is_401(verifier: auth.Verifier) -> None:
    result = verifier.verify(_headers("not-a-jwt"), box=_box())
    assert isinstance(result, problems.Problem)
    assert result.code == "UNAUTHENTICATED"


@pytest.mark.unit
def test_expired_token_is_401(verifier: auth.Verifier, keypair) -> None:
    token = _token(keypair, ttl=10, now=1_000_000.0)  # long in the past, real clock now
    result = verifier.verify(_headers(token), box=_box())
    assert isinstance(result, problems.Problem)
    assert result.code == "UNAUTHENTICATED"


@pytest.mark.unit
def test_a_ttl_over_300_seconds_is_rejected(verifier: auth.Verifier, keypair) -> None:
    token = _token(keypair, ttl=301)
    result = verifier.verify(_headers(token), box=_box())
    assert isinstance(result, problems.Problem)
    assert result.code == "UNAUTHENTICATED"


@pytest.mark.unit
def test_hrn_mismatch_is_403(verifier: auth.Verifier, keypair) -> None:
    token = _token(keypair, hrn="hrn_other")
    result = verifier.verify(_headers(token, hrn="hrn_other"), box=_box(harness_id="hrn_box"))
    assert isinstance(result, problems.Problem)
    assert result.code == "FORBIDDEN"


@pytest.mark.unit
def test_org_mismatch_is_403(verifier: auth.Verifier, keypair) -> None:
    token = _token(keypair, org="org_a")
    result = verifier.verify(_headers(token), box=_box(org="org_b"))
    assert isinstance(result, problems.Problem)
    assert result.code == "FORBIDDEN"


@pytest.mark.unit
def test_unset_box_org_means_any(verifier: auth.Verifier, keypair) -> None:
    token = _token(keypair, org="org_a")
    result = verifier.verify(_headers(token), box=_box(org=None))
    assert isinstance(result, auth.Principal)


@pytest.mark.unit
def test_header_hrn_mismatching_the_claim_is_403(verifier: auth.Verifier, keypair) -> None:
    token = _token(keypair, hrn="hrn_box")
    result = verifier.verify(_headers(token, hrn="hrn_someone_else"), box=_box())
    assert isinstance(result, problems.Problem)
    assert result.code == "FORBIDDEN"


@pytest.mark.unit
def test_require_scope_names_the_missing_scope(verifier: auth.Verifier, keypair) -> None:
    token = _token(keypair, scope=["harness:read"])
    principal = verifier.verify(_headers(token), box=_box())
    assert isinstance(principal, auth.Principal)

    result = auth.require_scope(principal, "harness:write")
    assert isinstance(result, problems.Problem)
    assert result.code == "FORBIDDEN"
    assert "harness:write" in (result.detail or "")
    assert auth.require_scope(principal, "harness:read") is None


@pytest.mark.unit
def test_account_key_for_is_console_prefixed_sub(verifier: auth.Verifier, keypair) -> None:
    token = _token(keypair)
    principal = verifier.verify(_headers(token), box=_box())
    assert isinstance(principal, auth.Principal)
    assert auth.account_key_for(principal) == "console:u1"


@pytest.mark.unit
def test_an_unknown_kid_refreshes_at_most_once_per_minute(tmp_path: Path) -> None:
    private_a, jwk_a = auth.generate_dev_keypair("a")
    private_b, jwk_b = auth.generate_dev_keypair("b")
    jwks_path = tmp_path / "jwks.json"
    _write_jwks(jwks_path, jwk_a)

    clock = [1000.0]
    verifier = auth.Verifier(auth.JwksSource(path=jwks_path), clock=lambda: clock[0])

    # Minted against the real clock -- the fake clock below only paces the
    # verifier's own cache/throttle logic, never the token's own validity.
    token_b = auth.mint_token(private_b, "b", sub="u1", org=None, ws="ws1", hrn="hrn_box", scope=[])

    # First attempt: b is unknown, triggers a fetch, still not found.
    result = verifier.verify(_headers(token_b), box=_box())
    assert isinstance(result, problems.Problem)

    # The file now carries b too, but under 60s later -- still throttled.
    _write_jwks(jwks_path, jwk_a, jwk_b)
    clock[0] += 30
    result = verifier.verify(_headers(token_b), box=_box())
    assert isinstance(result, problems.Problem)

    # Past the throttle window: refetches, finds b.
    clock[0] += 31
    result = verifier.verify(_headers(token_b), box=_box())
    assert isinstance(result, auth.Principal)


@pytest.mark.unit
def test_a_cached_key_survives_a_refresh_that_drops_it(tmp_path: Path) -> None:
    """'Old keys kept until their tokens could have expired': removing a key
    from the JWKS must not invalidate a token already cached under it,
    within the retention window."""
    private_a, jwk_a = auth.generate_dev_keypair("a")
    private_b, jwk_b = auth.generate_dev_keypair("b")
    jwks_path = tmp_path / "jwks.json"
    _write_jwks(jwks_path, jwk_a, jwk_b)

    clock = [1000.0]
    verifier = auth.Verifier(auth.JwksSource(path=jwks_path), clock=lambda: clock[0])
    token_a = auth.mint_token(private_a, "a", sub="u1", org=None, ws="ws1", hrn="hrn_box", scope=[])

    first = verifier.verify(_headers(token_a), box=_box())
    assert isinstance(first, auth.Principal)

    # a is dropped from the source; well under the retention window.
    _write_jwks(jwks_path, jwk_b)
    clock[0] += 100
    second = verifier.verify(_headers(token_a), box=_box())
    assert isinstance(second, auth.Principal)


@pytest.mark.unit
def test_generate_dev_keypair_and_mint_token_round_trip() -> None:
    private_pem, jwk = auth.generate_dev_keypair("dev")
    assert jwk["kty"] == "EC"
    assert jwk["crv"] == "P-256"
    token = auth.mint_token(private_pem, "dev", sub="u1", org=None, ws="w1", hrn="hrn_x", scope=["a:b"])
    assert isinstance(token, str) and token.count(".") == 2
