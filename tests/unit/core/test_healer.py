"""Unit tests for the Autonomous Self-Healing Engine."""

from __future__ import annotations

from unittest.mock import MagicMock

from patchproof.core.healer import AutonomousHealer, HealResult
from patchproof.models.sandbox import (
    ComparativeExecutionResult,
    ComparativeTestCase,
    ContainerConfig,
    ExecutionResult,
    PatchVerdict,
    RegressionClassification,
    ResourceLimits,
    TestCaseResult,
    TestOutcome,
    TestRunReport,
)
from patchproof.reporting.diagnostic import AISelfHealingReport


def create_mock_result(verdict: PatchVerdict) -> ComparativeExecutionResult:
    """Helper to create comparative result."""
    t_case = TestCaseResult(name="test_1", outcome=TestOutcome.PASS, duration_seconds=0.01)
    report = TestRunReport(
        test_cases=(t_case,), exit_code=0, duration_seconds=0.01, total_collected=1
    )
    exec_res = ExecutionResult(
        exit_code=0 if verdict == PatchVerdict.ACCEPTED else 1,
        duration_seconds=0.01,
        test_report=report,
    )
    comparison = ComparativeTestCase(
        test_name="test_1",
        baseline_outcome=TestOutcome.PASS,
        patched_outcome=TestOutcome.PASS if verdict == PatchVerdict.ACCEPTED else TestOutcome.FAIL,
        classification=(
            RegressionClassification.CLEAN_PRESERVATION
            if verdict == PatchVerdict.ACCEPTED
            else RegressionClassification.INTRODUCED_REGRESSION
        ),
    )
    return ComparativeExecutionResult(
        baseline_result=exec_res,
        patched_result=exec_res,
        verdict=verdict,
        test_comparisons=(comparison,),
        introduced_regressions_count=0 if verdict == PatchVerdict.ACCEPTED else 1,
        summary="Verdict summary",
    )


def test_healer_already_green() -> None:
    """If the initial patch is already green, the healer terminates in 1 iteration."""
    mock_runner = MagicMock()
    mock_runner.run.return_value = create_mock_result(PatchVerdict.ACCEPTED)

    healer = AutonomousHealer(runner=mock_runner)
    config = ContainerConfig(
        host_repo_path=".",
        resources=ResourceLimits(),
    )

    result: HealResult = healer.heal(
        config=config,
        initial_patch="--- a/x.py\n+++ b/x.py\n@@ -1,1 +1,1 @@\n-1\n+2\n",
        repair_agent_fn=lambda _diag: "--- a/x.py\n+++ b/x.py\n@@ -1,1 +1,1 @@\n-1\n+3\n",
        max_iterations=3,
    )

    assert result.success is True
    assert result.iterations == 1
    assert mock_runner.run.call_count == 1


def test_healer_converges_on_second_turn() -> None:
    """If initial patch fails, but the repair agent fixes it on turn 2, healer succeeds."""
    mock_runner = MagicMock()
    # Turn 1: REJECTED, Turn 2: ACCEPTED
    mock_runner.run.side_effect = [
        create_mock_result(PatchVerdict.REJECTED),
        create_mock_result(PatchVerdict.ACCEPTED),
    ]

    healer = AutonomousHealer(runner=mock_runner)
    config = ContainerConfig(
        host_repo_path=".",
        resources=ResourceLimits(),
    )

    improved_patch = "--- a/x.py\n+++ b/x.py\n@@ -1,1 +1,1 @@\n-1\n+corrected\n"

    def agent_callback(report: AISelfHealingReport) -> str:
        assert report.verdict == "REJECTED"
        return improved_patch

    result: HealResult = healer.heal(
        config=config,
        initial_patch="--- a/x.py\n+++ b/x.py\n@@ -1,1 +1,1 @@\n-1\n+broken\n",
        repair_agent_fn=agent_callback,
        max_iterations=3,
    )

    assert result.success is True
    assert result.iterations == 2
    assert result.final_patch == improved_patch
    assert mock_runner.run.call_count == 2


def test_healer_exhausts_max_iterations() -> None:
    """If repair agent fails to fix regressions within max_iterations, healer returns failure."""
    mock_runner = MagicMock()
    mock_runner.run.return_value = create_mock_result(PatchVerdict.REJECTED)

    healer = AutonomousHealer(runner=mock_runner)
    config = ContainerConfig(
        host_repo_path=".",
        resources=ResourceLimits(),
    )

    result: HealResult = healer.heal(
        config=config,
        initial_patch="--- a/x.py\n+++ b/x.py\n@@ -1,1 +1,1 @@\n-1\n+broken\n",
        repair_agent_fn=lambda _diag: (
            "--- a/x.py\n+++ b/x.py\n@@ -1,1 +1,1 @@\n-1\n+still_broken\n"
        ),
        max_iterations=3,
    )

    assert result.success is False
    assert result.iterations == 3
    assert mock_runner.run.call_count == 3
