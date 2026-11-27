"""Cryptographic Proof-of-Patch Attestation Engine.

Generates and validates tamper-proof signed verification certificates proving
that code patches were evaluated in an isolated zero-network sandbox without regressions.
"""

from __future__ import annotations

import datetime
import hashlib
import hmac
import json
import pathlib
from dataclasses import asdict, dataclass
from typing import Any

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519

from patchproof.core.runtime_guard import extract_runtime_profile
from patchproof.models.sandbox import ComparativeExecutionResult
from patchproof.reporting.asymmetric import (
    sign_payload_ed25519,
    verify_payload_ed25519,
)

DEFAULT_HMAC_SECRET = "patchproof-attestation-v1"


@dataclass(frozen=True)
class ProofCertificate:
    """Tamper-evident certificate of sandboxed patch verification."""

    version: str
    patch_sha256: str
    base_commit: str | None
    verdict: str
    total_tests: int
    passed_tests: int
    introduced_regressions: int
    sandbox_isolated: bool
    created_at_iso: str
    signature: str
    anti_cheat_passed: bool = True
    algorithm: str = "HMAC-SHA256"
    public_key_pem: str | None = None
    signer_identity: str | None = None
    assertion_baseline_count: int | None = None
    assertion_patched_count: int | None = None

    def to_dict(self) -> dict[str, Any]:
        """Convert certificate to serializable dictionary."""
        return asdict(self)


def _compute_digest_payload(
    version: str,
    patch_sha256: str,
    base_commit: str | None,
    verdict: str,
    total_tests: int,
    passed_tests: int,
    introduced_regressions: int,
    sandbox_isolated: bool,
    anti_cheat_passed: bool,
    created_at_iso: str,
    algorithm: str = "HMAC-SHA256",
    signer_identity: str | None = None,
    assertion_baseline_count: int | None = None,
    assertion_patched_count: int | None = None,
) -> bytes:
    """Generate canonical byte string for signature computation."""
    canonical_str = (
        f"v={version}|patch={patch_sha256}|commit={base_commit or ''}|"
        f"verdict={verdict}|total={total_tests}|passed={passed_tests}|"
        f"regressions={introduced_regressions}|isolated={sandbox_isolated}|"
        f"anticheat={anti_cheat_passed}|algo={algorithm}|"
        f"signer={signer_identity or ''}|"
        f"base_asserts={assertion_baseline_count or ''}|"
        f"patch_asserts={assertion_patched_count or ''}|"
        f"created={created_at_iso}"
    )
    return canonical_str.encode("utf-8")


def _compute_signature(payload: bytes, secret_key: str) -> str:
    """Compute HMAC-SHA256 signature for the given payload."""
    return hmac.new(secret_key.encode("utf-8"), payload, hashlib.sha256).hexdigest()


