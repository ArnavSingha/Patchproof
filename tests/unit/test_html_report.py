"""Unit tests for standalone visual HTML report generation."""

from __future__ import annotations

import html

from patchproof.models.sandbox import (
    ComparativeExecutionResult,
    ComparativeTestCase,
    ExecutionResult,
    PatchVerdict,
    RegressionClassification,
    TestOutcome,
    TestRunReport,
)
from patchproof.reporting.html_report import generate_html_report


def make_test_result(
    verdict: PatchVerdict = PatchVerdict.ACCEPTED,
    summary: str = "Verification passed with zero regressions",
    regressions: int = 0,
    defects_resolved: int = 0,
    comparisons: tuple[ComparativeTestCase, ...] = (),
) -> ComparativeExecutionResult:
    """Helper to synthesize ComparativeExecutionResult for HTML testing."""
    dummy_exec = ExecutionResult(
        exit_code=0 if verdict == PatchVerdict.ACCEPTED else 1,
        duration_seconds=1.23,
        test_report=TestRunReport(
            test_cases=(),
            exit_code=0,
            duration_seconds=1.23,
            total_collected=len(comparisons),
        ),
    )
    return ComparativeExecutionResult(
        baseline_result=dummy_exec,
        patched_result=dummy_exec,
        verdict=verdict,
        summary=summary,
        introduced_regressions_count=regressions,
        resolved_defects_count=defects_resolved,
        suppressed_tests_count=0,
        pre_existing_failures_count=0,
        test_comparisons=comparisons,
    )


class TestHtmlReportGenerator:
    """Test generating self-contained HTML proof dashboards."""

    def test_generates_valid_html_structure(self) -> None:
        result = make_test_result()
        html_out = generate_html_report(result, patch_content="--- a/x.py\n+++ b/x.py\n")

        assert "<!DOCTYPE html>" in html_out
        assert "<html" in html_out
        assert "PatchProof Verification Report" in html_out
        assert "ACCEPTED" in html_out
        assert "</html>" in html_out

    def test_verdict_badge_colors(self) -> None:
        res_accepted = make_test_result(verdict=PatchVerdict.ACCEPTED)
        html_accepted = generate_html_report(res_accepted)
        assert "status-accepted" in html_accepted

        res_rejected = make_test_result(
            verdict=PatchVerdict.REJECTED,
            regressions=1,
            summary="Introduced 1 regression",
        )
        html_rejected = generate_html_report(res_rejected)
        assert "status-rejected" in html_rejected

    def test_escapes_xss_content(self) -> None:
        malicious_summary = "<script>alert('xss')</script>"
        malicious_patch = "<script>alert('patch_xss')</script>"
        comparison = ComparativeTestCase(
            test_name="tests/test_x.py::<img src=x onerror=alert(1)>",
            baseline_outcome=TestOutcome.PASS,
            patched_outcome=TestOutcome.FAIL,
            classification=RegressionClassification.INTRODUCED_REGRESSION,
        )
        result = make_test_result(
            verdict=PatchVerdict.REJECTED,
            summary=malicious_summary,
            regressions=1,
            comparisons=(comparison,),
        )

        html_out = generate_html_report(result, patch_content=malicious_patch)

        # Raw script tags must never appear unescaped
        assert "<script>" not in html_out
        assert html.escape(malicious_summary) in html_out
        assert html.escape(malicious_patch) in html_out
        assert html.escape(comparison.test_name) in html_out

    def test_renders_test_comparisons_table(self) -> None:
        comp1 = ComparativeTestCase(
            test_name="tests/test_auth.py::test_login",
            baseline_outcome=TestOutcome.PASS,
            patched_outcome=TestOutcome.PASS,
            classification=RegressionClassification.CLEAN_PRESERVATION,
        )
        comp2 = ComparativeTestCase(
            test_name="tests/test_billing.py::test_invoice",
            baseline_outcome=TestOutcome.PASS,
            patched_outcome=TestOutcome.FAIL,
            classification=RegressionClassification.INTRODUCED_REGRESSION,
        )
        result = make_test_result(
            verdict=PatchVerdict.REJECTED,
            regressions=1,
            comparisons=(comp1, comp2),
        )

        html_out = generate_html_report(result)
        assert "tests/test_auth.py::test_login" in html_out
        assert "tests/test_billing.py::test_invoice" in html_out
        assert "clean_preservation" in html_out
        assert "introduced_regression" in html_out

    def test_offline_self_contained(self) -> None:
        result = make_test_result()
        html_out = generate_html_report(result)

        # Verify no external HTTP/HTTPS CDN references
        assert "http://" not in html_out
        assert "https://" not in html_out
        assert "<style>" in html_out
