"""Blast Radius Assessment Gate for PatchProof AI Software Factory.

Categorizes incoming patches into:
- Lineage A (Contained / Level 4): Low risk, verified autonomously.
- Lineage B (Breaking / Level 3): High risk, executed in ephemeral sandbox with human operator gate.
"""

from __future__ import annotations

from pathlib import PurePosixPath

from patchproof.core.normalizer import parse_patch
from patchproof.factory.models import BlastRadiusReport, FactoryLineage, RiskLevel
from patchproof.models.patch import FileDelta

CORE_SENSITIVE_PREFIXES: tuple[str, ...] = (
    "src/patchproof/core/",
    "src/patchproof/sandbox/",
    ".github/workflows/",
)

CRITICAL_ROOT_FILES: tuple[str, ...] = (
    "pyproject.toml",
    "AGENTS.md",
    "CLAUDE.md",
    "dockerfile",
    "docker-compose.yml",
)


class BlastRadiusClassifier:
    """Evaluates patch diffs to compute blast radius and route to Lineage A or B."""

    def __init__(
        self,
        sensitive_prefixes: tuple[str, ...] = CORE_SENSITIVE_PREFIXES,
        critical_files: tuple[str, ...] = CRITICAL_ROOT_FILES,
    ) -> None:
        self.sensitive_prefixes = sensitive_prefixes
        self.critical_files = tuple(f.lower() for f in critical_files)

    def assess_patch(self, patch_str: str) -> BlastRadiusReport:
        """Parse unified diff and assess blast radius risk."""
        if not patch_str.strip():
            return BlastRadiusReport(
                risk_level=RiskLevel.LOW,
                lineage=FactoryLineage.LINEAGE_A,
                is_contained=True,
                touched_files=(),
                core_files_touched=(),
                rationale="Empty patch contains no modifications.",
            )

        canonical = parse_patch(patch_str)
        return self.assess_deltas(canonical.file_deltas)

    def assess_deltas(self, deltas: tuple[FileDelta, ...]) -> BlastRadiusReport:
        """Assess parsed file deltas against security boundaries."""
        touched: list[str] = []
        core_touched: list[str] = []
        reasons: list[str] = []

        for delta in deltas:
            normalized_path = PurePosixPath(delta.path.replace("\\", "/")).as_posix()
            touched.append(normalized_path)
            basename = PurePosixPath(normalized_path).name.lower()

            # 1. Critical governance or configuration files
            if basename in self.critical_files or normalized_path.lower() in self.critical_files:
                core_touched.append(normalized_path)
                reasons.append(
                    f"Modifies critical configuration/governance file: {normalized_path}"
                )
                continue

            # 2. Check if file is in sensitive core path
            is_sensitive_path = any(
                normalized_path.startswith(prefix) for prefix in self.sensitive_prefixes
            )

            if is_sensitive_path:
                # Inspect if modifications are purely comments/whitespace
                if not self._is_comment_only_delta(delta):
                    core_touched.append(normalized_path)
                    reasons.append(f"Modifies executable core code: {normalized_path}")

        if core_touched:
            return BlastRadiusReport(
                risk_level=RiskLevel.HIGH,
                lineage=FactoryLineage.LINEAGE_B,
                is_contained=False,
                touched_files=tuple(touched),
                core_files_touched=tuple(core_touched),
                rationale="; ".join(reasons),
                confidence_score=0.95,
            )

        return BlastRadiusReport(
            risk_level=RiskLevel.LOW,
            lineage=FactoryLineage.LINEAGE_A,
            is_contained=True,
            touched_files=tuple(touched),
            core_files_touched=(),
            rationale=(
                "All modifications are contained within safe documentation or non-core boundaries."
            ),
            confidence_score=0.98,
        )

    def _is_comment_only_delta(self, delta: FileDelta) -> bool:
        """Check if delta contains only comments or blank lines."""
        for hunk in delta.hunks:
            for line in hunk.lines:
                if line.startswith(("+", "-")) and not line.startswith(("+++", "---")):
                    content = line[1:].strip()
                    if content and not content.startswith("#"):
                        return False
        return True
