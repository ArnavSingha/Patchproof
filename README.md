# PatchProof

**Deterministic Code-Patch Verification & Reliability Engine for AI-Assisted Engineering**

PatchProof is an independent verification subsystem designed to evaluate, test, and validate AI-generated code patches (from Aider, Claude Code, Cursor, Copilot, etc.) using syntax validation, isolated container sandboxes, test-integrity analysis, and regression verification.

---

## Key Features

- **Deterministic Patch Normalizer:** Parses unified diffs and SEARCH/REPLACE blocks into immutable, canonical models (`CanonicalPatch`, `FileDelta`, `PatchHunk`), normalizing line endings and hunk counts.
- **AST Scope & Syntax Validator:** Validates Python AST modifications before execution. Detects broken syntax, structural anomalies, and calculates affected function/class boundaries.
- **Isolated Sandbox Execution:** Executes verification and test suites inside hardened, ephemeral Docker containers with disabled networking (`--network none`), resource caps, and sanitized host environments.
- **Comparative Regression Classifier:** Runs tests against base and patched states to classify regressions (broken existing tests vs. newly introduced test additions).
- **Model Context Protocol (MCP) Server:** Native MCP integration allowing AI agents (Cursor, Claude, Antigravity) to call `verify_patch` directly during code generation.
- **Audit Reporting:** Generates machine-readable JSON summaries and clean HTML visual reports.

---

## Quickstart

### Installation

```bash
pip install patchproof
```

Or install in development mode from source:

```bash
git clone https://github.com/ArnavSingha/Patchproof.git
cd patchproof
pip install -e ".[dev]"
```

### CLI Usage

```bash
# Verify a patch against a repository
patchproof verify --patch changes.patch --repo ./my-project

# Run verification with Docker sandboxing enabled
patchproof verify --patch changes.patch --repo ./my-project --sandbox

# Output results as structured JSON
patchproof verify --patch changes.patch --repo ./my-project --json

# Generate an HTML audit report
patchproof verify --patch changes.patch --repo ./my-project --html-report report.html
```

---

## MCP Server Integration

PatchProof includes a built-in MCP server for AI coding assistants. Configure your MCP client:

```json
{
  "mcpServers": {
    "patchproof": {
      "command": "patchproof",
      "args": ["mcp"]
    }
  }
}
```

---

## Development & Testing

```bash
# Run unit and integration tests
pytest

# Code formatting and linting
ruff check .

# Static type verification
mypy src/ tests/
```

---

## License

This project is licensed under the Apache License 2.0. See the `LICENSE` file for details.
