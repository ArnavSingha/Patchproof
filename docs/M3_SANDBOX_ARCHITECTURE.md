# PatchProof Milestone M3: Isolated Sandbox Architecture & Threat Model

**Document ID:** ARCH-M3-SANDBOX-001  
**Status:** REVISED & APPROVED — ARCHITECTURE & THREAT MODEL SPECIFICATION  
**Authors:** PatchProof Systems Architecture & Security Engineering Team  
**Reviewers:** Independent Adversarial Reviewer, Technical Architect, Security Engineer  
**Workspace:** `.`  

---

## 1. Executive Summary & Core Invariants

Milestone M3 establishes the isolated execution subsystem of **PatchProof**. The core function of PatchProof is to evaluate AI-generated code patches through empirical execution evidence without endangering the host environment, developer credentials, or infrastructure.

### The Four Non-Negotiable Invariants:
1. **Worktree vs. Sandbox Disambiguation:** A Git worktree is purely a workspace file-isolation mechanism. **A Git worktree is NOT a security sandbox.** Code executed inside a worktree runs on the host OS with the developer's full user privileges and network access.
2. **Untrusted Code Never Runs on Host:** Any patch evaluation, test execution, dependency resolution, or script execution initiated by or derived from AI-generated diffs MUST occur exclusively within an isolated container.
3. **Fail-Closed Execution:** If Docker is not installed, not running, in an unsupported mode, inaccessible, or misconfigured, PatchProof **MUST FAIL CLOSED**. It is strictly forbidden to fall back to host-level process execution.
4. **Zero Host Credential Exposure:** Host environment variables, user configuration directories (`~/.ssh`, `~/.aws`, `~/.config`, `~/.gitconfig`), the repository's `.git/` directory, and Docker control sockets (`/var/run/docker.sock`, `\\.\pipe\docker_engine`) must NEVER be mounted, exposed, or inherited by test containers.

---

## 2. Windows 11 + Docker Desktop Architecture

Operating on Windows 11 introduces specific architectural realities regarding virtualization boundaries, filesystem performance, and process sandboxing.

```
+-----------------------------------------------------------------------------------------+
|                                    WINDOWS 11 HOST                                      |
|                                                                                         |
|  +-------------------------------------+      +--------------------------------------+  |
|  |       PatchProof Orchestrator       |      |     Developer Secrets & Identity     |  |
|  |  (Python Host Process - No Root)    |      |  (~/.ssh, ~/.aws, Environment Vars)  |  |
|  +------------------+------------------+      +------------------+-------------------+  |
|                     | (Docker CLI / Named Pipe,                  |                      |
|                     |  close_fds=True)                           x BLOCKED (No Mounts)  |
|                     v                                                                   |
|  +---------------------------------------------------------------+-------------------+  |
|  |                          Docker Desktop for Windows Engine                        |  |
|  |               Pre-flight Check: docker info {{.OSType}} == linux                  |  |
|  |                          (\\.\pipe\docker_engine)                                 |  |
+--+-----------------------------------------------------------------------------------+--+
|                                           | Hyper-V / Virtualization Boundary            |
|                                           v                                              |
|  +-----------------------------------------------------------------------------------+  |
|  |                              WSL2 Utility VM (Moby Linux)                         |  |
|  |                                                                                   |  |
|  |  +-----------------------------------------------------------------------------+  |  |
|  |  |                   Isolated Test Container (Ephemeral)                       |  |  |
|  |  |                                                                             |  |  |
|  |  |  Security Policy:                                                           |  |  |
|  |  |  - User: 10001:10001 (unprivileged non-root)                               |  |  |
|  |  |  - Capabilities: --cap-drop=ALL                                             |  |  |
|  |  |  - Network: --network none (strictly offline)                               |  |  |
|  |  |  - Privileges: --security-opt=no-new-privileges                             |  |  |
|  |  |  - Resource Caps: 1.0 CPU, 1GB RAM, 128 PIDs, 15s timeout                      |  |  |
|  |  |                                                                             |  |  |
|  |  |  Filesystem Layout:                                                         |  |  |
|  |  |  - /workspace (tmpfs 256MB / ephemeral copy - Read/Write)                   |  |  |
|  |  |  - /repo_base (host checkout mount - Strictly Read-Only :ro)               |  |  |
|  |  |  - /repo_base/.git (MASKED via empty tmpfs mount - Zero Token Exposure)     |  |  |
|  |  +-----------------------------------------------------------------------------+  |  |
|  +-----------------------------------------------------------------------------------+  |
+-----------------------------------------------------------------------------------------+
```

