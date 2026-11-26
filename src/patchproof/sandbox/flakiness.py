"""Flakiness Disambiguation Engine.

Differentiates true deterministic regressions from non-deterministic test flakes,
intermittent race conditions, and transient timing anomalies inside the isolated sandbox.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum


class FlakeStatus(StrEnum):
    """Categorization of test determinism across repeated executions."""

    DETERMINISTIC_REGRESSION = "deterministic_regression"
    FLAKY_TRANSIENT = "flaky_transient"
    TRANSIENT_ANOMALY = "transient_anomaly"


@dataclass(frozen=True)
class FlakeAnalysisReport:
    """Consolidated assessment of test repeatability and flakiness."""

    __test__ = False

    test_name: str
    attempts: int
    pass_count: int
    fail_count: int
    status: FlakeStatus
    durations: tuple[float, ...]
    summary: str

    @property
    def is_flaky(self) -> bool:
        """Return True if the test demonstrated non-deterministic outcomes."""
        return self.status in (FlakeStatus.FLAKY_TRANSIENT, FlakeStatus.TRANSIENT_ANOMALY)


def disambiguate_flakiness(
    test_name: str,
    runner_fn: Callable[[str], tuple[bool, float]],
    attempts: int = 3,
) -> FlakeAnalysisReport:
    """Execute target test repeatedly to establish determinism or flakiness."""
    pass_count = 0
    fail_count = 0
    durations: list[float] = []

    for _ in range(attempts):
        passed, duration = runner_fn(test_name)
        durations.append(duration)
        if passed:
            pass_count += 1
        else:
            fail_count += 1

    if fail_count == attempts:
        status = FlakeStatus.DETERMINISTIC_REGRESSION
        summary = (
            f"Test '{test_name}' failed consistently across all {attempts} runs "
            "(confirmed deterministic regression)."
        )
    elif pass_count == attempts:
        status = FlakeStatus.TRANSIENT_ANOMALY
        summary = (
            f"Test '{test_name}' passed across all {attempts} retry runs "
            "(initial failure was a transient anomaly)."
        )
    else:
        status = FlakeStatus.FLAKY_TRANSIENT
        summary = (
            f"Test '{test_name}' is non-deterministic / flaky: passed {pass_count}/{attempts}, "
            f"failed {fail_count}/{attempts}."
        )

    return FlakeAnalysisReport(
        test_name=test_name,
        attempts=attempts,
        pass_count=pass_count,
        fail_count=fail_count,
        status=status,
        durations=tuple(durations),
        summary=summary,
    )
