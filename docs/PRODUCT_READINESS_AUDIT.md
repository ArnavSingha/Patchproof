# PatchProof: Product Readiness & Usability Audit

**Author:** Principal Product Engineer, DevEx Lead, QA Lead, Security Architect  
**Scope:** Complete Codebase, CLI, Packaging, Developer Onboarding, Upstream Aider Integration  
**Date:** November 21, 2026  
**Status:** **DISCOVERY AUDIT COMPLETE (CRITICAL USABILITY BLOCKERS IDENTIFIED)**

---

## 1. Executive Summary

PatchProof's underlying engine (Milestones M1, M2, and M3) possesses strong algorithmic foundations: AST analysis, unified diff normalization, SEARCH/REPLACE block parsing, regression classification state machines, and fail-closed security boundary models.

However, from a **Real User Developer Experience** perspective, **PatchProof is currently an internal engine, not an end-user product**.

If an external developer cloned this repository today, their experience would immediately stall at the first command. Passing 240 unit and mocked tests proves internal architectural compliance, but hides severe productization blockers:
1. The default runner container image does not exist, and no Dockerfile is provided.
2. The default container execution configuration points `pytest` at an empty tmpfs directory (`/workspace`) while the repository is mounted at `/repo`, causing test runs to report zero tests (`INCONCLUSIVE`).
3. There is no documentation explaining the CLI in `README.md`.
4. Error messages dump raw operating system pipe exceptions rather than providing actionable remediation.
5. Feeding changes from Aider requires awkward manual file redirection (`git diff > p.patch`) rather than direct Git or Aider integration.

---

## 2. Detailed Audit Findings by Dimension

### 2.1. Installation & Distribution
- **Current State:**
  - `pyproject.toml` defines a standard package with `[project.scripts] patchproof = "patchproof.cli:main"`.
  - Dependencies are minimal (`pydantic>=2.0.0,<3.0.0`).
- **Product Deficiencies:**
  - **Not published to PyPI:** Requires manual git clone and `pip install -e .`.
  - **No binary distribution:** Users must manage a Python virtual environment to run a CLI tool that evaluates other code. Tool isolation via `pipx` or a standalone executable is undocumented.
  - **Missing System Prerequisites:** Users are not told they need Docker Desktop, WSL2 on Windows, or specific Linux permissions (`docker` group) before running commands.

---

### 2.2. The First-Time User Experience (FTUX)
- **Current State:**
  - A user clones the repository, installs with `pip install -e .`, and opens `README.md`.
- **Product Deficiencies:**
  - **Outdated Documentation:** `README.md` only documents Milestone M1 (the core normalizer) and `pytest`. It makes zero mention of the `patchproof` CLI, the `verify` subcommand, or Docker requirements.
  - **No Quickstart / Demo:** There are no sample patches, no demo repository, and no walk-through command.
  - **Blind CLI Discovery:** Running `patchproof` with no arguments produces a bare argparse message (`error: the following arguments are required: command`) with exit code 5, without suggesting `patchproof verify --help`.

---

### 2.3. The Container Execution Gap (Critical Architectural Usability Blocker)
- **Current State:**
  - The CLI defaults to `--image patchproof-runner-python:3.11`.
  - Inside the container:
    - Host repo is bind-mounted at `/repo` (`readonly=true`).
    - `/workspace` is mounted as an empty tmpfs (`size=256m, mode=1777`).
    - Container working directory is set to `-w /workspace`.
    - Default command is `("pytest", "-q")`.
- **Product Deficiencies:**
  - **Image Does Not Exist:** `patchproof-runner-python:3.11` is neither on Docker Hub nor defined in any local `Dockerfile`. The CLI immediately fails with `docker: Error response from daemon: pull access denied`.
  - **If User Uses `python:3.11-slim`:**
    1. `python:3.11-slim` does not include `pytest`. Running `pytest` fails with `executable file not found in $PATH`.
    2. Because `--network none` is enforced, the container cannot `pip install pytest`.
    3. Even if `pytest` were installed in the image, running `pytest` inside `-w /workspace` looks for tests in `/workspace`, which is an empty tmpfs! The source code is in `/repo`. Thus, zero tests are discovered, and PatchProof reports `INCONCLUSIVE` (exit code 3).
  - **Root Cause:** In M3 architecture, copying from `/repo` to `/workspace` was designed as an entrypoint step, but the CLI was wired directly to `("pytest", "-q")`.

---

### 2.4. Upstream Aider Integration & Workflow Friction
- **Current State:**
  - `patchproof verify <patch>` strictly expects either a file path on disk or `-` from standard input.
- **Product Deficiencies:**
  - **No Direct Git Integration:** In 90% of Aider workflows, Aider modifies files in the user's current Git working tree or creates a commit. To verify with PatchProof, the user must manually run:
    ```bash
    git diff > /tmp/aider.patch
    patchproof verify /tmp/aider.patch
    ```
    or remember the pipe syntax:
    ```bash
    git diff | patchproof verify -
    ```
  - **No Commit Verification:** Aider frequently commits its changes. There is no `patchproof verify --head` or `patchproof verify --commit <sha>`.
  - **No Direct Aider Hook:** PatchProof cannot yet be called as an automated post-generation linter or test validator from Aider's `--test-cmd` or architect workflows.