### 2.1 WSL2 Backend Boundaries
- **Virtualization Layer:** On Windows 11, Docker Desktop operates via WSL2, executing containers inside a lightweight Linux utility VM managed by Hyper-V.
- **Hardware Isolation:** The boundary between the Windows host and the container is reinforced by hardware-assisted virtualization (Hyper-V). Even in the event of a container breakout inside the Linux kernel, the adversary lands in the WSL2 utility VM, NOT directly in the Windows user session.
- **Filesystem Cross-Boundary Overhead:** Bind mounting Windows paths (`/mnt/c/...`) into WSL2 containers suffers from 9P/drvfs filesystem translation overhead. To achieve sub-second execution, source files are either:
  1. Streamed directly via tar archive into a container-managed `tmpfs` volume (excluding `.git`, `.venv`, and `__pycache__`), or
  2. Mounted strictly as `:ro` with `.git` masked, where workspace mutations occur in container-local RAM (`tmpfs`).

### 2.2 Docker Desktop Engine Modes & Limitations on Windows
- **Mandatory Linux Containers Mode Check:** Docker Desktop for Windows can run in either **Linux Containers mode** (WSL2) or **Windows Containers mode**. Windows Containers mode does NOT support Linux capability drops (`--cap-drop=ALL`), non-root UID mapping (`10001:10001`), or Linux base images.
  - **Pre-flight Check:** The orchestrator must execute:
    ```bash
    docker info --format "{{.OSType}}"
    ```
  - If output is not `linux`, PatchProof **fails closed immediately** with `UnsupportedDockerEngineError: Docker Desktop must be running in Linux Containers mode (WSL2 backend)`.
- **Subprocess File Descriptor Leakage Prevention:** On Windows, all child processes spawned by the orchestrator via Python's `subprocess.Popen` must explicitly set `close_fds=True` to prevent leaking open host file handles, named pipes, or socket descriptors to child processes.
- **Daemon Availability:** Docker Desktop may be stopped, updating, or crashing. The orchestrator probes daemon health with a tight timeout (2.0s) and aborts cleanly if unreachable.

---

## 3. Threat Model & Security Boundaries (STRIDE + AI Threat Vectors)

### 3.1 Adversary Definition & Capabilities
The adversary is an untrusted code patch. This patch may be:
- An AI-hallucinated destructive script (e.g., recursive deletion, fork bomb).
- A prompt-injected payload designed to exfiltrate developer secrets or cloud keys.
- A malicious test designed to hijack the test runner process and persist backdoors.
- A subtle test alteration designed to mask security regressions.

### 3.2 Threat Matrix (STRIDE Analysis)

