# Implementation Blueprint: The 100x Indestructible Verification Engine

**Document Version:** 1.0.0  
**Target Engine Version:** PatchProof Core v0.2.0  
**Classification:** Binding Engineering Specification  
**Governing Standard:** [AGENTS.md](../../AGENTS.md)

---

## 1. Architectural Overview

To resolve the critical vulnerabilities exposed by the Adversarial Council ([01_DEBATE_TRANSCRIPT.md](01_DEBATE_TRANSCRIPT.md)), PatchProof will implement four new defense-in-depth subsystems:

```
                            PATCHPROOF 100X DEFENSE CORE
                            
   ┌────────────────────────────────────────────────────────────────────────┐
   │ 1. DUAL-LAYER ANTI-CHEAT                                               │
   │    Layer A: Static AST Delta Analyzer (Syntax audit)                   │
   │    Layer B: Dynamic Runtime Auditor Plugin (Bytecode & Assertion count)│
   └───────────────────────────────────┬────────────────────────────────────┘
                                       │
   ┌───────────────────────────────────┴────────────────────────────────────┐
   │ 2. DUAL-MODE EPHEMERAL SANDBOXING                                      │
   │    Mode A: Strict Air-Gapped (--network none)                          │
   │    Mode B: Internal Multi-Service Bridge (--network internal)          │
   └───────────────────────────────────┬────────────────────────────────────┘
                                       │
   ┌───────────────────────────────────┴────────────────────────────────────┐
   │ 3. ASYMMETRIC CRYPTOGRAPHIC PROVENANCE                                 │
   │    Engine A: Ed25519 Asymmetric Public/Private Keypair Signing         │
   │    Engine B: GitHub OIDC Sigstore / in-toto Provenance Attestation     │
   └───────────────────────────────────┬────────────────────────────────────┘
                                       │
   ┌───────────────────────────────────┴────────────────────────────────────┐
   │ 4. MONOREPO SPARSE SCOPED EXTRACTION                                   │
   │    Computes Minimal Common Directory Ancestor for Subtree Staging      │
   └────────────────────────────────────────────────────────────────────────┘
```

---

## 2. Subsystem Specifications

### Subsystem 1: Dynamic Runtime Assertion Auditor (`runtime_guard.py`)

#### Objective
Detect runtime evasion techniques (such as dynamic `getattr()`, `builtins` monkeypatching, or `pytest_collection_modifyitems` hijacking) that evade static AST detection.

#### Architecture
PatchProof injects a self-contained, read-only pytest plugin into `/tmp/patchproof_runtime_guard.py` mounted inside the container.

#### Plugin Code Architecture
```python
"""Injected container runtime auditor for assertion execution profiling."""

import pytest
import sys
import json
import pathlib

class RuntimeAssertionTracker:
    def __init__(self) -> None:
        self.assertions_by_test: dict[str, int] = {}
        self.current_test: str | None = None

    def trace_calls(self, frame, event, arg):
        # Count COMPARE_OP or assertion events in test functions
        if event == "line":
            code = frame.f_code
            if "test_" in code.co_name or code.co_name.startswith("test"):
                # Track executed lines inside test functions
                pass
        return self.trace_calls

@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_protocol(item, nextitem):
    # Monitor item execution and profile executed assertions
    yield

def pytest_sessionfinish(session, exitstatus):
    # Dump runtime execution profile to /tmp/patchproof_runtime_profile.json
    metrics = {
        "collected_count": len(session.items),
        "executed_count": session.testscount,
    }
    pathlib.Path("/tmp/patchproof_runtime_profile.json").write_text(json.dumps(metrics))
```

#### Reconciler Logic in `classifier.py`
```python
def reconcile_runtime_profiles(
    baseline_profile: RuntimeProfile,
    patched_profile: RuntimeProfile,
) -> tuple[bool, str | None]:
    """Compare runtime execution profiles between baseline and patched containers.
    
    Returns (tampering_detected, reason).
    """
    if patched_profile.executed_assertions < baseline_profile.executed_assertions:
        drop = baseline_profile.executed_assertions - patched_profile.executed_assertions
        return True, f"Dynamic test tampering detected: {drop} fewer assertion(s) executed at runtime"
    return False, None
```

---

### Subsystem 2: Dual-Mode Ephemeral Bridge Sandbox (`network_bridge.py`)

#### Objective
Enable multi-service enterprise tests (requiring PostgreSQL, Redis, or local mock HTTP servers) without compromising host security or enabling external internet egress.

#### Interface Specification
```python
class NetworkMode(StrEnum):
    AIRGAPPED = "none"           # Zero networking (default unit tests)
    INTERNAL_BRIDGE = "internal" # Isolated intra-sandbox bridge (enterprise multi-service)

@dataclass(frozen=True)
class SandboxNetworkConfig:
    mode: NetworkMode
    network_name: str | None = None
    sidecars: tuple[str, ...] = ()  # e.g. ("postgres:16-alpine", "redis:7-alpine")
```

