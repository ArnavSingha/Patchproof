"""TDD tests for dynamic SVG badge generation."""

from __future__ import annotations

from patchproof.models.sandbox import (
    ComparativeExecutionResult,
    ExecutionResult,
    PatchVerdict,
)
from patchproof.reporting.badge import generate_svg_badge


class TestSVGBadge:
    """Verify SVG badge rendering, color coding, and verdict styling."""

    def test_accepted_verdict_generates_green_badge(self) -> None:
        dummy = ExecutionResult(exit_code=0, duration_seconds=1.0)
        res = ComparativeExecutionResult(
            baseline_result=dummy,
            patched_result=dummy,
            verdict=PatchVerdict.ACCEPTED,
            summary="All tests passing cleanly.",
        )
        svg = generate_svg_badge(res)

        assert "<svg" in svg
        assert "</svg>" in svg
        assert "patchproof" in svg
        assert "verified" in svg.lower()
        # Green color hex
        assert "#2ea44f" in svg or "#4c1" in svg or "#34d058" in svg

    def test_rejected_verdict_generates_red_badge(self) -> None:
        dummy = ExecutionResult(exit_code=1, duration_seconds=1.0)
        res = ComparativeExecutionResult(
            baseline_result=dummy,
            patched_result=dummy,
            verdict=PatchVerdict.REJECTED,
            summary="Introduced 2 regression(s).",
        )
        svg = generate_svg_badge(res)

        assert "<svg" in svg
        assert "rejected" in svg.lower() or "failed" in svg.lower()
        # Red color hex
        assert "#e05d44" in svg or "#cb2431" in svg or "#d73a49" in svg

    def test_suspicious_verdict_generates_amber_badge(self) -> None:
        dummy = ExecutionResult(exit_code=0, duration_seconds=1.0)
        res = ComparativeExecutionResult(
            baseline_result=dummy,
            patched_result=dummy,
            verdict=PatchVerdict.SUSPICIOUS,
            summary="Test tampering detected.",
            test_tampering_detected=True,
        )
        svg = generate_svg_badge(res)

        assert "<svg" in svg
        assert "suspicious" in svg.lower() or "tampered" in svg.lower()
        # Amber/orange color
        assert "#dfb317" in svg or "#f66a0a" in svg or "#d97706" in svg
