"""Unit tests for baseline-versus-patched regression classification and verdicts."""

from __future__ import annotations

import json

from patchproof.models.sandbox import (
    ExecutionResult,
    PatchVerdict,
    RegressionClassification,
    SandboxFailureReason,
    TestCaseResult,
    TestOutcome,
    TestRunReport,
)
from patchproof.sandbox.classifier import RegressionClassifier


def make_exec_result(
    test_cases: tuple[TestCaseResult, ...] = (),
    exit_code: int = 0,
    collection_failed: bool = False,
    failure_reason: SandboxFailureReason = SandboxFailureReason.NONE,
    oom_killed: bool = False,
    timeout_triggered: bool = False,
) -> ExecutionResult:
    report = TestRunReport(
        test_cases=test_cases,
        exit_code=exit_code,
        duration_seconds=0.1,
        total_collected=len(test_cases),
        collection_failed=collection_failed,
    )
    return ExecutionResult(
        exit_code=exit_code,
        stdout="report",
        duration_seconds=0.1,
        failure_reason=failure_reason,
        oom_killed=oom_killed,
        timeout_triggered=timeout_triggered,
        test_report=report,
    )


class TestRegressionClassifier:
    """Test deterministic regression classification according to PatchProof rules."""

    def test_clean_preservation_and_accepted_verdict(self) -> None:
        t1 = TestCaseResult(name="test_1", outcome=TestOutcome.PASS, duration_seconds=0.01)
        base = make_exec_result(test_cases=(t1,))
        patch = make_exec_result(test_cases=(t1,))

        classifier = RegressionClassifier()
        comp = classifier.classify(base, patch)

        assert comp.verdict == PatchVerdict.ACCEPTED
        assert comp.introduced_regressions_count == 0
        assert len(comp.test_comparisons) == 1
        assert (
            comp.test_comparisons[0].classification == RegressionClassification.CLEAN_PRESERVATION
        )

    def test_detect_introduced_regression_rejects_patch(self) -> None:
        t_base = TestCaseResult(name="test_1", outcome=TestOutcome.PASS, duration_seconds=0.01)
        t_patch = TestCaseResult(name="test_1", outcome=TestOutcome.FAIL, duration_seconds=0.01)

        base = make_exec_result(test_cases=(t_base,))
        patch = make_exec_result(test_cases=(t_patch,), exit_code=1)

        classifier = RegressionClassifier()
        comp = classifier.classify(base, patch)

        assert comp.verdict == PatchVerdict.REJECTED
        assert comp.introduced_regressions_count == 1
        assert (
            comp.test_comparisons[0].classification
            == RegressionClassification.INTRODUCED_REGRESSION
        )

    def test_detect_resolved_defect(self) -> None:
        t_base = TestCaseResult(name="test_bug", outcome=TestOutcome.FAIL, duration_seconds=0.01)
        t_patch = TestCaseResult(name="test_bug", outcome=TestOutcome.PASS, duration_seconds=0.01)

        base = make_exec_result(test_cases=(t_base,), exit_code=1)
        patch = make_exec_result(test_cases=(t_patch,), exit_code=0)

        classifier = RegressionClassifier()
        comp = classifier.classify(base, patch)

        assert comp.verdict == PatchVerdict.ACCEPTED
        assert comp.resolved_defects_count == 1
        assert comp.test_comparisons[0].classification == RegressionClassification.RESOLVED_DEFECT

    def test_detect_pre_existing_failure(self) -> None:
        t1 = TestCaseResult(name="test_broken", outcome=TestOutcome.FAIL, duration_seconds=0.01)
        base = make_exec_result(test_cases=(t1,), exit_code=1)
        patch = make_exec_result(test_cases=(t1,), exit_code=1)

        classifier = RegressionClassifier()
        comp = classifier.classify(base, patch)

        assert comp.pre_existing_failures_count == 1
        assert (
            comp.test_comparisons[0].classification == RegressionClassification.PRE_EXISTING_FAILURE
        )
        # Note: Pre-existing failure alone without regressions does not accept if tests still fail
        assert comp.verdict != PatchVerdict.ACCEPTED

    def test_detect_suppression_alert_flags_suspicious(self) -> None:
        t_base = TestCaseResult(
            name="test_important", outcome=TestOutcome.PASS, duration_seconds=0.01
        )
        t_patch = TestCaseResult(
            name="test_important", outcome=TestOutcome.SKIPPED, duration_seconds=0.01
        )

        base = make_exec_result(test_cases=(t_base,))
        patch = make_exec_result(test_cases=(t_patch,))

        classifier = RegressionClassifier()
        comp = classifier.classify(base, patch)

        assert comp.verdict == PatchVerdict.SUSPICIOUS
        assert comp.suppressed_tests_count == 1
        assert comp.test_comparisons[0].classification == RegressionClassification.SUPPRESSION_ALERT

    def test_collection_failure_in_patch_rejects(self) -> None:
        t1 = TestCaseResult(name="test_1", outcome=TestOutcome.PASS, duration_seconds=0.01)
        base = make_exec_result(test_cases=(t1,))
        patch = make_exec_result(test_cases=(), exit_code=2, collection_failed=True)

        classifier = RegressionClassifier()
        comp = classifier.classify(base, patch)

        assert comp.verdict == PatchVerdict.REJECTED
        assert comp.collection_failure_occurred is True

    def test_collection_failure_in_baseline_flags_infrastructure_failure(self) -> None:
        base = make_exec_result(test_cases=(), exit_code=2, collection_failed=True)
        t1 = TestCaseResult(name="test_1", outcome=TestOutcome.PASS, duration_seconds=0.01)
        patch = make_exec_result(test_cases=(t1,))

        classifier = RegressionClassifier()
        comp = classifier.classify(base, patch)

        assert comp.verdict == PatchVerdict.INFRASTRUCTURE_FAILURE

    def test_timeout_in_patch_rejects(self) -> None:
        t1 = TestCaseResult(name="test_1", outcome=TestOutcome.PASS, duration_seconds=0.01)
        base = make_exec_result(test_cases=(t1,))
        patch = make_exec_result(
            test_cases=(),
            exit_code=137,
            timeout_triggered=True,
            failure_reason=SandboxFailureReason.TIMEOUT,
        )

        classifier = RegressionClassifier()
        comp = classifier.classify(base, patch)

        assert comp.verdict == PatchVerdict.REJECTED
        assert "timeout" in comp.summary.lower()

    def test_oom_killed_in_patch_rejects(self) -> None:
        t1 = TestCaseResult(name="test_1", outcome=TestOutcome.PASS, duration_seconds=0.01)
        base = make_exec_result(test_cases=(t1,))
        patch = make_exec_result(
            test_cases=(),
            exit_code=137,
            oom_killed=True,
            failure_reason=SandboxFailureReason.OOM_KILLED,
        )

        classifier = RegressionClassifier()
        comp = classifier.classify(base, patch)

        assert comp.verdict == PatchVerdict.REJECTED
        assert "oom" in comp.summary.lower()

    def test_detect_new_tests_and_removed_tests(self) -> None:
        t_common = TestCaseResult(name="t_com", outcome=TestOutcome.PASS, duration_seconds=0.01)
        t_old = TestCaseResult(name="t_old", outcome=TestOutcome.PASS, duration_seconds=0.01)
        t_new = TestCaseResult(name="t_new", outcome=TestOutcome.PASS, duration_seconds=0.01)

        base = make_exec_result(test_cases=(t_common, t_old))
        patch = make_exec_result(test_cases=(t_common, t_new))

        classifier = RegressionClassifier()
        comp = classifier.classify(base, patch)

        classifications = {c.test_name: c.classification for c in comp.test_comparisons}
        assert classifications["t_com"] == RegressionClassification.CLEAN_PRESERVATION
        assert classifications["t_old"] == RegressionClassification.REMOVED_TEST
        assert classifications["t_new"] == RegressionClassification.NEW_TEST_PASSING
        assert comp.removed_tests_count == 1
        assert comp.verdict == PatchVerdict.SUSPICIOUS

    def test_dynamic_runtime_assertion_drop_flags_tampering(self) -> None:
        t1 = TestCaseResult(name="test_1", outcome=TestOutcome.PASS, duration_seconds=0.01)
        base = make_exec_result(test_cases=(t1,))
        # Baseline executed 10 assertions
        base_payload = json.dumps(
            {
                "collected_count": 1,
                "executed_count": 1,
                "executed_assertions": 10,
                "assertions_by_test": {"test_1": 10},
            }
        )
        base = base.model_copy(update={"stdout": f"__PATCHPROOF_RUNTIME_PROFILE__={base_payload}"})
        patch = make_exec_result(test_cases=(t1,))
        # Patched executed only 3 assertions
        patch_payload = json.dumps(
            {
                "collected_count": 1,
                "executed_count": 1,
                "executed_assertions": 3,
                "assertions_by_test": {"test_1": 3},
            }
        )
        patch = patch.model_copy(
            update={"stdout": f"__PATCHPROOF_RUNTIME_PROFILE__={patch_payload}"}
        )

        classifier = RegressionClassifier()
        comp = classifier.classify(base, patch)

        assert comp.test_tampering_detected is True
        assert comp.verdict == PatchVerdict.SUSPICIOUS
        assert len(comp.tampering_details) > 0
        assert "7 fewer assertion(s)" in comp.tampering_details[0]

    def test_dynamic_runtime_guard_suppressed_flags_tampering(self) -> None:
        t1 = TestCaseResult(name="test_1", outcome=TestOutcome.PASS, duration_seconds=0.01)
        base = make_exec_result(test_cases=(t1,))
        base_payload = json.dumps(
            {
                "collected_count": 1,
                "executed_count": 1,
                "executed_assertions": 5,
                "assertions_by_test": {"test_1": 5},
            }
        )
        base = base.model_copy(update={"stdout": f"__PATCHPROOF_RUNTIME_PROFILE__={base_payload}"})
        patch = make_exec_result(test_cases=(t1,))
        # Patched suppressed the guard
        patch = patch.model_copy(update={"stdout": "patch suppressed guard output"})

        classifier = RegressionClassifier()
        comp = classifier.classify(base, patch)

        assert comp.test_tampering_detected is True
        assert comp.verdict == PatchVerdict.SUSPICIOUS
        assert "suppressed the PatchProof runtime guard" in comp.tampering_details[0]

