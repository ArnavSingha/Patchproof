"""Unit tests for the GitHub PR and step summary Markdown report generator."""

from __future__ import annotations

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
from patchproof.reporting.certificate import generate_certificate
from patchproof.reporting.markdown import generate_markdown_summary


def make_comparative_result(
    verdict: PatchVerdict = PatchVerdict.ACCEPTED,
    test_tampering_detected: bool = False,
    tampering_details: tuple[str, ...] = (),
    regressions: int = 0,
    resolved: int = 0,
) -> ComparativeExecutionResult:
    """Helper to build sample comparative results for markdown testing."""
    t1 = TestCaseResult(
        name="tests/test_api.py::test_login", outcome=TestOutcome.PASS, duration_seconds=0.01
    )
    report = TestRunReport(test_cases=(t1,), exit_code=0, duration_seconds=0.01, total_collected=1)
    base_res = ExecutionResult(exit_code=0, duration_seconds=0.01, test_report=report)
    patched_res = ExecutionResult(exit_code=0, duration_seconds=0.01, test_report=report)

    comparisons = [
        ComparativeTestCase(
            test_name="tests/test_api.py::test_login",
            baseline_outcome=TestOutcome.PASS,
            patched_outcome=TestOutcome.PASS,
            classification=RegressionClassification.CLEAN_PRESERVATION,
        )
    ]

    return ComparativeExecutionResult(
        baseline_result=base_res,
        patched_result=patched_res,
        verdict=verdict,
        test_comparisons=tuple(comparisons),
        introduced_regressions_count=regressions,
        resolved_defects_count=resolved,
        test_tampering_detected=test_tampering_detected,
        tampering_details=tampering_details,
        summary="Verified clean",
    )


class TestMarkdownSummaryGenerator:
    """Test generation of rich GitHub Markdown PR proof cards and CI job summaries."""

    def test_clean_accepted_summary_generation(self) -> None:
        """Verify markdown report for a clean accepted verification."""
        result = make_comparative_result(verdict=PatchVerdict.ACCEPTED, resolved=1)
        cert = generate_certificate(result, patch_content="diff --git a/x b/x\n")

        md = generate_markdown_summary(result, cert=cert)

        assert "<!-- patchproof-attestation-marker -->" in md
        assert "PatchProof Verification" in md
        assert "ACCEPTED" in md
        assert "PASSED" in md
        assert "Anti-Cheat Integrity" in md
        assert cert.signature[:16] in md

    def test_tampering_detected_summary_generation(self) -> None:
        """Verify markdown report explicitly highlights tampering alerts."""
        result = make_comparative_result(
            verdict=PatchVerdict.SUSPICIOUS,
            test_tampering_detected=True,
            tampering_details=("Test assertion count decreased from 5 to 1",),
        )

        md = generate_markdown_summary(result)

        assert "SUSPICIOUS" in md
        assert "TAMPERING DETECTED" in md
        assert "Test assertion count decreased" in md

    def test_regression_detected_summary_generation(self) -> None:
        """Verify markdown report surfaces introduced regressions and details."""
        t1 = TestCaseResult(
            name="tests/test_auth.py::test_token", outcome=TestOutcome.PASS, duration_seconds=0.01
        )
        t2 = TestCaseResult(
            name="tests/test_auth.py::test_token", outcome=TestOutcome.FAIL, duration_seconds=0.01
        )
        base_rep = TestRunReport(
            test_cases=(t1,), exit_code=0, duration_seconds=0.01, total_collected=1
        )
        patch_rep = TestRunReport(
            test_cases=(t2,), exit_code=1, duration_seconds=0.01, total_collected=1
        )

        result = ComparativeExecutionResult(
            baseline_result=ExecutionResult(
                exit_code=0, duration_seconds=0.01, test_report=base_rep
            ),
            patched_result=ExecutionResult(
                exit_code=1,
                stderr="AssertionError: expected 200 got 500",
                duration_seconds=0.01,
                test_report=patch_rep,
            ),
            verdict=PatchVerdict.REJECTED,
            test_comparisons=(
                ComparativeTestCase(
                    test_name="tests/test_auth.py::test_token",
                    baseline_outcome=TestOutcome.PASS,
                    patched_outcome=TestOutcome.FAIL,
                    classification=RegressionClassification.INTRODUCED_REGRESSION,
                    message="Regression: shifted from pass to fail",
                ),
            ),
            introduced_regressions_count=1,
            summary="Patch rejected: introduced 1 regression(s)",
        )

        md = generate_markdown_summary(result)

        assert "REJECTED" in md
        assert "1 regression(s)" in md
        assert "tests/test_auth.py::test_token" in md
        assert "AssertionError: expected 200 got 500" in md
