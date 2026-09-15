"""`tether/keys.py` — key generation, disk persistence, raw r‖s sign/verify."""

from __future__ import annotations

from pathlib import Path

import pytest

from sadana.tether import keys


@pytest.mark.unit
def test_sign_verifies_against_its_own_public_key() -> None:
    pair = keys.generate()
    signature = pair.sign(b"a-nonce")
    assert keys.verify(pair.public_pem(), b"a-nonce", signature)


@pytest.mark.unit
def test_sign_fails_to_verify_against_a_different_key_pair() -> None:
    pair = keys.generate()
    other = keys.generate()
    signature = pair.sign(b"a-nonce")
    assert not keys.verify(other.public_pem(), b"a-nonce", signature)


@pytest.mark.unit
def test_verify_fails_against_a_tampered_nonce() -> None:
    pair = keys.generate()
    signature = pair.sign(b"a-nonce")
    assert not keys.verify(pair.public_pem(), b"a-different-nonce", signature)


@pytest.mark.unit
def test_verify_never_raises_on_a_malformed_signature() -> None:
    pair = keys.generate()
    assert keys.verify(pair.public_pem(), b"a-nonce", b"too-short") is False


@pytest.mark.unit
def test_signature_is_the_raw_64_byte_r_s_form_not_der() -> None:
    pair = keys.generate()
    signature = pair.sign(b"a-nonce")
    assert len(signature) == 64


@pytest.mark.unit
def test_save_load_round_trips_through_a_tmp_file(tmp_path: Path) -> None:
    pair = keys.generate()
    path = tmp_path / "tether" / "key.pem"
    keys.save(pair, path)

    loaded = keys.load(path)
    signature = loaded.sign(b"a-nonce")
    assert keys.verify(pair.public_pem(), b"a-nonce", signature)


@pytest.mark.unit
def test_save_refuses_to_overwrite_an_existing_key_without_replace(tmp_path: Path) -> None:
    path = tmp_path / "key.pem"
    keys.save(keys.generate(), path)

    with pytest.raises(FileExistsError):
        keys.save(keys.generate(), path)


@pytest.mark.unit
def test_save_replace_true_overwrites_the_existing_key(tmp_path: Path) -> None:
    path = tmp_path / "key.pem"
    first = keys.generate()
    keys.save(first, path)

    second = keys.generate()
    keys.save(second, path, replace=True)

    loaded = keys.load(path)
    assert loaded.public_pem() == second.public_pem()
    assert loaded.public_pem() != first.public_pem()


@pytest.mark.unit
def test_save_writes_the_key_file_at_mode_0600(tmp_path: Path) -> None:
    path = tmp_path / "key.pem"
    keys.save(keys.generate(), path)
    assert (path.stat().st_mode & 0o777) == 0o600
