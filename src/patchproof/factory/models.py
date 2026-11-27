"""Data models for PatchProof AI Software Factory."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class RiskLevel(StrEnum):
    """Blast radius risk classification."""

    LOW = "low"
    HIGH = "high"


class FactoryLineage(StrEnum):
    """Lineage routing through the AI Software Factory."""

    LINEAGE_A = "lineage_a"  # Level 4: Contained, autonomous ship to production
    LINEAGE_B = "lineage_b"  # Level 3: Breaking, ephemeral sandbox with operator gate


class HybridLoopState(StrEnum):
    """Lifecycle state of an iteration within the factory hybrid loop."""

    INIT = "init"
    EVALUATING = "evaluating"
    RETRYING = "retrying"
    PASSED = "passed"
    FAILED = "failed"
    EXCEPTION_QUEUED = "exception_queued"


class BlastRadiusReport(BaseModel):
    """Assessment report determining blast radius and lineage routing."""

    model_config = ConfigDict(frozen=True)

    risk_level: RiskLevel = Field(description="Calculated risk severity")
    lineage: FactoryLineage = Field(description="Assigned factory lineage")
    is_contained: bool = Field(description="True if change cannot compromise boundaries")
    touched_files: tuple[str, ...] = Field(
        default_factory=tuple, description="All altered file paths"
    )
    core_files_touched: tuple[str, ...] = Field(
        default_factory=tuple, description="Sensitive core files altered"
    )
    rationale: str = Field(description="Explanation of routing decision")
    confidence_score: float = Field(default=1.0, description="Confidence in assessment")


class FactoryRunResult(BaseModel):
    """Outcome of an end-to-end factory lineage execution."""

    model_config = ConfigDict(frozen=True)

    run_id: str = Field(description="Unique run execution identifier")
    lineage: FactoryLineage = Field(description="Executed lineage")
    status: HybridLoopState = Field(description="Final terminal state")
    iterations: int = Field(description="Number of feedback loop attempts")
    blast_radius: BlastRadiusReport = Field(description="Input blast radius assessment")
    provenance_signature: str | None = Field(
        default=None, description="Ed25519 signature of verified patch"
    )
    provenance_key: str | None = Field(default=None, description="Ed25519 public key")
    diagnostic_message: str = Field(
        default="", description="Detailed error diagnostics if failed"
    )
    shipped_to_production: bool = Field(
        default=False, description="True if autonomously merged/deployed"
    )
    operator_approval_required: bool = Field(
        default=False, description="True if awaiting human sign-off"
    )
