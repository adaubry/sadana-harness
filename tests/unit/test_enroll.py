"""`enroll.py` — the box's own side of the console's enrollment handshake,
exercised entirely against a monkeypatched `urllib` and `door.auth.Verifier`.
No real network call anywhere in this file."""

from __future__ import annotations

import urllib.error
import urllib.request

import jwt
import pytest

from sadana import enroll
from sadana.door.auth import Verifier
from sadana.tether import identity


def _token(**claims: object) -> str:
    # 32+ bytes: PyJWT warns below RFC 7518 §3.2's minimum for HS256.
    return jwt.encode(dict(claims), "test-signing-secret-at-least-32-bytes-long", algorithm="HS256")


class _FakeResponse:
    def __init__(self, status: int) -> None:
        self.status = status

    def __enter__(self) -> _FakeResponse:
        return self

    def __exit__(self, *exc: object) -> bool:
        return False


@pytest.mark.unit
def test_already_enrolled_without_replace_key(monkeypatch: pytest.MonkeyPatch) -> None:
    identity.save(
        identity.Identity(
            harness_id="hrn_existing", org=None, relay_url="https://relay.example/", console_url=None, enrolled_at=1.0
        )
    )
    outcome = enroll.enroll(_token(hrn="hrn_new"), "https://relay.example/")
    assert outcome == enroll.AlreadyEnrolled(harness_id="hrn_existing")


@pytest.mark.unit
def test_unparseable_token_is_refused() -> None:
    outcome = enroll.enroll("not-a-jwt-at-all", "https://relay.example/")
    assert isinstance(outcome, enroll.TokenRefused)


@pytest.mark.unit
def test_token_with_no_hrn_claim_is_refused() -> None:
    outcome = enroll.enroll(_token(org="org_1"), "https://relay.example/")
    assert isinstance(outcome, enroll.TokenRefused)
    assert "hrn" in outcome.reason


@pytest.mark.unit
def test_token_with_no_issuer_and_no_console_is_refused() -> None:
    outcome = enroll.enroll(_token(hrn="hrn_x"), "https://relay.example/")
    assert isinstance(outcome, enroll.TokenRefused)
    assert "console" in outcome.reason


@pytest.mark.unit
def test_token_with_issuer_whose_signature_fails_to_verify_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(Verifier, "verify_signature_only", lambda self, token: None)
    outcome = enroll.enroll(_token(hrn="hrn_x", iss="https://console.example"), "https://relay.example/")
    assert isinstance(outcome, enroll.TokenRefused)
    assert "signature" in outcome.reason


@pytest.mark.unit
def test_relay_401_is_token_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    def _raise(*a: object, **k: object) -> None:
        raise urllib.error.HTTPError("https://relay.example/enroll", 401, "unauthorized", {}, None)  # type: ignore[arg-type]

    monkeypatch.setattr(urllib.request, "urlopen", _raise)
    outcome = enroll.enroll(_token(hrn="hrn_x", org="org_1"), "https://relay.example/", console="https://c.example")
    assert isinstance(outcome, enroll.TokenRefused)
    assert identity.load() is None  # nothing persisted on refusal


@pytest.mark.unit
def test_relay_other_http_error_is_network_failed(monkeypatch: pytest.MonkeyPatch) -> None:
    def _raise(*a: object, **k: object) -> None:
        raise urllib.error.HTTPError("https://relay.example/enroll", 500, "boom", {}, None)  # type: ignore[arg-type]

    monkeypatch.setattr(urllib.request, "urlopen", _raise)
    outcome = enroll.enroll(_token(hrn="hrn_x"), "https://relay.example/", console="https://c.example")
    assert isinstance(outcome, enroll.NetworkFailed)


@pytest.mark.unit
def test_unreachable_relay_is_network_failed(monkeypatch: pytest.MonkeyPatch) -> None:
    def _raise(*a: object, **k: object) -> None:
        raise urllib.error.URLError("no route to host")

    monkeypatch.setattr(urllib.request, "urlopen", _raise)
    outcome = enroll.enroll(_token(hrn="hrn_x"), "https://relay.example/", console="https://c.example")
    assert isinstance(outcome, enroll.NetworkFailed)


@pytest.mark.unit
def test_successful_enroll_with_no_issuer_is_unverified_and_persists_identity(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **k: _FakeResponse(201))
    outcome = enroll.enroll(
        _token(hrn="hrn_x", org="org_1"), "https://relay.example/", console="https://console.example"
    )
    assert outcome == enroll.Enrolled(harness_id="hrn_x", verified=False)

    saved = identity.load()
    assert saved is not None
    assert saved.harness_id == "hrn_x"
    assert saved.org == "org_1"
    assert saved.relay_url == "https://relay.example/"
    assert saved.console_url == "https://console.example"


@pytest.mark.unit
def test_successful_enroll_with_verified_issuer(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(Verifier, "verify_signature_only", lambda self, token: {"hrn": "hrn_x"})
    monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **k: _FakeResponse(200))
    outcome = enroll.enroll(_token(hrn="hrn_x", iss="https://console.example"), "https://relay.example/")
    assert outcome == enroll.Enrolled(harness_id="hrn_x", verified=True)
    assert identity.load() is not None


@pytest.mark.unit
def test_replace_key_re_enrolls_over_an_existing_identity(monkeypatch: pytest.MonkeyPatch) -> None:
    identity.save(
        identity.Identity(
            harness_id="hrn_old", org=None, relay_url="https://relay.example/", console_url=None, enrolled_at=1.0
        )
    )
    monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **k: _FakeResponse(200))
    outcome = enroll.enroll(
        _token(hrn="hrn_new"), "https://relay.example/", console="https://console.example", replace_key=True
    )
    assert outcome == enroll.Enrolled(harness_id="hrn_new", verified=False)
    saved = identity.load()
    assert saved is not None
    assert saved.harness_id == "hrn_new"
