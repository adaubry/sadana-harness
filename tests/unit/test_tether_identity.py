"""`tether/identity.py` — the enrolled identity file, round-tripped through
this test's own isolated state directory (see `tests/conftest.py`)."""

from __future__ import annotations

import pytest

from sadana.tether import identity


@pytest.mark.unit
def test_load_returns_none_when_never_enrolled() -> None:
    assert identity.load() is None


@pytest.mark.unit
def test_save_load_round_trips_every_field() -> None:
    original = identity.Identity(
        harness_id="hrn_abc",
        org="org_1",
        relay_url="https://relay.example/",
        console_url="https://console.example/",
        enrolled_at=1_700_000_000.5,
    )
    identity.save(original)

    loaded = identity.load()
    assert loaded == original


@pytest.mark.unit
def test_save_load_round_trips_with_optional_fields_absent() -> None:
    original = identity.Identity(
        harness_id="hrn_abc", org=None, relay_url="https://relay.example/", console_url=None, enrolled_at=1.0
    )
    identity.save(original)

    loaded = identity.load()
    assert loaded == original


@pytest.mark.unit
def test_save_escapes_quotes_and_backslashes() -> None:
    original = identity.Identity(
        harness_id='hrn_"weird"\\', org=None, relay_url="https://relay.example/", console_url=None, enrolled_at=1.0
    )
    identity.save(original)

    assert identity.load() == original


@pytest.mark.unit
def test_remove_deletes_the_file() -> None:
    identity.save(
        identity.Identity(
            harness_id="hrn_abc", org=None, relay_url="https://relay.example/", console_url=None, enrolled_at=1.0
        )
    )
    identity.remove()
    assert identity.load() is None


@pytest.mark.unit
def test_remove_is_a_no_op_when_never_enrolled() -> None:
    identity.remove()  # must not raise
    assert identity.load() is None


@pytest.mark.unit
def test_save_overwrites_a_previous_identity() -> None:
    identity.save(
        identity.Identity(
            harness_id="hrn_old", org=None, relay_url="https://relay.example/", console_url=None, enrolled_at=1.0
        )
    )
    identity.save(
        identity.Identity(
            harness_id="hrn_new", org=None, relay_url="https://relay.example/", console_url=None, enrolled_at=2.0
        )
    )
    loaded = identity.load()
    assert loaded is not None
    assert loaded.harness_id == "hrn_new"