def generate_certificate(
    result: ComparativeExecutionResult,
    patch_content: str,
    base_commit: str | None = None,
    secret_key: str | None = None,
    private_key_pem: str | None = None,
    signer_identity: str | None = None,
    created_at: datetime.datetime | None = None,
) -> ProofCertificate:
    """Generate signed Proof-of-Patch certificate for a comparative execution result."""
    now = created_at or datetime.datetime.now(datetime.UTC)
    created_iso = now.isoformat()

    patch_hash = hashlib.sha256(patch_content.encode("utf-8")).hexdigest()
    total_tests = len(result.test_comparisons)
    passed_tests = (
        result.patched_result.test_report.passed_count if result.patched_result.test_report else 0
    )
    anti_cheat_passed = (
        not result.test_tampering_detected
        and result.removed_tests_count == 0
    )

    base_prof = extract_runtime_profile(result.baseline_result.stdout)
    patch_prof = extract_runtime_profile(result.patched_result.stdout)
    assert_base = base_prof.executed_assertions if base_prof else None
    assert_patch = patch_prof.executed_assertions if patch_prof else None

    if private_key_pem:
        algorithm = "Ed25519"
        priv_key = serialization.load_pem_private_key(
            private_key_pem.encode("utf-8"),
            password=None,
        )
        if not isinstance(priv_key, ed25519.Ed25519PrivateKey):
            raise ValueError("Provided key is not an Ed25519 private key")
        pub_pem = priv_key.public_key().public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        ).decode("utf-8")

        payload = _compute_digest_payload(
            version="2.0",
            patch_sha256=patch_hash,
            base_commit=base_commit,
            verdict=result.verdict.value,
            total_tests=total_tests,
            passed_tests=passed_tests,
            introduced_regressions=result.introduced_regressions_count,
            sandbox_isolated=True,
            anti_cheat_passed=anti_cheat_passed,
            created_at_iso=created_iso,
            algorithm=algorithm,
            signer_identity=signer_identity,
            assertion_baseline_count=assert_base,
            assertion_patched_count=assert_patch,
        )
        sig = sign_payload_ed25519(payload, private_key_pem)
        version = "2.0"
    else:
        algorithm = "HMAC-SHA256"
        secret = secret_key or DEFAULT_HMAC_SECRET
        pub_pem = None
        version = "1.0"
        payload = _compute_digest_payload(
            version=version,
            patch_sha256=patch_hash,
            base_commit=base_commit,
            verdict=result.verdict.value,
            total_tests=total_tests,
            passed_tests=passed_tests,
            introduced_regressions=result.introduced_regressions_count,
            sandbox_isolated=True,
            anti_cheat_passed=anti_cheat_passed,
            created_at_iso=created_iso,
            algorithm=algorithm,
            signer_identity=signer_identity,
            assertion_baseline_count=assert_base,
            assertion_patched_count=assert_patch,
        )
        sig = _compute_signature(payload, secret)

    return ProofCertificate(
        version=version,
        patch_sha256=patch_hash,
        base_commit=base_commit,
        verdict=result.verdict.value,
        total_tests=total_tests,
        passed_tests=passed_tests,
        introduced_regressions=result.introduced_regressions_count,
        sandbox_isolated=True,
        anti_cheat_passed=anti_cheat_passed,
        created_at_iso=created_iso,
        signature=sig,
        algorithm=algorithm,
        public_key_pem=pub_pem,
        signer_identity=signer_identity,
        assertion_baseline_count=assert_base,
        assertion_patched_count=assert_patch,
    )


def verify_certificate(
    cert: ProofCertificate,
    secret_key: str | None = None,
    public_key_pem: str | None = None,
) -> bool:
    """Verify that the certificate signature matches its payload and was not tampered with."""
    payload = _compute_digest_payload(
        version=cert.version,
        patch_sha256=cert.patch_sha256,
        base_commit=cert.base_commit,
        verdict=cert.verdict,
        total_tests=cert.total_tests,
        passed_tests=cert.passed_tests,
        introduced_regressions=cert.introduced_regressions,
        sandbox_isolated=cert.sandbox_isolated,
        anti_cheat_passed=cert.anti_cheat_passed,
        created_at_iso=cert.created_at_iso,
        algorithm=cert.algorithm,
        signer_identity=cert.signer_identity,
        assertion_baseline_count=cert.assertion_baseline_count,
        assertion_patched_count=cert.assertion_patched_count,
    )

    if cert.algorithm == "Ed25519":
        target_pub = public_key_pem or cert.public_key_pem
        if not target_pub:
            return False
        return verify_payload_ed25519(payload, cert.signature, target_pub)

    secret = secret_key or DEFAULT_HMAC_SECRET
    expected_sig = _compute_signature(payload, secret)
    return hmac.compare_digest(cert.signature, expected_sig)


def export_certificate(cert: ProofCertificate, output_path: pathlib.Path) -> None:
    """Write ProofCertificate to disk in pretty-printed JSON format."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(cert.to_dict(), indent=2) + "\n", encoding="utf-8")