---

### 2.5. Error Messaging & Diagnostics
- **Current State:**
  - Errors print raw internal exceptions.
- **Product Deficiencies:**
  - **Docker Offline:**
    *Actual output:*
    `summary: Infrastructure failure: Docker daemon is unavailable (Docker daemon is not responsive: WARNING: ... open //./pipe/docker_engine: The system cannot find the file specified.)`
    *User reaction:* "What does this mean? How do I fix it?"
    *Needed:* "Docker Desktop is offline. Please start Docker Desktop and ensure it is in Linux Container mode."
  - **Patch Application Context Mismatch:**
    *Actual output:*
    `Patched environment setup failed: Patch application failed: Context mismatch in src/app.py at hunk starting at line 14`
    *Needed:* Plain English explanation of why the patch rejected (e.g. "The patch was created against an older version of src/app.py. Hunk context lines did not match.").
  - **Missing Prerequisites:** There is no `patchproof doctor` command to validate Docker, WSL2, permissions, and runner images upfront.

---

### 2.6. Cross-Platform Reality Check

| Operating System | Current Viability | Major Friction Points |
| :--- | :---: | :--- |
| **Windows 11** | High Potential, High Friction | • Docker Desktop requires WSL2 engine.<br>• Named pipe permissions (`docker_engine` vs `dockerDesktopLinuxEngine`).<br>• Physical symlink creation requires Developer Mode or Admin rights. |
| **Linux (Ubuntu/Debian)** | High Viability, Medium Friction | • Requires user to be in `docker` group (`usermod -aG docker $USER`).<br>• Native bind mounts and tmpfs work smoothly. |
| **macOS (Apple Silicon)** | Medium Viability | • Docker Desktop or OrbStack VM required.<br>• Architecture differences (`linux/amd64` vs `linux/arm64`) can cause slow emulation if x86 runner images are hardcoded. |

---

## 3. Prioritized Gap Matrix

| Priority | Issue ID | Area | Description | Impact |
| :---: | :---: | :--- | :--- | :--- |
| **P0** | **BLOCK-1** | Runner Image | Default image `patchproof-runner-python:3.11` does not exist on Docker Hub or locally. | Prevents all real Docker execution out-of-the-box. |
| **P0** | **BLOCK-2** | Workspace Sync | Working directory is empty `/workspace`; repo is at `/repo`. Pytest finds 0 tests. | Default execution always reports `INCONCLUSIVE`. |
| **P1** | **USAB-1** | Diagnostics | Docker failure outputs raw Windows named pipe errors with no recovery steps. | Developer cannot diagnose setup issues without help. |
| **P1** | **USAB-2** | Workflows | No `patchproof doctor` to verify Docker, Linux mode, and image readiness. | User only discovers failures mid-run. |
| **P1** | **USAB-3** | Workflow | No native Git flags (`--git`, `--staged`, `--head`); requires manual file piping. | High friction when paired with Aider. |
| **P1** | **USAB-4** | Test Commands | Cannot pass custom test commands (e.g. `--test-command "pytest tests/unit"`). | Must run all repo tests every time. |
| **P2** | **DOCS-1** | Documentation | `README.md` documents only M1; CLI usage is completely missing. | New users cannot discover features. |
| **P2** | **DOCS-2** | Demos | No bundled sample repository or example patches for quick onboarding. | User cannot test tool before trying on real code. |
| **P2** | **USAB-5** | Feedback | No progress spinner during container execution; CLI appears frozen during long runs. | Poor interactive terminal experience. |
| **P3** | **REP-1** | Reporting | Terminal dashboard is plain text; JSON is raw; no shareable visual HTML report. | Hard to share evidence with non-technical peers. |

---

## 4. Recommendations for Productization

1. **Implement `patchproof doctor`:** A diagnostic command that checks Python version, Docker installation, daemon running state, WSL2/Linux engine, and runner image availability, printing green checks or clear remediation steps.
2. **Provide a Standard Runner Image & Dockerfile:** Package a reproducible `Dockerfile` in `docker/runner.Dockerfile` that installs Python 3.11, `pytest`, common test plugins, and creates unprivileged user `10001:10001`.
3. **Fix Container Workspace Synchronization:** In `docker_cli.py`, ensure the container entrypoint properly mounts the workspace or copies `/repo` to `/workspace` so `pytest` discovers the project's tests.
4. **Add Native Git Integration:** Implement `patchproof verify --git` (verifies uncommitted `git diff`) and `patchproof verify --head` (verifies latest commit).
5. **Add Configurable Test Commands:** Allow `--test-command "pytest tests/unit/test_app.py"` and `.patchproof.toml` configuration.
6. **Rewrite User Documentation:** Update `README.md` with visual FTUX guide, installation instructions, Aider workflow examples, and troubleshooting FAQ.