| Threat Category | Attack Vector | Specific Scenario | PatchProof M3 Mitigation |
| :--- | :--- | :--- | :--- |
| **Spoofing** | Identity Spoofing | Patch attempts to impersonate authorized committer or CI pipeline. | All patch tests run unauthenticated under UID 10001; no git credentials mounted. |
| **Tampering** | Host Filesystem Corruption | Test script attempts `rm -rf /` or path traversal `../../` into host directory. | Host source mounted strictly `:ro`. Workspace execution inside container `tmpfs`. |
| **Repudiation** | Audit Log Deletion | Malicious code clears bash history or deletes test artifacts. | Test runner outputs are captured via stdout/stderr pipe streams directly to host logs. |
| **Information Disclosure** | Credential Exfiltration | Patch accesses `~/.aws/credentials`, `~/.ssh/id_rsa`, or environment tokens. | Host credential paths are explicitly never mounted. Environment variables sanitized to allowlist (`PATH`, `LANG`). |
| **Information Disclosure** | Git Token Exfiltration | Patch reads `/repo_base/.git/config` to harvest personal access tokens or remote auth. | `.git` is strictly masked via an empty tmpfs mount or excluded during tar streaming. |
| **Information Disclosure** | Network Exfiltration | Patch connects to external server via `curl`, `socket`, or DNS exfiltration. | Container started with `--network none`. No virtual network bridge or DNS access. |
| **Denial of Service** | Resource Exhaustion | Recursive fork bomb (`:(){ :\|:& };:`), infinite loops, memory leaks. | Enforced cgroup quotas: `--pids-limit 128`, `--memory 1g --memory-swap 1g`, `--cpus 1.0`, hard timeout. |
| **Elevation of Privilege** | Container Escape | Exploiting kernel vulnerabilities or abusing Linux capabilities to gain host root. | Non-root user (`--user 10001:10001`), `--cap-drop=ALL`, `--security-opt=no-new-privileges`. |

---

## 4. Container Lifecycle & Cleanup Architecture

Container lifecycle management must be deterministic and leak-proof. If a container hangs, crashes, or is interrupted via `SIGINT`/`Ctrl+C`, the system guarantees cleanup.

```
[Start]
   |
   v
[1. Verify Docker Daemon & Engine Mode] --(Unavailable or Not Linux)--> [ABORT: Fail Closed]
   |
   +--(Healthy Linux Container Engine)
   v
[2. Prepare Ephemeral Context] -> Staging copy (excluding .git, .venv, __pycache__)
   |
   v
[3. Launch Baseline Container] -> docker run --rm --network none (Baseline Commit)
   |                                 |-- [Timeout Alarm (Host Monitored)]
   v                                 +-- [Resource Monitor]
[4. Capture Baseline Results] <------+
   |
   v
[5. Apply Patch to Worktree Copy] -> Pure in-memory AST / Normalizer application
   |
   v
[6. Launch Patched Container] -> docker run --rm --network none (Patched Code)
   |                                |-- [Timeout Alarm (Host Monitored)]
   v                                +-- [Resource Monitor]
[7. Capture Patched Results] <------+
   |
   v
[8. Comparative Analysis] -> Reconcile Base vs Patch (Regressions vs Pre-existing)
   |
   v
[9. Post-Execution Cleanup] -> Enforce container destruction, prune labeled orphans
   |
   v
[Complete]
```

### 4.1 Guaranteed Cleanup Strategy
1. **Docker `--rm` Flag:** All containers are invoked with `--rm` ensuring the Docker daemon cleans up filesystem layers upon exit.
2. **Explicit Host Process Wrapper:** Python `subprocess.Popen` is wrapped in a context manager (`try ... finally`) with `close_fds=True`.
3. **Emergency Termination Sequence:**
   - If execution exceeds `timeout_seconds`:
     1. Host executes `docker kill <container_name>`.
     2. Wait 1.0s for exit.
     3. If container persists, execute `docker rm -f <container_name>`.
4. **Orphan Sweeper:** All containers are tagged with metadata labels:
   - `patchproof.managed=true`
   - `patchproof.session_id=<uuid>`
   - `patchproof.timestamp=<unix_ts>`
   The orchestrator runs an orphan cleanup sweep at startup and shutdown:  
   `docker ps -aq --filter "label=patchproof.managed=true" --filter "status=running"`.

---

## 5. Filesystem Mount Strategy & Credential Hygiene

Filesystem access is the most critical attack vector in containerized test runners. The strategy must guarantee that the host repository is physically incapable of being modified by the container, and that no sensitive host repository metadata is exposed.

