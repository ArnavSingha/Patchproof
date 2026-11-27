"""Unit tests for Ed25519 asymmetric cryptographic attestation and certificates."""

from __future__ import annotations

import base64

from patchproof.models.sandbox import (
    ComparativeExecutionResult,
    ComparativeTestCase,
    ExecutionResult,
    PatchVerdict,
    RegressionClassification,
    TestOutcome,
    TestRunReport,
)
from patchproof.reporting.asymmetric import (
    generate_ed25519_keypair,
    sign_payload_ed25519,
    verify_payload_ed25519,
)
from patchproof.reporting.certificate import (
    ProofCertificate,
    generate_certificate,
    verify_certificate,
)


def make_dummy_comparative_result(
    verdict: PatchVerdict = PatchVerdict.ACCEPTED,
    tampered: bool = False,
) -> ComparativeExecutionResult:
    """Helper to create dummy ComparativeExecutionResult."""
    profile_stdout = (
        '__PATCHPROOF_RUNTIME_PROFILE__={"collected_count": 2, "executed_count": 2, '
        '"executed_assertions": 5, "assertions_by_test": {}}'
    )
    base_res = ExecutionResult(
        exit_code=0,
        stdout=profile_stdout,
        duration_seconds=0.1,
        test_report=TestRunReport(
            test_cases=(), exit_code=0, duration_seconds=0.1, total_collected=2
        ),
    )
    patched_res = ExecutionResult(
        exit_code=0,
        stdout=profile_stdout,
        duration_seconds=0.1,
        test_report=TestRunReport(
            test_cases=(), exit_code=0, duration_seconds=0.1, total_collected=2
        ),
    )
    comp_case = ComparativeTestCase(
        test_name="test_1",
        baseline_outcome=TestOutcome.PASS,
        patched_outcome=TestOutcome.PASS,
        classification=RegressionClassification.CLEAN_PRESERVATION,
    )
    return ComparativeExecutionResult(
        baseline_result=base_res,
        patched_result=patched_res,
        verdict=verdict,
        test_comparisons=(comp_case,),
        test_tampering_detected=tampered,
    )


def test_ed25519_keypair_generation() -> None:
    """generate_ed25519_keypair returns valid PEM-encoded private and public keys."""
    priv_pem, pub_pem = generate_ed25519_keypair()
    assert isinstance(priv_pem, str)
    assert isinstance(pub_pem, str)
    assert "-----BEGIN PRIVATE KEY-----" in priv_pem
    assert "-----BEGIN PUBLIC KEY-----" in pub_pem


def test_ed25519_sign_and_verify_payload() -> None:
    """Signing a payload and verifying with corresponding public key succeeds."""
    priv_pem, pub_pem = generate_ed25519_keypair()
    payload = b"canonical-patchproof-payload-v2"
    sig_b64 = sign_payload_ed25519(payload, priv_pem)

    assert isinstance(sig_b64, str)
    # Valid base64
    assert len(base64.b64decode(sig_b64)) == 64  # Ed25519 signature is 64 bytes

    # Verify succeeds
    assert verify_payload_ed25519(payload, sig_b64, pub_pem) is True


def test_ed25519_verify_fails_on_tampered_payload() -> None:
    """Verification fails when payload or signature is modified."""
    priv_pem, pub_pem = generate_ed25519_keypair()
    payload = b"canonical-payload"
    sig_b64 = sign_payload_ed25519(payload, priv_pem)

    # Tampered payload
    tampered_payload = b"tampered-payload"
    assert verify_payload_ed25519(tampered_payload, sig_b64, pub_pem) is False

    # Corrupt signature
    corrupt_sig = base64.b64encode(b"0" * 64).decode("ascii")
    assert verify_payload_ed25519(payload, corrupt_sig, pub_pem) is False


def test_generate_asymmetric_certificate() -> None:
    """generate_certificate produces Ed25519-signed certificate when private key is given."""
    priv_pem, pub_pem = generate_ed25519_keypair()
    result = make_dummy_comparative_result()
    patch_text = "--- a/foo.py\n+++ b/foo.py\n@@ -1 +1 @@\n-old\n+new\n"

    cert = generate_certificate(
        result,
        patch_content=patch_text,
        base_commit="abc1234",
        private_key_pem=priv_pem,
        signer_identity="ci.patchproof.io/agent",
    )

    assert cert.algorithm == "Ed25519"
    assert cert.public_key_pem is not None
    assert cert.signer_identity == "ci.patchproof.io/agent"
    assert cert.assertion_baseline_count == 5
    assert cert.assertion_patched_count == 5

    # Verification with embedded public key
    assert verify_certificate(cert) is True

    # Verification with explicit public key
    assert verify_certificate(cert, public_key_pem=pub_pem) is True


def test_asymmetric_certificate_rejects_tampering() -> None:
    """Certificate verification fails if any property is tampered with."""
    priv_pem, _ = generate_ed25519_keypair()
    result = make_dummy_comparative_result()
    cert = generate_certificate(
        result,
        patch_content="some patch",
        private_key_pem=priv_pem,
    )

    # Forging verdict from ACCEPTED to REJECTED or changing patch hash
    tampered_cert = ProofCertificate(
        version=cert.version,
        patch_sha256="forged-hash",
        base_commit=cert.base_commit,
        verdict=cert.verdict,
        total_tests=cert.total_tests,
        passed_tests=cert.passed_tests,
        introduced_regressions=cert.introduced_regressions,
        sandbox_isolated=cert.sandbox_isolated,
        created_at_iso=cert.created_at_iso,
        signature=cert.signature,
        anti_cheat_passed=cert.anti_cheat_passed,
        algorithm=cert.algorithm,
        public_key_pem=cert.public_key_pem,
        signer_identity=cert.signer_identity,
        assertion_baseline_count=cert.assertion_baseline_count,
        assertion_patched_count=cert.assertion_patched_count,
    )
    assert verify_certificate(tampered_cert) is False


def test_backwards_compatible_hmac_certificate() -> None:
    """Existing HMAC-SHA256 signing and verification continues to function seamlessly."""
    result = make_dummy_comparative_result()
    cert = generate_certificate(
        result,
        patch_content="legacy patch",
        secret_key="my-secret-key",
    )
    assert cert.algorithm == "HMAC-SHA256"
    assert verify_certificate(cert, secret_key="my-secret-key") is True
    assert verify_certificate(cert, secret_key="wrong-key") is False
