# PatchProof Dependency Management Strategy

**Document Version:** 1.0.0-m4.1  
**Status:** Approved & Binding  
**Applies To:** PatchProof Milestone M4.1+  

---

## 1. Executive Summary

PatchProof is an independent, deterministic verification engine designed to operate safely on untrusted code patches produced by AI coding agents (such as upstream Aider). A fundamental tenet of PatchProof is **absolute isolation**: all verification runs execute inside an unprivileged container with network access completely disabled (`--network none`).

Because the sandbox operates with `--network none`, containerized test runs cannot download or install packages at runtime. This document defines PatchProof's architectural dependency management model across three operational tiers:
1. **Tier 1:** Standard Out-of-the-Box Runner (`patchproof-runner-python:3.11`).
2. **Tier 2:** Custom Prebuilt Project Runners (`--image <custom-image>`).
3. **Tier 3 (Future M5+):** Automated Deterministic Environment Synthesis.

---

## 2. Standard Runner Capabilities and Limitations

### 2.1 The Standard Runner (`patchproof-runner-python:3.11`)

PatchProof provides a hardened standard runner built from [`docker/runner.Dockerfile`](file:///docker/runner.Dockerfile). Developers instantiate this image locally using:

```bash
patchproof setup-runner
```

#### Included Capabilities:
- **Base Image:** Pinned `python:3.11-slim-bookworm` (Debian Bookworm).
- **Core Runtime:** Python 3.11 standard library.
- **Test Framework:** `pytest>=8.0.0`, `pytest-timeout`, `pytest-xdist`.
- **System Utilities:** Standard POSIX core utilities (`bash`, `sh`, `find`, `grep`, `coreutils`).
- **Security Context:** Unprivileged user `10001:10001`, dropped capabilities (`--cap-drop ALL`), `no-new-privileges`, read-only rootfs (`--read-only`), ephemeral writable `/workspace` and `/tmp`.

#### Intentional Exclusions & Limitations:
- **No Third-Party Packages:** Third-party libraries (`numpy`, `pandas`, `requests`, `fastapi`, `django`, `cryptography`, etc.) are **NOT** pre-installed in the standard runner.
- **Pure-Python & Standard Library Scope:** Projects with tests depending strictly on Python standard library modules or self-contained vendored packages will run immediately without extra configuration. Projects importing uninstalled third-party libraries will encounter `ModuleNotFoundError` during pytest test collection.

---

## 3. Projects Requiring Third-Party Dependencies

When a repository relies on external dependencies, running against the minimal standard runner will result in collection errors. PatchProof resolves this through **Tier 2: Custom Prebuilt Runners**.

### 3.1 Custom Prebuilt Runner Workflow

A developer or team builds a container image containing their project's exact dependencies, packages, and environment.

#### Example `Dockerfile.test`:
```dockerfile
# Start from PatchProof runner or compatible base
FROM python:3.11-slim-bookworm

# Install system libraries needed by project
RUN apt-get update && apt-get install -y --no-install-recommends \
        libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# Install project dependencies into container Python environment
COPY requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt pytest pytest-timeout

# Ensure unprivileged user 10001:10001 exists and owns /workspace
RUN groupadd -g 10001 patchproof \
    && useradd -u 10001 -g 10001 -m -s /bin/bash patchproof \
    && mkdir -p /workspace /tmp \
    && chown -R patchproof:patchproof /workspace /tmp \
    && chmod 1777 /tmp /workspace

USER 10001:10001
WORKDIR /workspace
```

#### Build and Verify with PatchProof:
```bash
# 1. Build custom image locally
docker build -t my-app-runner:latest -f Dockerfile.test .

# 2. Instruct PatchProof to use custom image
patchproof verify feature.patch --image my-app-runner:latest --repo .
```

PatchProof executes the comparative evaluation inside `my-app-runner:latest` while applying all mandatory security invariants (`--network none`, resource caps, staging isolation, `.git` masking).

---

## 4. Security Prohibitions & Architectural Decisions

### 4.1 Why PatchProof Prohibits `pip install` During Verification

Running `pip install` (or `poetry install`, `uv pip install`, etc.) inside the verification sandbox is strictly prohibited for three reasons:

1. **Network Boundary Enforcement:** Enabling network access inside the container would allow malicious patches or adversarial dependencies to exfiltrate host environment clues, execute server-side request forgery (SSRF), or communicate with command-and-control servers.
2. **Deterministic Reproducibility:** Dynamic package resolution introduces nondeterminism. If an upstream index releases a new package version or suffers an outage between the baseline and patched run, the verification verdict becomes invalid.
3. **Supply-Chain Poisoning:** AI coding agents frequently suggest new or typo-squatted dependencies ("dependency hallucination"). Allowing unauthenticated package installation during verification invites arbitrary remote code execution at the container boundary.

### 4.2 Why PatchProof Prohibits Blind Execution of Repository Dockerfiles

Many repositories contain a `Dockerfile` or `docker-compose.yml`. PatchProof will **never automatically build or execute** a repository Dockerfile without explicit developer configuration because:

1. **Arbitrary Code Execution in Build Context:** A malicious patch could modify `Dockerfile` with RUN directives that exfiltrate data or mount privileged host directories during `docker build`.
2. **Missing Security Invariants:** Repository Dockerfiles often run as `root` (UID 0), expose default bridges, or omit security profiles required by PatchProof.
3. **Build Cache Pollution:** Automatic builds can pollute the developer's local Docker cache with untrusted image layers.

Developers must intentionally build or approve their custom runner images prior to passing `--image` to PatchProof.

---

## 5. Roadmap: Automated Dependency Synthesis (Milestone M5+)

To improve developer experience without compromising security, future milestones will introduce structured dependency synthesis:

- **M5.1 — Dependency Manifest Inspector:** PatchProof CLI will inspect `pyproject.toml`, `requirements.txt`, or `setup.cfg` and generate a proposed `Dockerfile.patchproof` for the developer to review and build with one command.
- **M5.2 — Hermetic Wheel Caches:** Mount-only read-only host directories containing pre-downloaded, SHA256-verified wheels for offline installation into ephemeral tmpfs layers.
- **M5.3 — Multi-Runtime Runners:** Official standard runner images for Node.js (TypeScript/Jest), Go, and Rust.