### 5.1 Dual-Mount Isolation Model & The `.git/` Masking Mandate
Containers utilize two distinct mount points with strict credential masking:

1. **Host Source Mount (Reference Only) with `.git/` Mask:**
   - Host path: `.`
   - Container target: `/repo_base`
   - Mount Options: `type=bind,source=...,target=/repo_base,readonly=true`
   - **VULN-1 Mitigation (Strict `.git/` Masking):** To prevent untrusted test scripts from reading `.git/config` and stealing personal access tokens, private remote URLs, or committer credentials, `.git/` is masked with an empty, non-executable tmpfs:
     ```bash
     --mount type=tmpfs,destination=/repo_base/.git,tmpfs-size=1k,tmpfs-mode=000
     ```
   - Invariant: Any attempt by a test process to write to `/repo_base` results in immediate `EROFS` (Read-only file system).

2. **Workspace Mount (Execution Layer):**
   - Container target: `/workspace`
   - Type: `tmpfs` (RAM filesystem)
   - Size limit: `256MB`
   - Mount Options: `rw,noexec=false,nosuid,nodev,size=268435456`
   - **Exclusion Filters:** When copying repository source files from `/repo_base` to `/workspace`, all virtual environment directories (`.venv`, `env`), build caches (`__pycache__`, `.pytest_cache`), and `.git` are strictly excluded to avoid saturating the 256MB tmpfs limit.
   - Lifecycle: Destroyed automatically when the container exits.

3. **Masked System Paths:**
   - The following paths are masked inside the container to prevent kernel/system probing:
     - `/proc/kcore`
     - `/proc/sys`
     - `/sys`

---

## 6. Network Isolation & Dependency Boundary Strategy

**Untrusted code must never have network access under any circumstances.**

### 6.1 `--network none` Mandate
- All test runs execute with `--network none`.
- The container namespace has only an isolated loopback (`lo`) interface.
- Outbound TCP/UDP sockets, DNS resolution, and raw socket creation are rejected at the kernel level.

### 6.2 The Strict Pre-Baked Image Policy (VULN-5 Resolution)
To eliminate supply-chain injection attacks and dynamic network downloading risks:
- **No Dynamic Package Installation:** Tests must NEVER execute `pip install` during the test run.
- **Strictly Pre-Baked Images Only:** Milestone M3 relies exclusively on pre-built, pre-scanned base images (`patchproof-runner-python:3.11`). These images contain the exact language runtime, standard testing frameworks (`pytest`, `coverage`), and core baseline packages.
- **Missing Dependencies Policy:** If an AI-generated patch introduces dependencies not present in the pre-baked runner image, PatchProof will NOT attempt to fetch them dynamically. The evaluation will immediately flag the run as `DEPENDENCY_UNAVAILABLE` and reject the patch on unverified dependency grounds.

---

## 7. Resource Quotas & Denial of Service Protection

To prevent rogue code from degrading the host system, strict hardware limits are enforced via cgroups:

| Resource | Enforcement Mechanism | Limit | Threat Mitigated |
| :--- | :--- | :--- | :--- |
| **CPU Allocation** | `--cpus="1.0" --cpu-shares=512` | 1.0 vCPU | Host CPU starvation, crypto-mining. |
| **Memory Allocation** | `--memory="1024m" --memory-swap="1024m"` | 1 GB RAM | Memory leak DoS, host crashing. Swap is disabled (`swap == memory`). |
| **Process Count** | `--pids-limit=128` | 128 PIDs | Fork bombs (`:(){ :\|:& };:`), thread-exhaustion attacks. |
| **Disk Space** | `tmpfs size=256m` | 256 MB | Disk-filling attacks, infinite file write loops. |
| **Execution Duration** | Host timer + Docker stop timeout | 15.0 seconds | Infinite loops, sleeping processes, deadlock bugs. |

---

## 8. Non-Root Execution & Credential Isolation

### 8.1 Non-Root Container Identity
- Containers execute under dedicated unprivileged credentials:
  - User: `10001`
  - Group: `10001`
  - Command: `--user 10001:10001`
