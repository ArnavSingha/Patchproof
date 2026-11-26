"""Autonomous Patch Self-Healing Engine.

Orchestrates multi-turn convergence loops using Fault Localization diagnostics
to automatically repair regressions and produce verified green patches.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass

from patchproof.core.normalizer import parse_patch
from patchproof.models.patch import CanonicalPatch
from patchproof.models.sandbox import (
    ComparativeExecutionResult,
    ContainerConfig,
    PatchVerdict,
)
from patchproof.reporting.diagnostic import AISelfHealingReport, generate_ai_feedback
from patchproof.sandbox.comparative import ComparativeRunner

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class HealResult:
    """Consolidated outcome of an autonomous self-healing session."""

    success: bool
    iterations: int
    final_patch: str
    last_result: ComparativeExecutionResult
    summary: str


class AutonomousHealer:
    """Orchestrates an automated repair-and-verify convergence loop."""

    def __init__(self, runner: ComparativeRunner | None = None) -> None:
        self.runner = runner or ComparativeRunner()

    def heal(
        self,
        config: ContainerConfig,
        initial_patch: str,
        repair_agent_fn: Callable[[AISelfHealingReport], str | None],
        max_iterations: int = 3,
        disambiguate_flakes: bool = False,
    ) -> HealResult:
        """Iteratively verify and repair a patch until regression-free or bounded limit."""
        current_patch = initial_patch
        last_result: ComparativeExecutionResult | None = None

        for iteration in range(1, max_iterations + 1):
            logger.info("Starting healing iteration %d/%d", iteration, max_iterations)
            last_result = self.runner.run(
                config,
                current_patch,
                disambiguate_flakes=disambiguate_flakes,
            )

            if last_result.verdict == PatchVerdict.ACCEPTED:
                return HealResult(
                    success=True,
                    iterations=iteration,
                    final_patch=current_patch,
                    last_result=last_result,
                    summary=f"Patch successfully healed in {iteration} iteration(s)",
                )

            # Generate fault localization diagnostics
            can_p: CanonicalPatch | None = None
            try:
                can_p = parse_patch(current_patch)
            except Exception:
                pass

            diagnostic_report = generate_ai_feedback(last_result, patch=can_p)

            # Call agent/repair function
            proposed_patch = repair_agent_fn(diagnostic_report)
            if not proposed_patch:
                return HealResult(
                    success=False,
                    iterations=iteration,
                    final_patch=current_patch,
                    last_result=last_result,
                    summary=(
                        f"Healing aborted at iteration {iteration}: "
                        "repair agent returned no replacement patch"
                    ),
                )

            current_patch = proposed_patch

        assert last_result is not None
        return HealResult(
            success=False,
            iterations=max_iterations,
            final_patch=current_patch,
            last_result=last_result,
            summary=f"Exhausted maximum {max_iterations} iterations without convergence",
        )
