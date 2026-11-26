"""Standalone, zero-dependency visual HTML evidence report generator.

Produces an interactive, self-contained HTML dashboard with metrics, test transition
matrices, diagnostic tracebacks, and syntax-highlighted diffs.
"""

from __future__ import annotations

import html
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from patchproof.models.sandbox import ComparativeExecutionResult

VERDICT_COLORS = {
    "accepted": "#10b981",  # emerald green
    "rejected": "#ef4444",  # rose red
    "suspicious": "#f59e0b",  # amber
    "inconclusive": "#64748b",  # slate
    "infrastructure_failure": "#dc2626",  # dark red
}

CLASSIFICATION_STYLES = {
    "clean_preservation": ("#10b981", "Preserved (Pass)"),
    "introduced_regression": ("#ef4444", "Regression"),
    "resolved_defect": ("#3b82f6", "Fixed Defect"),
    "pre_existing_failure": ("#64748b", "Pre-existing Fail"),
    "suppression_alert": ("#f59e0b", "Suppressed"),
    "pre_existing_skip": ("#475569", "Pre-existing Skip"),
    "new_test_passing": ("#06b6d4", "New Test (Pass)"),
    "new_test_failing": ("#f43f5e", "New Test (Fail)"),
    "new_test_skipped": ("#64748b", "New Test (Skip)"),
    "removed_test": ("#94a3b8", "Removed Test"),
}

CSS_STYLES = """
:root {
  --bg-color: #0f172a;
  --surface-color: #1e293b;
  --surface-hover: #334155;
  --border-color: #334155;
  --text-primary: #f8fafc;
  --text-secondary: #94a3b8;
  --text-muted: #64748b;
  --accent-blue: #38bdf8;
  --status-green: #10b981;
  --status-red: #ef4444;
  --status-amber: #f59e0b;
  --code-bg: #0b1120;
}
* { box-sizing: border-box; margin: 0; padding: 0; }
body {
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
  background-color: var(--bg-color);
  color: var(--text-primary);
  line-height: 1.5;
  padding: 2rem 1rem;
}
.container {
  max-width: 1200px;
  margin: 0 auto;
}
.header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 2rem;
  padding-bottom: 1.5rem;
  border-bottom: 1px solid var(--border-color);
}
.header-title h1 {
  font-size: 1.75rem;
  font-weight: 700;
  display: flex;
  align-items: center;
  gap: 0.75rem;
}
.header-title p {
  color: var(--text-secondary);
  font-size: 0.95rem;
  margin-top: 0.25rem;
}
.badge-verdict {
  font-size: 1.1rem;
  font-weight: 800;
  padding: 0.5rem 1.25rem;
  border-radius: 9999px;
  text-transform: uppercase;
  letter-spacing: 0.05em;
  display: inline-block;
}
.status-accepted {
  background-color: rgba(16, 185, 129, 0.15);
  color: #34d399;
  border: 1px solid #10b981;
}
.status-rejected {
  background-color: rgba(239, 68, 68, 0.15);
  color: #f87171;
  border: 1px solid #ef4444;
}
.status-suspicious {
  background-color: rgba(245, 158, 11, 0.15);
  color: #fbbf24;
  border: 1px solid #f59e0b;
}
.status-inconclusive {
  background-color: rgba(100, 116, 139, 0.15);
  color: #94a3b8;
  border: 1px solid #64748b;
}
.status-infrastructure_failure {
  background-color: rgba(220, 38, 38, 0.2);
  color: #f87171;
  border: 1px solid #dc2626;
}

.grid-metrics {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
  gap: 1rem;
  margin-bottom: 2rem;
}
.card-metric {
  background: var(--surface-color);
  border: 1px solid var(--border-color);
  border-radius: 0.75rem;
  padding: 1.25rem;
}
.card-metric .metric-label {
  font-size: 0.85rem;
  font-weight: 600;
  color: var(--text-secondary);
  text-transform: uppercase;
  letter-spacing: 0.05em;
}
.card-metric .metric-value {
  font-size: 1.85rem;
  font-weight: 700;
  margin-top: 0.5rem;
}
.text-danger { color: #f87171; }
.text-success { color: #34d399; }
.text-accent { color: #38bdf8; }

.section {
  background: var(--surface-color);
  border: 1px solid var(--border-color);
  border-radius: 0.75rem;
  padding: 1.5rem;
  margin-bottom: 2rem;
}
.section h2 {
  font-size: 1.25rem;
  font-weight: 700;
  margin-bottom: 1rem;
  display: flex;
  align-items: center;
  gap: 0.5rem;
}
.table-container {
  overflow-x: auto;
}
table {
  width: 100%;
  border-collapse: collapse;
  font-size: 0.9rem;
}
th {
  text-align: left;
  padding: 0.75rem 1rem;
  background-color: rgba(0, 0, 0, 0.2);
  color: var(--text-secondary);
  border-bottom: 1px solid var(--border-color);
  font-weight: 600;
}
td {
  padding: 0.75rem 1rem;
  border-bottom: 1px solid rgba(255, 255, 255, 0.05);
}
tr:hover td {
  background-color: var(--surface-hover);
}
.pill {
  font-size: 0.75rem;
  font-weight: 700;
  padding: 0.25rem 0.65rem;
  border-radius: 9999px;
  display: inline-block;
  text-transform: uppercase;
}
.diff-pre {
  background-color: var(--code-bg);
  border: 1px solid var(--border-color);
  border-radius: 0.5rem;
  padding: 1rem;
  overflow-x: auto;
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
  font-size: 0.85rem;
  line-height: 1.45;
}
.diff-add { color: #34d399; background: rgba(16, 185, 129, 0.1); display: block; }
.diff-sub { color: #f87171; background: rgba(239, 68, 68, 0.1); display: block; }
.diff-hunk { color: #38bdf8; font-weight: bold; display: block; }
.footer {
  text-align: center;
  color: var(--text-muted);
  font-size: 0.85rem;
  margin-top: 3rem;
}
"""