- Root execution inside the container is strictly forbidden.

### 8.2 Dropping Linux Capabilities
- Default Docker containers retain capabilities like `CAP_CHOWN`, `CAP_FOWNER`, `CAP_SETUID`.
- PatchProof strips ALL Linux kernel capabilities:
  `--cap-drop=ALL`
- Privilege escalation prevention is locked:
  `--security-opt=no-new-privileges`

### 8.3 Environment Sanitization Allowlist
The host process sanitizes all environment variables passed to the container. Zero host environment variables are passed through by default.

**Strict Environment Allowlist:**
```
PATH=/usr/local/bin:/usr/local/sbin:/usr/bin:/usr/sbin:/bin:/sbin
LANG=C.UTF-8
LC_ALL=C.UTF-8
PYTHONUNBUFFERED=1
PYTHONDONTWRITEBYTECODE=1
PYTHONPATH=/workspace
```
*Host credentials (`AWS_ACCESS_KEY_ID`, `GITHUB_TOKEN`, `OPENAI_API_KEY`, Windows `%USERPROFILE%`) are entirely omitted.*

---

## 9. Baseline vs. Patched Comparative Execution Lifecycle

A patch cannot be judged solely by whether its tests pass. Pre-existing test failures must be isolated from new regressions.

### 9.1 The Two-Phase Lifecycle

```
             Phase 1: Baseline Execution               Phase 2: Patched Execution
             (Pre-Patch Repository State)             (Post-Patch Repository State)
                         |                                          |
                         v                                          v
                 +---------------+                          +---------------+
                 | Baseline Run  |                          |  Patched Run  |
                 +-------+-------+                          +-------+-------+
                         |                                          |
                         +--------------------+---------------------+
                                              |
                                              v
                                  [Reconciliation Engine]
                                              |
                                              v
                                   [Deterministic Report]
```

### 9.2 The Comparative Decision Matrix (Including Collection Guard)

Let $T$ be the set of all executed test cases. For any test $t \in T$:

| Baseline Phase | Patched Phase | Classification | Patch Verdict Impact |
| :---: | :---: | :--- | :--- |
| **`COLLECTION_OK`** | **`COLLECTION_FAIL`** | **Fatal Harness / Import Crash** | **REJECT PATCH (`COLLECTION_FAILURE`)** (Syntax error / broken import) |
| `PASS` | `PASS` | **Clean Preservation** | Neutral / Positive |
| `FAIL` | `FAIL` | **Pre-Existing Failure** | Neutral (Not caused by patch) |
| `PASS` | `FAIL` | **Introduced Regression** | **REJECT PATCH** (Critical bug introduced) |
| `FAIL` | `PASS` | **Resolved Defect** | **ACCEPT PATCH** (Bug fix confirmed) |
| `PASS` | `SKIPPED` | **Suppression Alert** | **SUSPICIOUS** (Potential test evasion) |
| `SKIPPED` | `SKIPPED` | **Pre-Existing Skip** | Neutral |
| *Missing* | `PASS` | **New Test Passing** | Positive (New coverage validated) |
| *Missing* | `FAIL` | **New Test Failing** | **REJECT PATCH** (New test fails) |

---

## 10. Test Integrity Analyzer Boundaries

AI-generated patches frequently attempt to achieve a "green build" by tampering with tests. PatchProof separates **deterministic verification** from **heuristic signals**.

### 10.1 Deterministic Integrity Checks (Absolute Proof)
The following checks are mathematically or syntactically verifiable:
1. **Deleted Test Files:** Patch removes a file under `tests/` (`ChangeType.DELETE`).
2. **Deleted Test Functions/Methods:** AST comparison reveals a function matching `test_*` present in baseline but missing in patched code.
3. **Skipped Test Markers Added:** AST detects addition of `@pytest.mark.skip`, `@pytest.mark.skipif`, or `pytest.skip()` within existing tests.
4. **Test Harness Modification:** Patch modifies test runner configuration files (`pytest.ini`, `setup.cfg`, `tox.ini`, `.coveragerc`).