#### Docker Command Construction Rule
When `mode == NetworkMode.INTERNAL_BRIDGE`:
```bash
# 1. Create temporary internal network with no external routing
docker network create --internal --opt com.docker.network.bridge.enable_icc=true patchproof-net-12345

# 2. Launch container attached to internal network
docker run --network=patchproof-net-12345 ...

# 3. Guaranteed cleanup on teardown
docker network rm patchproof-net-12345
```
*Security Guarantee:* The `--internal` flag configures host `iptables` to drop all forwarding rules to public interfaces (`eth0`, `wlan0`, `wan`). Packets can only flow between containers explicitly attached to `patchproof-net-12345`.

---

### Subsystem 3: Asymmetric Ed25519 Cryptographic Attestation (`asymmetric.py`)

#### Objective
Eliminate shared symmetric HMAC secrets. Allow anyone to verify Proof-of-Patch certificates using public keys, while restricting signing to private keys or GitHub OIDC identity tokens.

#### Data Model Specification
```python
class AsymmetricProofCertificate(BaseModel):
    """Cryptographic Proof-of-Patch certificate signed via Ed25519."""
    
    model_config = ConfigDict(frozen=True)
    
    schema_version: str = "2.0.0"
    verdict: str
    patch_sha256: str
    repository: str
    base_commit: str
    anti_cheat_passed: bool
    regressions_count: int
    timestamp_utc: str
    signer_identity: str         # e.g. "github.com/ArnavSingha/Patchproof/.github/workflows/ci.yml"
    public_key_pem: str
    signature_base64: str        # Ed25519 signature over canonical payload
```

#### Key Management Protocol
1. **Local CLI:**
   `patchproof keygen [--out-dir ~/.patchproof]` generates an Ed25519 keypair.
2. **CI Mode (GitHub Actions):**
   PatchProof requests an OIDC token via `ACTIONS_ID_TOKEN_REQUEST_URL` and signs using in-toto v1.0 provenance.
3. **Verification Command:**
   `patchproof verify-cert cert.json [--public-key pubkey.pem]` verifies the signature with zero network calls and zero shared secrets.

---

### Subsystem 4: Monorepo Sparse Scoped Extraction (`sparse_tree.py`)

#### Objective
Reduce staging latency in large multi-GB monorepos from minutes to milliseconds.

#### Interface Specification
```python
def compute_minimal_common_ancestor(modified_paths: Sequence[str]) -> str | None:
    """Calculate the shallowest common directory among all modified files in a patch.
    
    Example:
        ['services/auth/models.py', 'services/auth/tests/test_login.py']
        -> 'services/auth'
    """
    if not modified_paths:
        return None
    split_parts = [p.replace('\\', '/').split('/') for p in modified_paths]
    common = []
    for segments in zip(*split_parts):
        if len(set(segments)) == 1:
            common.append(segments[0])
        else:
            break
    # Return directory path, excluding the final filename
    return '/'.join(common[:-1]) if len(common) > 1 else None
```

#### Extraction Command Rule
If `common_ancestor` is detected (e.g. `services/auth`):
```bash
git archive HEAD:services/auth/ | tar -x -C <staging_dir>
```
Only the relevant microservice subtree is populated, guaranteeing sub-second staging setup regardless of total monorepo size.

---

## 3. Phased Implementation Roadmap

| Phase | Milestone | Deliverables | Target Verification |
| :---: | :---: | :--- | :--- |
| **Phase 1** | **P0: Security Gate** | Container runtime assertion tracking (`runtime_guard.py`) + dynamic tampering reconciler in `classifier.py` | Unit tests injecting dynamic `getattr()` and `builtins` bypasses; verify 100% detection |
| **Phase 2** | **P0: Enterprise Infra**| Ephemeral internal bridge driver (`network_bridge.py`) + Docker network lifecycle management | Integration tests launching local sidecar mock; verify zero external network leak |
| **Phase 3** | **P1: Trust & Crypto** | Ed25519 asymmetric signing engine (`asymmetric.py`) + `patchproof keygen` CLI | Verification tests validating untampered signatures and rejecting modified payloads |
| **Phase 4** | **P1: Scale & Speed** | Monorepo common directory extractor (`sparse_tree.py`) + scoped `git archive` staging | Benchmark tests measuring staging time on 10,000-file mock repository (< 200ms) |

---

## 4. Quality Gate Adherence

Per [AGENTS.md](../../AGENTS.md):
- **TDD:** Failing unit and adversarial tests must precede code implementation in every phase.
- **Strict Typing:** All new code must pass `mypy --strict src/patchproof tests/` with zero untyped definitions and zero `Any`.
- **Zero Ruff Errors:** Code must pass `ruff check .` with zero errors and zero warnings.
- **Fail-Closed Boundary:** Any unhandled exception or ambiguous state in security/isolation checks must terminate with exit code `1` or `2` (REJECTED / SUSPICIOUS).