def _render_diff_html(patch_text: str) -> str:
    """Format patch diff with inline line-by-line syntax coloring."""
    lines_html = []
    for line in patch_text.splitlines():
        escaped = html.escape(line)
        if line.startswith("+") and not line.startswith("+++"):
            lines_html.append(f'<span class="diff-add">{escaped}</span>')
        elif line.startswith("-") and not line.startswith("---"):
            lines_html.append(f'<span class="diff-sub">{escaped}</span>')
        elif line.startswith("@@"):
            lines_html.append(f'<span class="diff-hunk">{escaped}</span>')
        else:
            lines_html.append(f"<span>{escaped}</span>")
    return "\n".join(lines_html)


def generate_html_report(
    result: ComparativeExecutionResult,
    patch_content: str | None = None,
) -> str:
    """Generate self-contained, standalone HTML verification report."""
    verdict_str = result.verdict.value.lower()
    badge_class = f"badge-verdict status-{verdict_str}"
    verdict_upper = html.escape(result.verdict.value.upper())
    summary_escaped = html.escape(result.summary)

    # Comparisons rows
    table_rows = []
    for c in result.test_comparisons:
        name_esc = html.escape(c.test_name)
        base_val = c.baseline_outcome.value if c.baseline_outcome else "—"
        patch_val = c.patched_outcome.value if c.patched_outcome else "—"

        color, _label = CLASSIFICATION_STYLES.get(
            c.classification.value, ("#64748b", c.classification.value)
        )
        pill_html = (
            f'<span class="pill" style="background-color: rgba({int(color[1:3], 16)}, '
            f"{int(color[3:5], 16)}, {int(color[5:7], 16)}, 0.15); color: {color}; "
            f'border: 1px solid {color};">{html.escape(c.classification.value)}</span>'
        )

        table_rows.append(
            f"<tr>"
            f'<td style="font-family: monospace;">{name_esc}</td>'
            f'<td style="text-transform: uppercase; font-weight: 600;">{base_val}</td>'
            f'<td style="text-transform: uppercase; font-weight: 600;">{patch_val}</td>'
            f"<td>{pill_html}</td>"
            f"</tr>"
        )

    comparisons_html = (
        "".join(table_rows)
        if table_rows
        else '<tr><td colspan="4" style="text-align: center; color: var(--text-muted);">'
        "No test comparison data available</td></tr>"
    )

    diff_section_html = ""
    if patch_content:
        rendered_diff = _render_diff_html(patch_content)
        diff_section_html = f"""
        <div class="section">
          <h2>Verified Patch Content</h2>
          <pre class="diff-pre"><code>{rendered_diff}</code></pre>
        </div>
        """

    diag_section_html = ""
    err_text = result.patched_result.stderr.strip()
    if err_text:
        diag_section_html = f"""
        <div class="section">
          <h2>Diagnostic Output (Stderr)</h2>
          <pre class="diff-pre" style="color: #f87171;"><code>{html.escape(err_text)}</code></pre>
        </div>
        """

    total_tests = len(result.test_comparisons)
    reg_color = "text-danger" if result.introduced_regressions_count > 0 else ""
    fix_color = "text-success" if result.resolved_defects_count > 0 else ""

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>PatchProof Verification Report - {verdict_upper}</title>
  <style>{CSS_STYLES}</style>
</head>
<body>
  <div class="container">
    <header class="header">
      <div class="header-title">
        <h1>PatchProof Verification Report</h1>
        <p>{summary_escaped}</p>
      </div>
      <div>
        <span class="{badge_class}">{verdict_upper}</span>
      </div>
    </header>

    <div class="grid-metrics">
      <div class="card-metric">
        <div class="metric-label">Regressions</div>
        <div class="metric-value {reg_color}">{result.introduced_regressions_count}</div>
      </div>
      <div class="card-metric">
        <div class="metric-label">Defects Resolved</div>
        <div class="metric-value {fix_color}">{result.resolved_defects_count}</div>
      </div>
      <div class="card-metric">
        <div class="metric-label">Pre-existing Fails</div>
        <div class="metric-value">{result.pre_existing_failures_count}</div>
      </div>
      <div class="card-metric">
        <div class="metric-label">Total Tests Evaluated</div>
        <div class="metric-value text-accent">{total_tests}</div>
      </div>
      <div class="card-metric">
        <div class="metric-label">Execution Duration</div>
        <div class="metric-value">{result.patched_result.duration_seconds:.2f}s</div>
      </div>
    </div>

    <div class="section">
      <h2>Test Transition Analysis</h2>
      <div class="table-container">
        <table>
          <thead>
            <tr>
              <th>Test Case</th>
              <th>Baseline</th>
              <th>Patched</th>
              <th>Classification</th>
            </tr>
          </thead>
          <tbody>
            {comparisons_html}
          </tbody>
        </table>
      </div>
    </div>

    {diff_section_html}
    {diag_section_html}

    <footer class="footer">
      Generated deterministically by PatchProof Isolated Verification Engine
    </footer>
  </div>
</body>
</html>
"""