### 10.2 Heuristic Integrity Checks (Suspicious Signals)
The following checks provide probabilistic warnings but cannot offer mathematical certainty:
1. **Weakened Assertions:** Replacing `assert x == 42` with `assert x is not None` or `assert True`.
2. **Exception Swallowing:** Introducing `try: ... except Exception: pass` inside test bodies.
3. **Mock Inflation:** Introducing excessive `unittest.mock.MagicMock` patches that stub out the system under test.
4. **Flaky Test Oscillation:** Tests that produce alternating pass/fail results across repeated container runs under identical seeds.

---

## 11. Failure Handling & Fail-Closed Protocols

| Failure Condition | System Behavior | Rationale |
| :--- | :--- | :--- |
| **Docker Daemon Inactive** | Immediate `DockerUnavailableError`. Exit code 2. | **Strictly fail closed.** Never fall back to host execution. |
| **Docker Engine != Linux** | Immediate `UnsupportedDockerEngineError`. Exit code 2. | **Strictly fail closed.** Windows Containers mode cannot enforce Linux isolation. |
| **Container Timeout (>15s)** | Host sends `docker kill`. Mark test as `TIMEOUT`. | Prevents hanging subprocesses and thread starvation. |
| **Container OOM (Code 137)** | Detect `OOMKilled: true` via inspect. Mark as `MEMORY_EXHAUSTION`. | Identifies unbounded memory allocations in patch code. |
| **Hunk Application Error** | Abort execution before container launch. Return `PatchApplicationError`. | Invalid patches must never reach test evaluation. |
| **Subprocess Abort (`Ctrl+C`)** | `finally` block executes `docker kill` on active container ID. | Guarantees zero orphaned containers on developer workstations. |

---

## 12. Security Limitations & Residual Risks

Honest security engineering requires explicit acknowledgment of residual risks:

1. **Docker Shared Kernel Architecture:**  
   Docker containers share the underlying Linux kernel (WSL2 VM kernel on Windows). Containers provide process isolation, not hardware virtualization. A 0-day Linux kernel privilege escalation exploit could theoretically break out of the container into the WSL2 VM.
2. **WSL2 VM Boundary:**  
   While breaking out of the container to the WSL2 VM does not grant native Windows administrator privileges, the WSL2 VM can access mounted Windows drives if permissions permit. PatchProof mitigates this by restricting mounts strictly to `:ro` on host source trees and masking `.git/`.
3. **Static AST Analysis Blind Spots:**  
   Dynamic Python constructs (`eval()`, `exec()`, `importlib.import_module()`, `sys.modules` manipulation) cannot be fully analyzed statically. The isolated container acts as the definitive security control against dynamic threats.

---

## 13. Unit, Integration, and Adversarial Test Strategy for M3

Implementation will be validated through a three-tiered testing pyramid:

```
                  / \
                 /   \
                / Adv \   <- Adversarial Tests (Malicious payloads, fork bombs, escapes)
               /-------\
              / Integr. \ <- Real Docker Container Integration Tests
             /-----------\
            /    Unit     \ <- Mocked Subprocess & Command Builder Tests
           +---------------+
```

### 13.1 Tier 1: Unit Tests (Fast, Offline, No Docker Required)
- `test_docker_command_builder`: Validates that flags (`--network none`, `--cap-drop=ALL`, `--user 10001:10001`, `--memory 1g`) are consistently generated.
- `test_git_mask_flag_generated`: Validates that `--mount type=tmpfs,destination=/repo_base/.git...` is always present.
- `test_environment_sanitizer`: Confirms host variables are stripped and only allowlisted keys are passed.
- `test_exit_code_parser`: Validates translation of Docker exit codes (0 = Success, 137 = OOM, 124 = Timeout).
- `test_fail_closed_on_missing_docker`: Validates that `DockerUnavailableError` is raised when the docker binary is missing.
- `test_fail_closed_on_windows_containers_mode`: Validates that `UnsupportedDockerEngineError` is raised when OSType is not linux.

