"""Asymmetric Ed25519 cryptographic signing and verification subsystem.

Provides tamper-evident digital signatures for Proof-of-Patch certificates
enabling offline verification in CI/CD without shared secrets.
"""

from __future__ import annotations

import base64
import logging

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519

logger = logging.getLogger(__name__)


def generate_ed25519_keypair() -> tuple[str, str]:
    """Generate a new Ed25519 keypair formatted as PEM strings.

    Returns:
        tuple[str, str]: (private_key_pem, public_key_pem)
    """
    private_key = ed25519.Ed25519PrivateKey.generate()
    public_key = private_key.public_key()

    priv_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode("utf-8")

    pub_pem = public_key.public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode("utf-8")

    return priv_pem, pub_pem


def sign_payload_ed25519(payload: bytes, private_key_pem: str) -> str:
    """Sign payload with Ed25519 private key and return base64-encoded signature.

    Args:
        payload: Canonical byte sequence to sign.
        private_key_pem: PEM-encoded Ed25519 private key.

    Returns:
        str: Base64-encoded signature.
    """
    priv_key = serialization.load_pem_private_key(
        private_key_pem.encode("utf-8"),
        password=None,
    )
    if not isinstance(priv_key, ed25519.Ed25519PrivateKey):
        raise ValueError("Provided key is not an Ed25519 private key")

    signature = priv_key.sign(payload)
    return base64.b64encode(signature).decode("ascii")


def verify_payload_ed25519(
    payload: bytes,
    signature_base64: str,
    public_key_pem: str,
) -> bool:
    """Verify Ed25519 signature against payload using public key.

    Args:
        payload: Canonical byte sequence.
        signature_base64: Base64-encoded signature string.
        public_key_pem: PEM-encoded Ed25519 public key.

    Returns:
        bool: True if signature is cryptographically valid, False otherwise.
    """
    try:
        pub_key = serialization.load_pem_public_key(public_key_pem.encode("utf-8"))
        if not isinstance(pub_key, ed25519.Ed25519PublicKey):
            return False

        sig_bytes = base64.b64decode(signature_base64)
        pub_key.verify(sig_bytes, payload)
        return True
    except (InvalidSignature, ValueError, TypeError) as err:
        logger.debug("Ed25519 verification failed: %s", err)
        return False
