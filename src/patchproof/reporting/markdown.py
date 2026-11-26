"""GitHub PR comment and Step Summary Markdown generator.

Formats deterministic comparative execution results into rich, GitHub-native proof cards
including verdict badges, Anti-Cheat integrity audits, and cryptographic attestations.
"""

from __future__ import annotations

from patchproof.models.sandbox import (
    ComparativeExecutionResult,
    PatchVerdict,
    RegressionClassification,
)
from patchproof.reporting.certificate import ProofCertificate

MARKER_COMMENT = "<!-- patchproof-attestation-marker -->"


def generate_markdown_summary(
    result: ComparativeExecutionResult,
    cert: ProofCertificate | None = None,
    patch_content: str | None = None,
) -> str:
    """Generate GitHub Flavored Markdown summary card for PR comments and CI summaries."""
    lines: list[str] = [MARKER_COMMENT]

    # 1. Header with Status Badge
    verdict_str = result.verdict.value.upper()
    if result.verdict == PatchVerdict.ACCEPTED:
        badge_url = "https://img.shields.io/badge/PatchProof-VERIFIED-brightgreen?style=flat-square&logo=shield"
        header_icon = "🛡️"
    elif result.verdict == PatchVerdict.SUSPICIOUS:
        badge_url = "https://img.shields.io/badge/PatchProof-SUSPICIOUS-yellow?style=flat-square&logo=shield"
        header_icon = "⚠️"
    elif result.verdict == PatchVerdict.REJECTED:
        badge_url = "https://img.shields.io/badge/PatchProof-REJECTED-red?style=flat-square&logo=shield"
        header_icon = "❌"
    else:
        badge_url = "https://img.shields.io/badge/PatchProof-INCONCLUSIVE-lightgrey?style=flat-square"
        header_icon = "❓"

    lines.append(f"## {header_icon} PatchProof Verification Report")
    lines.append(f"[![Verdict: {verdict_str}]({badge_url})](https://github.com/ArnavSingha/Patchproof)\n")
    lines.append(f"> **Summary:** {result.summary}\n")

    # 2. Key Metrics Table
    clean_count = sum(
        1
        for t in result.test_comparisons
        if t.classification == RegressionClassification.CLEAN_PRESERVATION
    )

    anti_cheat_status = (
        "🚨 **TAMPERING DETECTED**"
        if result.test_tampering_detected
        else "✅ **PASSED** (Untampered)"
    )

    lines.append("### Verification Metrics")
    lines.append("| Metric | Measurement | Status |")
    lines.append("| :--- | :--- | :--- |")
    reg_icon = "❌" if result.introduced_regressions_count > 0 else "✅"
    tamper_icon = "🚨" if result.test_tampering_detected else "✅"
    lines.append(f"| **Verdict** | `{verdict_str}` | {header_icon} |")
    lines.append(f"| **Preserved Tests** | `{clean_count}` test(s) preserved clean | ✅ |")
    lines.append(
        f"| **Resolved Defects** | `{result.resolved_defects_count}` bug(s) resolved | 🎯 |"
    )
    lines.append(
        f"| **Introduced Regressions** | `{result.introduced_regressions_count}` regression(s) | "
        f"{reg_icon} |"
    )
    lines.append(f"| **Anti-Cheat Integrity** | {anti_cheat_status} | {tamper_icon} |")
    removed_cnt = int(getattr(result, "removed_tests_count", 0) or 0)
    suppressed_cnt = int(getattr(result, "suppressed_tests_count", 0) or 0)
    lines.append(
        f"| **Removed / Skipped Tests** | `{removed_cnt}` removed, `{suppressed_cnt}` suppressed | "
        f"{'⚠️' if (removed_cnt + suppressed_cnt) > 0 else '✅'} |"
    )
    lines.append("| **Isolation Sandbox** | Docker (`--network none`, read-only root) | 🔒 |")

    if cert is not None:
        lines.append(f"| **Proof Attestation** | HMAC-SHA256: `{cert.signature[:16]}...` | 📜 |")

    lines.append("")

    # 3. Anti-Cheat Violations Section (if any)
    if result.test_tampering_detected and result.tampering_details:
        lines.append("<details open><summary>🚨 <b>Anti-Cheat Tampering Violations</b></summary>\n")
        lines.append(
            "> The candidate patch modified existing tests in a manner that weakens "
            "regression guarantees:\n"
        )
        for detail in result.tampering_details:
            lines.append(f"- ⚠️ **[VIOLATION]** {detail}")
        lines.append("</details>\n")

    # 4. Critical Test Transitions Section
    critical_transitions = [
        tc
        for tc in result.test_comparisons
        if tc.classification
        in (
            RegressionClassification.INTRODUCED_REGRESSION,
            RegressionClassification.RESOLVED_DEFECT,
            RegressionClassification.SUPPRESSION_ALERT,
            RegressionClassification.REMOVED_TEST,
        )
    ]

    if critical_transitions:
        lines.append("<details open><summary>🔍 <b>Critical Test Transitions</b></summary>\n")
        lines.append("| Classification | Test Identifier | Shift | Message |")
        lines.append("| :--- | :--- | :--- | :--- |")
        for tc in critical_transitions:
            b_val = tc.baseline_outcome.value.upper() if tc.baseline_outcome else "NONE"
            p_val = tc.patched_outcome.value.upper() if tc.patched_outcome else "NONE"
            msg = tc.message or "-"
            lines.append(
                f"| `{tc.classification.value}` | `{tc.test_name}` | `{b_val} -> {p_val}` | {msg} |"
            )
        lines.append("</details>\n")

    # 5. Diagnostic Output (Stderr) on failure
    err_text = result.patched_result.stderr.strip()
    if err_text and result.verdict in (PatchVerdict.REJECTED, PatchVerdict.INFRASTRUCTURE_FAILURE):
        lines.append("<details><summary>📋 <b>Test Execution Diagnostics (Stderr)</b></summary>\n")
        lines.append("```text")
        for err_line in err_text.splitlines()[:15]:
            lines.append(err_line)
        if len(err_text.splitlines()) > 15:
            lines.append("... [truncated in summary, see artifact or full log]")
        lines.append("```")
        lines.append("</details>\n")

    # 6. Footer
    lines.append("---")
    lines.append(
        "*Evaluated deterministically by [PatchProof](https://github.com/ArnavSingha/Patchproof) "
        "in zero-network container isolation.*"
    )

    return "\n".join(lines) + "\n"
