"""The box's own P-256 key pair — generation, disk persistence, and the
raw r‖s ES256 signature the tether's challenge/response uses.

`docs/tasks/H30-tether-enroll-frames-lifecycle/spec.md`. I/O — its own file,
per `CLAUDE.md`'s rule that a module touching real I/O (here, the
filesystem) never shares a file with a block's pure-function module.

`cryptography` is already a transitive dependency through `PyJWT[crypto]`
(`door/auth.py` already imports it for the console's own token
infrastructure) — nothing new is added here. The signature is deliberately
**raw r‖s**, not DER: `cryptography`'s own `sign()` returns DER, so this
module unpacks it with `decode_dss_signature`/`encode_dss_signature`. Raw
r‖s is the same convention JOSE's ES256 uses, so this stays consistent with
the console's own token format even though this signature is never itself
a JWT — `docs/console/wire.md` §5 fixes the wire shape.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature, encode_dss_signature

from sadana import config

_CURVE_BYTES = 32  # P-256: each of r and s is 32 bytes, big-endian.


def default_path() -> Path:
    """`state_dir/tether/key.pem` — the one place `enroll.py`, `gateway.py`
    and `door/nouns/harness.py`'s `deregister` all agree the box's own key
    lives, so none of them re-derives the path independently."""
    return config.get_paths().state_dir / "tether" / "key.pem"


@dataclass(frozen=True)
class KeyPair:
    """Wraps a real `ec.EllipticCurvePrivateKey`. The private key itself
    never has a `__repr__`/`__str__` path through this dataclass that would
    print it — only `sign()`/`private_pem()` ever touch it, and neither is
    ever passed to `logging` or an error message anywhere in this package."""

    _private_key: ec.EllipticCurvePrivateKey

    def sign(self, nonce: bytes) -> bytes:
        der_signature = self._private_key.sign(nonce, ec.ECDSA(hashes.SHA256()))
        r, s = decode_dss_signature(der_signature)
        return r.to_bytes(_CURVE_BYTES, "big") + s.to_bytes(_CURVE_BYTES, "big")

    def public_pem(self) -> bytes:
        return self._private_key.public_key().public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )

    def private_pem(self) -> bytes:
        return self._private_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )


def generate() -> KeyPair:
    return KeyPair(ec.generate_private_key(ec.SECP256R1()))


def save(key: KeyPair, path: Path, *, replace: bool = False) -> None:
    """Refuses to overwrite an existing key unless `replace=True` — the
    `O_CREAT | O_EXCL` open is what makes that atomic rather than a
    check-then-write race. Mode `0600`: this file is the box's private key
    and nothing else on the machine should be able to read it."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if replace:
        path.unlink(missing_ok=True)
    fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(key.private_pem())


def load(path: Path) -> KeyPair:
    private_key = serialization.load_pem_private_key(path.read_bytes(), password=None)
    if not isinstance(private_key, ec.EllipticCurvePrivateKey):
        raise ValueError(f"{path} does not hold an EC private key")
    return KeyPair(private_key)


def verify(public_pem: bytes, nonce: bytes, signature: bytes) -> bool:
    """The other half of `KeyPair.sign` — used by the fixture relay (and
    this module's own tests) to check a signed challenge against a stored
    public key. Never raises on a malformed signature; a wrong length or a
    signature that doesn't verify are both simply `False`."""
    if len(signature) != 2 * _CURVE_BYTES:
        return False
    r = int.from_bytes(signature[:_CURVE_BYTES], "big")
    s = int.from_bytes(signature[_CURVE_BYTES:], "big")
    public_key = serialization.load_pem_public_key(public_pem)
    if not isinstance(public_key, ec.EllipticCurvePublicKey):
        return False
    try:
        public_key.verify(encode_dss_signature(r, s), nonce, ec.ECDSA(hashes.SHA256()))
        return True
    except InvalidSignature:
        return False
