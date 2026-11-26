"""Unit tests for the Flakiness Disambiguation Engine."""

from __future__ import annotations

from patchproof.sandbox.flakiness import (
    FlakeAnalysisReport,
    FlakeStatus,
    disambiguate_flakiness,
)


def test_flakiness_all_failures_is_deterministic() -> None:
    """When all retry attempts fail, it must be classified as a DETERMINISTIC_REGRESSION."""
    # Runner always returns False (failed)
    report: FlakeAnalysisReport = disambiguate_flakiness(
        test_name="tests/test_foo.py::test_fail",
        runner_fn=lambda _name: (False, 0.05),
        attempts=3,
    )

    assert report.status == FlakeStatus.DETERMINISTIC_REGRESSION
    assert report.attempts == 3
    assert report.fail_count == 3
    assert report.pass_count == 0
    assert not report.is_flaky


def test_flakiness_mixed_outcomes_is_flaky() -> None:
    """When retry outcomes vary (fail, pass, fail), it must be classified as FLAKY_TRANSIENT."""
    outcomes = [(False, 0.05), (True, 0.04), (False, 0.05)]
    idx = 0

    def mock_runner(_name: str) -> tuple[bool, float]:
        nonlocal idx
        res = outcomes[idx]
        idx += 1
        return res

    report: FlakeAnalysisReport = disambiguate_flakiness(
        test_name="tests/test_timing.py::test_race",
        runner_fn=mock_runner,
        attempts=3,
    )

    assert report.status == FlakeStatus.FLAKY_TRANSIENT
    assert report.attempts == 3
    assert report.fail_count == 2
    assert report.pass_count == 1
    assert report.is_flaky
    assert "flaky" in report.summary.lower()


def test_flakiness_all_passes_is_transient_anomaly() -> None:
    """When all retries pass, it indicates an isolated transient glitch."""
    report: FlakeAnalysisReport = disambiguate_flakiness(
        test_name="tests/test_net.py::test_transient",
        runner_fn=lambda _name: (True, 0.02),
        attempts=3,
    )

    assert report.status == FlakeStatus.TRANSIENT_ANOMALY
    assert report.attempts == 3
    assert report.pass_count == 3
    assert report.fail_count == 0
    assert report.is_flaky
