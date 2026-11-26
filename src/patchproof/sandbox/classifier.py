"""Baseline-versus-patched regression classification and empirical verdict synthesis."""

from __future__ import annotations

from patchproof.models.sandbox import (
    ComparativeExecutionResult,
    ComparativeTestCase,
    ExecutionResult,
    PatchVerdict,
    RegressionClassification,
    SandboxFailureReason,
    TestCaseResult,
    TestOutcome,
)


class RegressionClassifier:
    """Reconciles test run reports and failure signals between baseline and patched executions."""

    def classify(
        self,
        baseline_result: ExecutionResult,
        patched_result: ExecutionResult,
    ) -> ComparativeExecutionResult:
        """Compare baseline and patched test results to produce an immutable comparative report."""
        base_report = baseline_result.test_report
        patch_report = patched_result.test_report

        # 1. Check for infrastructure failures in baseline
        if (
            baseline_result.failure_reason
            in (
                SandboxFailureReason.TIMEOUT,
                SandboxFailureReason.OOM_KILLED,
                SandboxFailureReason.DOCKER_UNAVAILABLE,
                SandboxFailureReason.UNSUPPORTED_ENGINE,
            )
            or (base_report is not None and base_report.collection_failed)
            or base_report is None
        ):
            summary = self._summarize_failure(
                "Baseline execution failed",
                baseline_result,
                base_report.collection_error if base_report else None,
            )
            return ComparativeExecutionResult(
                baseline_result=baseline_result,
                patched_result=patched_result,
                verdict=PatchVerdict.INFRASTRUCTURE_FAILURE,
                summary=summary,
            )

        # 2. Check for patched run hard infrastructure/lifecycle failures
        if patched_result.failure_reason == SandboxFailureReason.TIMEOUT:
            return ComparativeExecutionResult(
                baseline_result=baseline_result,
                patched_result=patched_result,
                verdict=PatchVerdict.REJECTED,
                summary="Patched execution exceeded host timeout quota",
            )

        if patched_result.failure_reason == SandboxFailureReason.OOM_KILLED:
            return ComparativeExecutionResult(
                baseline_result=baseline_result,
                patched_result=patched_result,
                verdict=PatchVerdict.REJECTED,
                summary="Patched execution was terminated by Linux cgroup OOM killer",
            )

        if patched_result.failure_reason == SandboxFailureReason.SETUP_ERROR:
            err_msg = patched_result.stderr or "Setup error"
            return ComparativeExecutionResult(
                baseline_result=baseline_result,
                patched_result=patched_result,
                verdict=PatchVerdict.REJECTED,
                summary=f"Patched environment setup failed: {err_msg}",
            )

        if patch_report is not None and patch_report.collection_failed:
            coll_err = patch_report.collection_error or "Collection error"
            return ComparativeExecutionResult(
                baseline_result=baseline_result,
                patched_result=patched_result,
                verdict=PatchVerdict.REJECTED,
                collection_failure_occurred=True,
                summary=f"Patched test collection failed: {coll_err}",
            )

        if patch_report is None:
            return ComparativeExecutionResult(
                baseline_result=baseline_result,
                patched_result=patched_result,
                verdict=PatchVerdict.REJECTED,
                summary=f"Patched run terminated abnormally (exit {patched_result.exit_code})",
            )

        # 3. Reconcile individual test cases
        base_map: dict[str, TestCaseResult] = base_report.test_map
        patch_map: dict[str, TestCaseResult] = patch_report.test_map
        all_test_names = sorted(set(base_map.keys()) | set(patch_map.keys()))

        comparisons: list[ComparativeTestCase] = []
        introduced_regressions = 0
        resolved_defects = 0
        pre_existing_failures = 0
        suppressed_tests = 0

        for name in all_test_names:
            b_case = base_map.get(name)
            p_case = patch_map.get(name)

            b_outcome = b_case.outcome if b_case else None
            p_outcome = p_case.outcome if p_case else None

            classification, msg = self._classify_transition(b_outcome, p_outcome)

            if classification == RegressionClassification.INTRODUCED_REGRESSION:
                introduced_regressions += 1
            elif classification == RegressionClassification.RESOLVED_DEFECT:
                resolved_defects += 1
            elif classification == RegressionClassification.PRE_EXISTING_FAILURE:
                pre_existing_failures += 1
            elif classification == RegressionClassification.SUPPRESSION_ALERT:
                suppressed_tests += 1

            comparisons.append(
                ComparativeTestCase(
                    test_name=name,
                    baseline_outcome=b_outcome,
                    patched_outcome=p_outcome,
                    classification=classification,
                    message=msg,
                )
            )

        # 4. Formulate empirical verdict
        if introduced_regressions > 0:
            verdict = PatchVerdict.REJECTED
            summary = f"Patch rejected: introduced {introduced_regressions} regression(s)"
        elif suppressed_tests > 0:
            verdict = PatchVerdict.SUSPICIOUS
            summary = (
                f"Patch flagged suspicious: {suppressed_tests} previously passing test(s) "
                "were suppressed"
            )
        elif pre_existing_failures > 0 and patched_result.exit_code != 0:
            verdict = PatchVerdict.SUSPICIOUS
            summary = (
                f"Patch left {pre_existing_failures} pre-existing failure(s) unresolved "
                f"({resolved_defects} defect(s) resolved)"
            )
        elif len(all_test_names) == 0:
            verdict = PatchVerdict.INCONCLUSIVE
            summary = "No test cases were discovered in either baseline or patched execution"
        else:
            verdict = PatchVerdict.ACCEPTED
            summary = (
                f"Patch verified clean: {len(comparisons)} test(s) preserved, "
                f"{resolved_defects} defect(s) resolved, 0 regressions"
            )

        return ComparativeExecutionResult(
            baseline_result=baseline_result,
            patched_result=patched_result,
            verdict=verdict,
            test_comparisons=tuple(comparisons),
            introduced_regressions_count=introduced_regressions,
            resolved_defects_count=resolved_defects,
            pre_existing_failures_count=pre_existing_failures,
            suppressed_tests_count=suppressed_tests,
            collection_failure_occurred=False,
            summary=summary,
        )

    def _classify_transition(
        self,
        b_outcome: TestOutcome | None,
        p_outcome: TestOutcome | None,
    ) -> tuple[RegressionClassification, str | None]:
        """Classify the outcome shift for a single test case."""
        # 1. New test added
        if b_outcome is None:
            if p_outcome == TestOutcome.PASS:
                return RegressionClassification.NEW_TEST_PASSING, "New test passing"
            if p_outcome in (TestOutcome.FAIL, TestOutcome.ERROR):
                return RegressionClassification.NEW_TEST_FAILING, "New test failing"
            return RegressionClassification.NEW_TEST_SKIPPED, "New test skipped"

        # 2. Existing test removed
        if p_outcome is None:
            return (
                RegressionClassification.REMOVED_TEST,
                "Test removed or uncollected in patched code",
            )

        # 3. Clean preservation
        if b_outcome == TestOutcome.PASS and p_outcome == TestOutcome.PASS:
            return RegressionClassification.CLEAN_PRESERVATION, None

        # 4. Introduced regression
        if b_outcome == TestOutcome.PASS and p_outcome in (TestOutcome.FAIL, TestOutcome.ERROR):
            return (
                RegressionClassification.INTRODUCED_REGRESSION,
                f"Regression: shifted from {b_outcome.value} to {p_outcome.value}",
            )

        # 5. Resolved defect
        if b_outcome in (TestOutcome.FAIL, TestOutcome.ERROR) and p_outcome == TestOutcome.PASS:
            return (
                RegressionClassification.RESOLVED_DEFECT,
                f"Resolved: shifted from {b_outcome.value} to {p_outcome.value}",
            )

        # 6. Pre-existing failure
        if b_outcome in (TestOutcome.FAIL, TestOutcome.ERROR) and p_outcome in (
            TestOutcome.FAIL,
            TestOutcome.ERROR,
        ):
            return (
                RegressionClassification.PRE_EXISTING_FAILURE,
                "Pre-existing failure unchanged",
            )

        # 7. Suppression alert
        if b_outcome == TestOutcome.PASS and p_outcome == TestOutcome.SKIPPED:
            return (
                RegressionClassification.SUPPRESSION_ALERT,
                "Suppression: passing test became skipped in patched code",
            )

        # 8. Pre-existing skip
        if b_outcome == TestOutcome.SKIPPED and p_outcome == TestOutcome.SKIPPED:
            return RegressionClassification.PRE_EXISTING_SKIP, None

        # Fallback
        if p_outcome in (TestOutcome.FAIL, TestOutcome.ERROR):
            return (
                RegressionClassification.INTRODUCED_REGRESSION,
                f"Shifted from {b_outcome.value} to {p_outcome.value}",
            )

        return RegressionClassification.CLEAN_PRESERVATION, None

    def _summarize_failure(
        self,
        prefix: str,
        res: ExecutionResult,
        extra_err: str | None = None,
    ) -> str:
        """Construct human-readable diagnostic failure summary."""
        parts = [prefix]
        if res.failure_reason != SandboxFailureReason.NONE:
            parts.append(f"reason={res.failure_reason.value}")
        if res.exit_code != 0:
            parts.append(f"exit_code={res.exit_code}")
        if extra_err:
            parts.append(f"error={extra_err[:80]}...")
        return ": ".join(parts)
