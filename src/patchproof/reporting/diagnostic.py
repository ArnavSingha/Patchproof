"""Fault Localization and AI Self-Healing Diagnostic Engine.

Provides spectrum-based fault localization (SBFL), hunk correlation, traceback condensation,
and token-optimized feedback prompt generation for AI coding agents (Aider, Cursor, Claude Code).
"""

from __future__ import annotations

import math
import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from patchproof.models.patch import CanonicalPatch
from patchproof.models.sandbox import (
    ComparativeExecutionResult,
    PatchVerdict,
    RegressionClassification,
)

_TB_FRAME_RE = re.compile(
    r'File\s+"(?P<file>[^"]+)",\s+line\s+(?P<line>\d+)(?:,\s+in\s+(?P<func>[^\n]+))?(?:\n\s+(?P<code>.+))?'
)


@dataclass(frozen=True)
class TracebackFrame:
    """Represents a single execution frame parsed from a Python traceback."""

    file_path: str
    line_number: int
    func_name: str
    code_line: str


@dataclass(frozen=True)
class HunkSuspicion:
    """Represents a patch hunk correlated with a failure, accompanied by a suspicion score."""

    file_path: str
    hunk_index: int
    suspicion_score: float
    matched_lines: tuple[int, ...]
    reason: str


@dataclass(frozen=True)
class RegressionDetail:
    """Detailed fault diagnostic for an introduced regression."""

    test_id: str
    failure_message: str
    condensed_traceback: str
    suspected_hunks: tuple[HunkSuspicion, ...]


@dataclass(frozen=True)
class AISelfHealingReport:
    """Structured report and token-optimized feedback prompt for AI self-correction."""

    verdict: str
    summary: str
    regressions: tuple[RegressionDetail, ...]
    preserved_tests_count: int
    hunk_suspicions: tuple[HunkSuspicion, ...]
    prompt_feedback: str

    def to_dict(self) -> dict[str, Any]:
        """Convert diagnostic report to JSON-serializable dictionary."""
        return {
            "verdict": self.verdict,
            "summary": self.summary,
            "preserved_tests_count": self.preserved_tests_count,
            "regressions_count": len(self.regressions),
            "hunk_suspicions": [
                {
                    "file_path": s.file_path,
                    "hunk_index": s.hunk_index,
                    "suspicion_score": round(s.suspicion_score, 3),
                    "matched_lines": list(s.matched_lines),
                    "reason": s.reason,
                }
                for s in self.hunk_suspicions
            ],
            "prompt_feedback": self.prompt_feedback,
        }


def extract_traceback_frames(traceback_text: str) -> list[TracebackFrame]:
    """Parse stack frames out of a standard Python traceback string."""
    frames: list[TracebackFrame] = []
    lines = traceback_text.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]
        match = re.search(
            r'File\s+"(?P<file>[^"]+)",\s+line\s+(?P<line>\d+)(?:,\s+in\s+(?P<func>[^\n]+))?',
            line,
        )
        if match:
            file_path = match.group("file")
            line_no = int(match.group("line"))
            func_name = (match.group("func") or "unknown").strip()
            code_line = ""
            if i + 1 < len(lines) and lines[i + 1].startswith("    "):
                code_line = lines[i + 1].strip()
                i += 1
            frames.append(
                TracebackFrame(
                    file_path=file_path,
                    line_number=line_no,
                    func_name=func_name,
                    code_line=code_line,
                )
            )
        i += 1
    return frames


def _normalize_path(path: str) -> str:
    """Normalize file paths for cross-platform comparison."""
    return path.replace("\\", "/").strip().lower()


def correlate_hunks_with_traceback(
    patch: CanonicalPatch,
    frames: Sequence[TracebackFrame],
    total_failed: int = 1,
    total_passed: int = 1,
) -> list[HunkSuspicion]:
    """Correlate traceback frames against patch hunks using Ochiai-inspired suspicion scoring."""
    suspicions: list[HunkSuspicion] = []

    for delta in patch.file_deltas:
        normalized_delta_path = _normalize_path(delta.path)
        for h_idx, hunk in enumerate(delta.hunks):
            # Calculate modified new line range [new_start, new_start + new_lines]
            hunk_start = hunk.new_start
            hunk_end = hunk.new_start + max(1, hunk.new_lines)

            matched_lines: list[int] = []
            matched_funcs: list[str] = []

            for frame in frames:
                norm_frame_path = _normalize_path(frame.file_path)
                # Check path suffix or equality
                if norm_frame_path.endswith(
                    normalized_delta_path
                ) or normalized_delta_path.endswith(norm_frame_path):
                    # Check if frame line falls in or immediately adjacent (+/- 2 lines) to hunk
                    if hunk_start - 2 <= frame.line_number <= hunk_end + 2:
                        matched_lines.append(frame.line_number)
                        matched_funcs.append(frame.func_name)

            if matched_lines:
                # Ochiai Suspicion: failed / sqrt(total_failed * (failed + passed))
                # For direct traceback hit, failed=1, passed=0 gives score 1.0 / sqrt(total_failed)
                denom = math.sqrt(max(1, total_failed) * max(1, total_failed))
                score = min(1.0, 1.0 / denom if denom > 0 else 0.95)
                # Boost if exact line matched
                if any(hunk_start <= line <= hunk_end for line in matched_lines):
                    score = max(score, 0.92)

                unique_lines = tuple(sorted(set(matched_lines)))
                suspicions.append(
                    HunkSuspicion(
                        file_path=delta.path,
                        hunk_index=h_idx,
                        suspicion_score=score,
                        matched_lines=unique_lines,
                        reason=(
                            f"Frame execution in {delta.path} at line(s) {unique_lines} "
                            f"within hunk {hunk.header}"
                        ),
                    )
                )

    suspicions.sort(key=lambda s: s.suspicion_score, reverse=True)
    return suspicions


