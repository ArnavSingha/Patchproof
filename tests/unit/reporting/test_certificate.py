"""Unit tests for the Cryptographic Proof-of-Patch Certificate Engine."""

from __future__ import annotations

import json
import pathlib

from patchproof.models.sandbox import (
    ComparativeExecutionResult,
    ComparativeTestCase,
    ExecutionResult,
    PatchVerdict,
    RegressionClassification,
    TestCaseResult,
    TestOutcome,
    TestRunReport,
)
from patchproof.reporting.certificate import (
    ProofCertificate,
    generate_certificate,
    verify_certificate,
)


def create_mock_result(verdict: PatchVerdict = PatchVerdict.ACCEPTED) -> ComparativeExecutionResult:
    """Helper to create dummy comparative result."""
    t_case = TestCaseResult(name="test_1", outcome=TestOutcome.PASS, duration_seconds=0.01)
    report = TestRunReport(
        test_cases=(t_case,), exit_code=0, duration_seconds=0.01, total_collected=1
    )
    exec_res = ExecutionResult(exit_code=0, duration_seconds=0.01, test_report=report)
    comparison = ComparativeTestCase(
        test_name="test_1",
        baseline_outcome=TestOutcome.PASS,
        patched_outcome=TestOutcome.PASS,
        classification=RegressionClassification.CLEAN_PRESERVATION,
    )
    return ComparativeExecutionResult(
        baseline_result=exec_res,
        patched_result=exec_res,
        verdict=verdict,
        test_comparisons=(comparison,),
        introduced_regressions_count=0,
        summary="Verified clean",
    )


def test_generate_and_verify_valid_certificate() -> None:
    """Test generating a certificate and validating its cryptographic HMAC signature."""
    result = create_mock_result(PatchVerdict.ACCEPTED)
    patch_text = "--- a/x.py\n+++ b/x.py\n@@ -1,1 +1,1 @@\n-1\n+2\n"

    cert = generate_certificate(result, patch_text, secret_key="my-secret-key")

    assert cert.verdict == "accepted"
    assert cert.total_tests == 1
    assert cert.passed_tests == 1
    assert cert.introduced_regressions == 0
    assert cert.sandbox_isolated is True
    assert len(cert.signature) == 64  # SHA-256 hex string

    # Verification must pass with matching secret key
    assert verify_certificate(cert, secret_key="my-secret-key") is True

    # Verification must fail with wrong secret key
    assert verify_certificate(cert, secret_key="wrong-secret-key") is False


def test_tampered_certificate_fails_verification() -> None:
    """Tampering with any field in the certificate must cause signature verification failure."""
    result = create_mock_result(PatchVerdict.ACCEPTED)
    patch_text = "--- a/x.py\n+++ b/x.py\n@@ -1,1 +1,1 @@\n-1\n+2\n"

    cert = generate_certificate(result, patch_text, secret_key="my-secret-key")

    # Tamper with total_tests
    tampered_dict = cert.to_dict()
    tampered_dict["total_tests"] = 999
    tampered_cert = ProofCertificate(**tampered_dict)

    assert verify_certificate(tampered_cert, secret_key="my-secret-key") is False


def test_export_and_import_certificate(tmp_path: pathlib.Path) -> None:
    """Test serializing to JSON and deserializing certificate."""
    result = create_mock_result(PatchVerdict.ACCEPTED)
    patch_text = "--- a/x.py\n+++ b/x.py\n@@ -1,1 +1,1 @@\n-1\n+2\n"
    cert = generate_certificate(result, patch_text, secret_key="test-key")

    out_file = tmp_path / "patchproof.cert.json"
    out_file.write_text(json.dumps(cert.to_dict(), indent=2), encoding="utf-8")

    loaded_dict = json.loads(out_file.read_text(encoding="utf-8"))
    loaded_cert = ProofCertificate(**loaded_dict)

    assert loaded_cert.signature == cert.signature
    assert verify_certificate(loaded_cert, secret_key="test-key") is True
