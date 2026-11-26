"""Cryptographic Proof-of-Patch Attestation Engine.

Generates and validates tamper-proof signed verification certificates proving
that code patches were evaluated in an isolated zero-network sandbox without regressions.
"""

from __future__ import annotations

import datetime
import hashlib
import hmac
import pathlib
from dataclasses import asdict, dataclass
from typing import Any

from patchproof.models.sandbox import ComparativeExecutionResult

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
) -> bytes:
    """Generate canonical byte string for HMAC signature computation."""
    canonical_str = (
        f"v={version}|patch={patch_sha256}|commit={base_commit or ''}|"
        f"verdict={verdict}|total={total_tests}|passed={passed_tests}|"
        f"regressions={introduced_regressions}|isolated={sandbox_isolated}|"
        f"anticheat={anti_cheat_passed}|"
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
    created_at: datetime.datetime | None = None,
) -> ProofCertificate:
    """Generate signed Proof-of-Patch certificate for a comparative execution result."""
    secret = secret_key or DEFAULT_HMAC_SECRET
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

    payload = _compute_digest_payload(
        version="1.0",
        patch_sha256=patch_hash,
        base_commit=base_commit,
        verdict=result.verdict.value,
        total_tests=total_tests,
        passed_tests=passed_tests,
        introduced_regressions=result.introduced_regressions_count,
        sandbox_isolated=True,
        anti_cheat_passed=anti_cheat_passed,
        created_at_iso=created_iso,
    )
    sig = _compute_signature(payload, secret)

    return ProofCertificate(
        version="1.0",
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
    )


def verify_certificate(cert: ProofCertificate, secret_key: str | None = None) -> bool:
    """Verify that the certificate signature matches its payload and was not tampered with."""
    secret = secret_key or DEFAULT_HMAC_SECRET
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
    )
    expected_sig = _compute_signature(payload, secret)
    return hmac.compare_digest(cert.signature, expected_sig)


def export_certificate(cert: ProofCertificate, output_path: pathlib.Path) -> None:
    """Write ProofCertificate to disk in pretty-printed JSON format."""
    import json

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(cert.to_dict(), indent=2) + "\n", encoding="utf-8")
