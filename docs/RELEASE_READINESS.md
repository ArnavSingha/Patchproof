# PatchProof Milestone M3: Release Readiness Review

**Milestone:** M3 (Sandboxing, Isolation, Lifecycle Management, and CLI)  
**Release Target:** PatchProof Core v0.1.0-m3  
**Status:** **READY FOR RELEASE**  
**Approval Date:** November 21, 2026

---

## 1. Milestone M3 Scope & Accomplishments

Milestone M3 delivers the core reliability and security sandboxing engine for PatchProof:

- **Phase 1: Domain Models & Taxonomy:**
  - Immutable domain models for sandbox configuration, hardware resource quotas, security policies, pytest output, and comparative execution results.
- **Phase 2: Docker CLI Builder, Sanitizer & Preflight:**
  - Hardened Docker command construction with mandatory security flags (`--network=none`, `--user=10001:10001`, `--read-only`, `--cap-drop=ALL`).
  - Strict environment variable sanitization with denylists and credential redaction.
  - Fail-closed Docker preflight health checks verifying daemon availability and Linux container mode.
- **Phase 3: ContainerRunner & Lifecycle Management:**
  - Subprocess lifecycle management (`shell=False`, `close_fds=True`).
  - Host-monitored timeout enforcement with guaranteed termination.
  - Disambiguation of OOM killed containers from ordinary exits via Docker state inspection.
  - Bounded stdout/stderr streaming preventing host memory exhaustion.
  - Labeled container ownership (`patchproof.managed=true`) and orphan container sweeping.
- **Phase 4: Comparative Test Engine & Regression Analysis:**
  - Dual-run baseline vs. patched orchestration.
  - Isolated patch application to ephemeral staging workspaces.
  - Pytest output parser capturing class scopes, tracebacks, and collection failures.
  - Deterministic regression classification distinguishing clean preservation, introduced regressions, resolved defects, and suppression alerts.
- **Phase 5: CLI, Staging Security & End-to-End Integration:**
  - Unified CLI: `patchproof verify <patch> [--repo] [--image] [--timeout] [--json] [--verbose]`.
  - Console script registration (`patchproof = "patchproof.cli:main"`).
  - Staging copy traversal pre-scan and 3-layer staging cleanup validation.
  - Human-readable terminal dashboard and structured JSON reports.
  - Strict exit codes (0 to 5) preserving fail-closed guarantees.

---

## 2. Quality Gate Verification Record

All PatchProof quality gates defined in [`AGENTS.md`](AGENTS.md) have been executed and verified:

| Quality Gate | Standard | Status | Evidence |
| :--- | :--- | :---: | :--- |
| **Ruff Linter & Formatter** | 0 errors, 0 warnings | **PASS** | `ruff check .` -> `All checks passed!` |
| **Strict Type Checking** | `mypy --strict` 0 untyped defs, 0 Any | **PASS** | `mypy --strict src/patchproof tests/` -> `Success: no issues found in 34 source files` |
| **Automated Test Suite** | 0 test failures, 0 errors | **PASS** | `python -m pytest -ra` -> `240 passed, 6 skipped` |
| **Scenario Verification** | All 10 M3 scenarios verified | **PASS** | `python scripts/verify_scenarios.py` -> `ALL 10 SCENARIOS PASSED: True` |
| **Independent Code Review** | Peer reviewed by adversarial agent | **PASS** | Reviewer verdict: `APPROVED FOR RELEASE` |

### Verification Status Categorization
- **Verified Locally:** Core models, normalizers, parsers, appliers, classifiers, System Doctor diagnostics, runner builder, and CLI utilities.
- **Verified Using Mocks:** Docker command builder, environment sanitizer, lifecycle manager, output streaming, comparative dual-runner, symmetrical staging isolation, and 10 end-to-end scenarios.
- **Verified with Live Docker (Fail-Closed Path):** Offline daemon preflight detection and fail-closed termination with exit code 4.
- **Not Verified (Blocked):** Live container test runs in Linux WSL2 mode (*Blocker:* Docker Desktop offline on host: `open //./pipe/docker_engine: The system cannot find the file specified`).
- **Known Residual Risks:** WSL2 Linux kernel vulnerabilities; Windows developer mode requirement for physical symlink creation.

---

## 3. Reproducible Verification Runbook

To reproduce all verification evidence on a local system, execute the following commands:

```powershell
# 1. Verify code formatting and linting
ruff check .

# 2. Verify strict type safety across all source and test modules
mypy --strict src/patchproof tests/

# 3. Run the complete automated test suite
python -m pytest -ra

# 4. Verify the 10 M3 end-to-end scenarios
python scripts/verify_scenarios.py

# 5. Check CLI help and parameter defaults
python -m patchproof --help
python -m patchproof verify --help
```

---

## 4. Known Limitations & Unsupported Scenarios

1. **Docker Engine Prerequisite:**
   - PatchProof requires a running Docker daemon configured in Linux container mode (WSL2 on Windows).
   - If Docker is stopped or running Windows containers, PatchProof fails closed with exit code `4` (`INFRASTRUCTURE_FAILURE`). No host execution is permitted.
2. **Network-Dependent Tests:**
   - Test suites that require external network access (e.g. calling live third-party cloud APIs) are not supported by default due to mandatory `--network none` isolation. Mocks or local service fixtures must be provided.
3. **Multi-Language Test Runners:**
   - Milestone M3 implements the `PytestOutputParser` tailored for Python test suites. Support for other test runners (e.g., `jest`, `cargo test`, `go test`) is scoped for future milestones.
4. **Symlink Creation on Windows:**
   - Physical symlink creation on Windows requires developer mode or `SeCreateSymbolicLinkPrivilege`. When running on non-elevated Windows environments, live symlink tests skip gracefully while mocked traversal tests verify protection.

---

## 5. Next Steps: Milestone M4 Preview

With Milestone M3's sandboxing foundation fully in place, Milestone M4 will focus on:
- Integration with Aider interactive workflows and automated feedback loops.
- Intelligent test subset selection to accelerate regression evaluation on large codebases.
- Container image pre-caching and warm-container pooling for sub-second execution overhead.
- Enhanced AST change impact scoring to prioritize high-risk test execution.