def _condense_traceback(raw_tb: str, max_lines: int = 15) -> str:
    """Condense raw traceback to essential user frames and error message."""
    lines = [line for line in raw_tb.splitlines() if line.strip()]
    if len(lines) <= max_lines:
        return "\n".join(lines)

    # Keep the header, the last few frames, and the final exception
    header = lines[:2]
    tail = lines[-(max_lines - 2) :]
    return "\n".join([*header, "  ... [intermediate frames omitted] ...", *tail])


def generate_ai_feedback(
    result: ComparativeExecutionResult,
    patch: CanonicalPatch | None = None,
    max_traceback_lines: int = 15,
) -> AISelfHealingReport:
    """Generate high-density, token-optimized feedback prompt for AI self-correction."""
    verdict_str = result.verdict.value.upper()
    preserved_count = sum(
        1
        for c in result.test_comparisons
        if c.classification == RegressionClassification.CLEAN_PRESERVATION
    )

    if result.verdict == PatchVerdict.ACCEPTED:
        prompt = (
            "================================================================================\n"
            "PATCHPROOF VERIFICATION PASSED (VERDICT: ACCEPTED)\n"
            "================================================================================\n"
            f"All {preserved_count} test(s) preserved cleanly with 0 regressions.\n"
            "No corrective action required.\n"
            "================================================================================"
        )
        return AISelfHealingReport(
            verdict="ACCEPTED",
            summary=result.summary,
            regressions=(),
            preserved_tests_count=preserved_count,
            hunk_suspicions=(),
            prompt_feedback=prompt,
        )

    # Gather regression details
    regressions: list[RegressionDetail] = []
    all_suspicions: list[HunkSuspicion] = []

    # Map patched test cases for failure messages
    patched_test_map = (
        result.patched_result.test_report.test_map if result.patched_result.test_report else {}
    )

    regressed_comparisons = [
        c
        for c in result.test_comparisons
        if c.classification == RegressionClassification.INTRODUCED_REGRESSION
    ]

    for comp in regressed_comparisons:
        test_case = patched_test_map.get(comp.test_name)
        raw_msg = (
            test_case.failure_message
            if test_case and test_case.failure_message
            else comp.message or "Test failed without captured traceback"
        )

        condensed_tb = _condense_traceback(raw_msg, max_lines=max_traceback_lines)
        frames = extract_traceback_frames(raw_msg)

        hunk_suspicions: list[HunkSuspicion] = []
        if patch:
            hunk_suspicions = correlate_hunks_with_traceback(
                patch,
                frames,
                total_failed=len(regressed_comparisons),
                total_passed=preserved_count,
            )
            all_suspicions.extend(hunk_suspicions)

        regressions.append(
            RegressionDetail(
                test_id=comp.test_name,
                failure_message=raw_msg.splitlines()[-1] if raw_msg else "Unknown failure",
                condensed_traceback=condensed_tb,
                suspected_hunks=tuple(hunk_suspicions),
            )
        )

    # Deduplicate suspicions
    unique_suspicions_map: dict[tuple[str, int], HunkSuspicion] = {}
    for s in all_suspicions:
        key = (s.file_path, s.hunk_index)
        if (
            key not in unique_suspicions_map
            or s.suspicion_score > unique_suspicions_map[key].suspicion_score
        ):
            unique_suspicions_map[key] = s
    sorted_suspicions = tuple(
        sorted(unique_suspicions_map.values(), key=lambda s: s.suspicion_score, reverse=True)
    )

    # Build token-budgeted prompt
    buf: list[str] = [
        "================================================================================",
        f"PATCHPROOF VERIFICATION FAILED (VERDICT: {verdict_str})",
        "================================================================================",
        (
            f"Your proposed changes introduced {len(regressions)} "
            "regression(s) against the test suite.\n"
        ),
    ]

    if sorted_suspicions:
        buf.append("FAULT LOCALIZATION (Suspicious Hunks):")
        for s in sorted_suspicions[:3]:
            score_pct = int(s.suspicion_score * 100)
            buf.append(
                f"- [{score_pct}% Suspicion] {s.file_path} (Hunk #{s.hunk_index + 1}) "
                f"Line(s) {list(s.matched_lines)}: {s.reason}"
            )
        buf.append("")

    buf.append("REGRESSION DETAILS:")
    for idx, reg in enumerate(regressions[:5], start=1):
        buf.append(f"[{idx}/{len(regressions)}] Test: {reg.test_id}")
        buf.append(f"- Failure: {reg.failure_message}")
        buf.append("```traceback")
        buf.append(reg.condensed_traceback)
        buf.append("```")
        buf.append("")

    buf.append("CRITICAL INVARIANTS:")
    buf.append(
        f"1. Fix the above regression(s) without breaking the "
        f"{preserved_count} existing test(s) that passed."
    )
    buf.append("2. Do not modify or suppress existing test files to make tests pass.")
    buf.append("3. Provide your corrected patch preserving all baseline behaviors.")
    buf.append("================================================================================")

    return AISelfHealingReport(
        verdict=verdict_str,
        summary=result.summary,
        regressions=tuple(regressions),
        preserved_tests_count=preserved_count,
        hunk_suspicions=sorted_suspicions,
        prompt_feedback="\n".join(buf),
    )
