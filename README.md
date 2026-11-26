# PatchProof

[![CI](https://github.com/ArnavSingha/Patchproof/actions/workflows/ci.yml/badge.svg)](https://github.com/ArnavSingha/Patchproof/actions/workflows/ci.yml)
[![License: Apache 2.0](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](https://opensource.org/licenses/Apache-2.0)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![Code style: ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json)](https://github.com/astral-sh/ruff)
[![Checked with mypy](https://www.mypy-lang.org/static/mypy_badge.svg)](https://mypy-lang.org/)

**Deterministic Code-Patch Verification & Reliability Engine for AI-Assisted Engineering**

PatchProof is an independent verification subsystem designed to evaluate, test, and validate AI-generated code patches (from Aider, Claude Code, Cursor, Copilot, Windsurf, etc.) using isolated zero-network container sandboxes, AST dependency analysis, Spectrum-Based Fault Localization, flakiness disambiguation, and cryptographic proof-of-patch attestation.

---

## Key Features

- **Anti-Cheat Test Tampering Guard:** Prevents autonomous agents from evading failures by modifying the test suite. Audits AST deltas across test files to detect and reject assertion count reductions, test function deletions, and `@pytest.mark.skip` suppression injections.
- **Isolated Container Sandboxing:** Executes tests in hardened, zero-network Docker containers (`--network none`, `--read-only`, unprivileged UID `10001:10001`, dropped capabilities) with ephemeral staging workspaces and symlink traversal defense.
- **Predictive Test Slicing (`--slice`):** Analyzes patch AST import graphs to isolate and run only impacted tests in **< 5ms** instead of waiting for the full test suite.
- **Spectrum-Based Fault Localization (SBFL):** Correlates failing test tracebacks directly with patch hunks, calculating an **Ochiai suspicion score** and pinpointing the exact lines of code that triggered regressions.
- **Flakiness Disambiguation (`--disambiguate-flakes`):** Re-runs failing tests inside the sandbox across 3 retry attempts to disambiguate non-deterministic timing/race flakes from true deterministic regressions.
- **Autonomous Self-Healing Loop (`patchproof heal`):** Orchestrates multi-turn convergence loops using localized fault diagnostics to automatically repair regressions until the patch is clean.
- **Cryptographic Proof-of-Patch Attestation (`--cert`):** Generates tamper-proof HMAC-SHA256 signed verification certificates (`patchproof.cert.json`) for CI/CD and pull request merge gating.
- **One-Command Agent Onboarding (`patchproof init-agent`):** Automatically configures `.aider.conf.yml`, `.cursor/mcp.json`, `.cursorrules`, and `CLAUDE.md`.
- **Model Context Protocol (MCP) Server:** Built-in JSON-RPC stdio MCP server exposing verification, diagnostics, and certificates directly to AI coding agents.

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

---

## CLI Usage

### 1. Verify Patches in Isolated Sandbox

```bash
# Verify a patch file with predictive slicing and flakiness disambiguation
patchproof verify changes.patch --slice --disambiguate-flakes

# Verify uncommitted Git changes in current repository
patchproof verify --git --slice

# Verify changes introduced by the latest HEAD commit
patchproof verify --head

# Print high-density AI diagnostic prompt if regressions occur
patchproof verify --git --ai-feedback

# Generate a visual HTML report and a cryptographic Proof-of-Patch certificate
patchproof verify changes.patch --html report.html --cert cert.json
```

### 2. Autonomous Patch Self-Healing

```bash
# Automatically verify and repair uncommitted Git regressions
patchproof heal --git --max-iterations 3

# Heal a patch file
patchproof heal changes.patch
```

### 3. Validate Cryptographic Proof Certificates

```bash
# Validate that a Proof-of-Patch certificate is authentic and untampered
patchproof verify-cert cert.json
```

### 4. Setup AI Agent Presets

```bash
# Initialize configuration for all agents (Aider, Cursor, Claude Code)
patchproof init-agent all

# Or initialize a specific agent
patchproof init-agent cursor
patchproof init-agent aider
patchproof init-agent claude
```

### 5. Diagnostics & Runner Setup

```bash
# Check host Docker daemon, Linux container mode, and environment readiness
patchproof doctor

# Automatically remediate repairable environment issues (e.g. build missing runner)
patchproof doctor --fix

# Build standard local Docker runner image
patchproof setup-runner

# Install Git pre-commit verification hook
patchproof install-hooks
```

---

## GitHub Action Integration

Add PatchProof verification, predictive test slicing, and Anti-Cheat PR gating to your repository in 3 lines:

```yaml
name: PatchProof Verification
on: [pull_request, push]

jobs:
  verify:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: ArnavSingha/Patchproof@main
        with:
          slice: true
          anti_cheat: true
          comment_pr: true
```

When triggered on a pull request, PatchProof automatically posts a rich, interactive **Proof Card** comment, updates `$GITHUB_STEP_SUMMARY`, and fails-closed if any test was weakened, deleted, or regressed.

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

Or run `patchproof init-agent cursor` to automatically configure your `.cursor/mcp.json`.

---

## Development & Testing

```bash
# Run unit and integration tests (355+ tests)
pytest

# Code formatting and linting
ruff check .

# Strict static type verification
mypy --strict src/ tests/
```

---

## License

This project is licensed under the Apache License 2.0. See the [LICENSE](LICENSE) file for details.
