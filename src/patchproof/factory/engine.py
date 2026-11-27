"""The Hybrid Loop Engine for the PatchProof AI Software Factory.

Implements the candidate -> gate -> feedback -> retry execution cycle
with cryptographic Ed25519 attestation and blast-radius lineage routing.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable

from pydantic import BaseModel, ConfigDict, Field

from patchproof.factory.blast_radius import BlastRadiusClassifier
from patchproof.factory.models import (
    BlastRadiusReport,
    FactoryLineage,
    FactoryRunResult,
    HybridLoopState,
    RiskLevel,
)
from patchproof.reporting.asymmetric import generate_ed25519_keypair, sign_payload_ed25519


class GateResult(BaseModel):
    """Result of deterministic verification gate."""

    model_config = ConfigDict(frozen=True)

    passed: bool = Field(description="True if all deterministic gates passed")
    diagnostics: str = Field(default="", description="Failure reasons or error traces")


class FactoryEngine:
    """Orchestrates candidate generation, deterministic gating, and lineage routing."""

    def __init__(
        self,
        max_retries: int = 3,
        classifier: BlastRadiusClassifier | None = None,
    ) -> None:
        self.max_retries = max(1, max_retries)
        self.classifier = classifier or BlastRadiusClassifier()

    def run(
        self,
        run_id: str,
        generator_fn: Callable[[str | None], str],
        gate_fn: Callable[[str], GateResult],
    ) -> FactoryRunResult:
        """Execute the hybrid loop from signal to closed lineage."""
        attempt = 0
        last_feedback: str | None = None
        last_report: BlastRadiusReport | None = None

        while attempt < self.max_retries:
            attempt += 1
            patch = generator_fn(last_feedback)
            report = self.classifier.assess_patch(patch)
            last_report = report

            gate_result = gate_fn(patch)
            if not gate_result.passed:
                last_feedback = gate_result.diagnostics
                continue

            # Deterministic gate passed -> Generate cryptographic Ed25519 provenance
            priv_pem, pub_pem = generate_ed25519_keypair()
            patch_digest = hashlib.sha256(patch.encode("utf-8")).hexdigest()
            provenance_payload = (
                f"run_id:{run_id};lineage:{report.lineage};digest:{patch_digest}".encode()
            )
            signature = sign_payload_ed25519(provenance_payload, priv_pem)

            if report.lineage == FactoryLineage.LINEAGE_A:
                # Level 4: Contained -> Auto-ships to production
                return FactoryRunResult(
                    run_id=run_id,
                    lineage=FactoryLineage.LINEAGE_A,
                    status=HybridLoopState.PASSED,
                    iterations=attempt,
                    blast_radius=report,
                    provenance_signature=signature,
                    provenance_key=pub_pem,
                    shipped_to_production=True,
                    operator_approval_required=False,
                )

            # Level 3: Breaking -> Ephemeral environment passed, awaiting operator gate
            return FactoryRunResult(
                run_id=run_id,
                lineage=FactoryLineage.LINEAGE_B,
                status=HybridLoopState.PASSED,
                iterations=attempt,
                blast_radius=report,
                provenance_signature=signature,
                provenance_key=pub_pem,
                shipped_to_production=False,
                operator_approval_required=True,
            )

        # Max retries exceeded -> Route to human exception queue
        fallback_report = last_report or BlastRadiusReport(
            risk_level=RiskLevel.HIGH,
            lineage=FactoryLineage.LINEAGE_B,
            is_contained=False,
            rationale="Run exceeded maximum retry iterations without passing verification.",
        )

        return FactoryRunResult(
            run_id=run_id,
            lineage=fallback_report.lineage,
            status=HybridLoopState.EXCEPTION_QUEUED,
            iterations=attempt,
            blast_radius=fallback_report,
            diagnostic_message=last_feedback or "Max retries exceeded",
            shipped_to_production=False,
            operator_approval_required=False,
        )

    def operator_approve(self, result: FactoryRunResult) -> FactoryRunResult:
        """Human operator approves a Lineage B run for production shipment."""
        return FactoryRunResult(
            run_id=result.run_id,
            lineage=result.lineage,
            status=result.status,
            iterations=result.iterations,
            blast_radius=result.blast_radius,
            provenance_signature=result.provenance_signature,
            provenance_key=result.provenance_key,
            diagnostic_message=result.diagnostic_message,
            shipped_to_production=True,
            operator_approval_required=False,
        )
