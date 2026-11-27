# Comparative Gap Analysis: Current vs. Target 100x Architecture

**Status:** APPROVED ARCHITECTURAL SPECIFICATION  
**Reference:** [01_DEBATE_TRANSCRIPT.md](01_DEBATE_TRANSCRIPT.md)

---

## 1. Systemic Comparison Matrix

| Component | Current State (v0.1.0) | Architect Vulnerability (Critic 1) | CISO Vulnerability (Critic 2) | Upgraded Target Architecture (100x) | Priority |
| :--- | :--- | :--- | :--- | :--- | :---: |
| **Sandbox Network Isolation** | Strict `--network none` on single container | Kills tests needing local sidecars (Postgres, Redis, mock servers) | Inability to test webhook handlers or local RPC without breaking isolation | **Dual-Mode Ephemeral Bridge (`--network internal`):** Zero external egress, but allows isolated intra-sandbox sidecar discovery | **P0** |
| **Anti-Cheat Test Tampering Guard** | Static AST visitor checking test function count, `@pytest.mark.skip`, assertions | Blind to test configurations outside Python (e.g. non-pytest runners) | Vulnerable to dynamic runtime evasion (`getattr()`, `builtins.__dict__`, monkeypatching) | **Hybrid Anti-Cheat (Static AST + Dynamic Runtime Plugin):** Injects read-only pytest plugin measuring executed assertion counts across runs | **P0** |
| **Cryptographic Attestation** | Symmetric HMAC-SHA256 (`patchproof.cert.json`) | Key distribution friction across decentralized developer machines | Shared secret compromise invalidates entire signature chain | **Asymmetric Ed25519 + Sigstore/OIDC Attestation:** Public key verification, in-toto v1.0 provenance signed by GitHub OIDC token | **P1** |
| **Monorepo & Staging Scale** | `git archive HEAD` on entire repository root | Full-tree extraction takes 30-180s on large multi-GB monorepos | Copying unneeded subtrees increases temporary storage attack surface | **Sparse Scoped Tree Extraction:** `git archive HEAD:<subpath>` extracting only touched services, dropping staging time to <200ms | **P1** |
| **Container Engine Hardening** | Standard Docker with dropped capabilities (`--cap-drop=ALL`) | Requires local Docker daemon; no support for alternative runtimes (Podman, Finch) | Shared host Linux kernel exposes kernel syscall zero-days to unprivileged exploits | **Multi-Runtime Seccomp & gVisor Sandbox (`runsc`):** Custom seccomp filter for Docker + optional gVisor microVM driver for untrusted code | **P2** |
| **Fault Localization** | Ochiai SBFL correlating traceback frames to patch hunks | Large multi-file diffs can yield diffuse suspicion scores | Agent receives localized prompt but no automated regression test generator | **Differential Hunk Slicing & Counterfactual Test Synthesis:** Generates targeted reproduction test case capturing minimal regression | **P2** |

---

## 2. In-Depth Gap Evaluations

### Gap 1: Dynamic Runtime Test Evasion
- **Risk Severity:** **CRITICAL (Security / Integrity Gate Failure)**
- **Root Cause:** Static AST checks occur before code execution. Dynamic metaprogramming (`exec`, `__import__`, modifying `pytest_collection_modifyitems`) runs during execution and evades static node inspection.
- **Remediation Requirement:** A container-injected pytest plugin must track:
  1. `tests_collected` (baseline vs. patched)
  2. `assertions_executed` (baseline vs. patched)
  3. `skip_reasons` (inspect dynamic skip calls)
  If the patched run executed fewer assertions than the baseline for an identical test input, the execution is flagged `SUSPICIOUS` and rejected.

### Gap 2: Enterprise Sidecar Isolation
- **Risk Severity:** **HIGH (Adoption Blocker for Backend/Enterprise Apps)**
- **Root Cause:** Real-world integration tests fail when `--network none` cuts off access to `localhost:5432` (PostgreSQL) or `localhost:6379` (Redis).
- **Remediation Requirement:** PatchProof must support `patchproof-compose` or ephemeral internal bridge networks:
  ```bash
  docker network create --internal patchproof-net-isolated
  ```
  Containers attached to this network can communicate with one another, but packets directed to public IPs (`0.0.0.0/0`) or the host network are dropped by kernel routing rules.

### Gap 3: Asymmetric Cryptographic Provenance
- **Risk Severity:** **HIGH (Enterprise Security Compliance)**
- **Root Cause:** A symmetric HMAC secret requires every CI worker and developer laptop to share the same pre-shared key (`PATCHPROOF_SECRET_KEY`). If one worker leaks it, the attestation chain is broken.
- **Remediation Requirement:**
  - Shift default signing to **Ed25519** keypairs (`patchproof keygen` generates public/private pair).
  - Private key resides strictly in CI secret or hardware HSM; public key is committed to `.patchproof/pubkey.pem`.
  - In GitHub Actions, use `actions/attest` with GitHub OIDC Sigstore for keyless, zero-maintenance cryptographic attestations.

### Gap 4: Monorepo Scoped Slicing
- **Risk Severity:** **MEDIUM (Performance Degradation on Large Checkouts)**
- **Root Cause:** `git archive HEAD` archives every file in the repository. On checkouts with 100,000+ files, this produces significant I/O and tar decompression overhead.
- **Remediation Requirement:** When `CanonicalPatch` is parsed, compute the common directory ancestor of all modified files. If the common ancestor is `services/order_service/`, extract only that path:
  ```bash
  git archive HEAD:services/order_service/
  ```
  Staging copy overhead drops from $O(\text{RepoSize})$ to $O(\text{PatchScope})$.