### 13.2 Tier 2: Integration Tests (Requires Active Docker Engine)
- `test_container_lifecycle_clean`: Spins up a benign alpine/python container and verifies automatic removal (`--rm`).
- `test_read_only_host_mount`: Proves that attempts to write to `/repo_base` raise `PermissionError` / `EROFS`.
- `test_git_directory_masked`: Proves that `/repo_base/.git` is empty and unreadable inside the container.
- `test_tmpfs_workspace_mutation`: Confirms that changes to `/workspace` succeed and remain isolated to RAM.
- `test_comparative_runner_integration`: Runs baseline vs patched pytest suite on a sample python fixture.

### 13.3 Tier 3: Adversarial Tests (Security & Integrity Validation)
- `test_malicious_fork_bomb`: Executes `:(){ :|:& };:` inside container. Confirms `--pids-limit 128` terminates bomb without host impact.
- `test_network_exfiltration_attempt`: Attempts `socket.connect(("8.8.8.8", 53))`. Confirms immediate `NetworkUnreachable` error.
- `test_host_credential_probe`: Script inspects `os.environ` and attempts reading `/root/.ssh`. Confirms zero host credentials found.
- `test_timeout_enforcement`: Executes `time.sleep(30)`. Confirms host terminates container at 15.0s.
- `test_test_tampering_detection`: Injects `pytest.skip` into a test. Confirms `TestIntegrityAnalyzer` flags tampering.
- `test_collection_crash_handling`: Injects fatal syntax error in test module. Confirms `COLLECTION_FAILURE` triggers `REJECT PATCH`.

---

## 14. Independent Review Findings & Resolutions

All four conditional review findings have been resolved in this revision:

1. **VULN-1 (.git Masking):** Resolved in Section 2, Section 3.2, and Section 5.1 via mandatory empty tmpfs mount at `/repo_base/.git`.
2. **VULN-2 (Docker OSType Pre-flight):** Resolved in Section 2.2 and Section 11 with explicit `docker info --format '{{.OSType}}' == 'linux'` check and `close_fds=True` mandate.
3. **VULN-4 (Collection Crash Guard):** Resolved in Section 9.2 with top-level `COLLECTION_FAIL` guard row triggering `REJECT PATCH`.
4. **VULN-5 (Pre-baked Images Only):** Resolved in Section 6.2 with strict policy barring dynamic wheel downloads in M3.

---

## 15. Implementation Plan & Deliverables (Awaiting Authorization)

| Phase | Module | Primary Deliverables | Verification Target |
| :---: | :--- | :--- | :--- |
| **Phase 1** | `patchproof.sandbox.models` | Immutable Pydantic models: `ContainerConfig`, `ExecutionResult`, `ComparisonReport`, `SandboxStatus`. | Unit tests for immutability & validation. |
| **Phase 2** | `patchproof.sandbox.docker_cli` | `DockerCommandBuilder`, `EnvironmentSanitizer`, `DaemonHealthCheck` (OSType verification, `close_fds=True`). | Unit tests for flag generation & allowlisting. |
| **Phase 3** | `patchproof.sandbox.runner` | `ContainerRunner`: Process manager, host timeout monitor, cleanup hooks, `.git` masking. | Unit & integration tests with mock/real Docker. |
| **Phase 4** | `patchproof.sandbox.comparator` | Baseline vs. Patched comparative runner, collection crash handler, regression analyzer. | Full comparative test suite. |
| **Phase 5** | `patchproof.sandbox.adversarial` | Adversarial test suite: fork bombs, network probes, timeout aborts, collection crashes. | Adversarial test pass. |

---

> **STOP:** Architecture and threat modeling complete. Per user instructions, implementation code will not begin until explicit human review and authorization is granted.
