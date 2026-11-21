# PatchProof: Architecture Review & Pre-Implementation Refinement

> **Author:** Independent Principal Systems & Security Architect  
> **Review Scope:** Critical review of initial Aider + PatchProof proposal  
> **Repository Foundation:** [Aider Upstream](https://github.com/Aider-AI/aider) (`aider-chat` v0.80+)  
> **Target Subsystem:** PatchProof (Deterministic Verification Engine)  
> **Environment Target:** Windows 11 Pro/Home (Build 26300), AMD Ryzen 5000, 16 GB RAM, NVIDIA GTX 1650 4GB  
> **Mode:** Architecture Review & Planning  
> **Status:** Completed & Approved  

---

## 1. Security Architecture Correction & Disambiguation

Our initial report blurred the line between workspace state management and security boundaries. A **Git worktree is NOT a security sandbox**. We must explicitly decouple and define each isolation layer.

### 1.1 Decomposition of Isolation Layers

| Layer | Mechanism | What It Protects | What It Does NOT Protect |
| :--- | :--- | :--- | :--- |
| **Git Worktree Isolation** | `git worktree add <path>` | Working tree files, uncommitted changes, Git `HEAD` on host. | **Zero security protection.** Code executed in a worktree runs on the host OS with the developer's full user privileges, access to host filesystem, network, and environment variables. |
| **Process Isolation** | OS Subprocess (`subprocess.Popen` / Job Objects) | Prevents target process from directly corrupting parent process memory. | Host filesystem, network sockets, child process spawning, host user credentials. |
| **Filesystem Isolation** | Container Bind Mounts (Read-Only / OverlayFS / TempFS) | Host root files, parent directories, SSH keys, cloud credentials (`~/.aws`, `~/.config`), Windows registry. | Malicious files written inside the mounted ephemeral workspace folder itself during execution. |
| **Network Isolation** | Container Network Namespaces (`--network none`) | Prevents data exfiltration (sending source code/API keys out), remote shell callbacks, downloading malicious payloads. | Local UNIX domain sockets or IPC mounts if improperly mapped into the container. |
| **Resource Isolation** | cgroups / Docker Resource Flags (`--cpus`, `--memory`) | Prevents CPU starvation, fork bombs, and Out-Of-Memory (OOM) freezing on host. | Internal container resource starvation if limits are set too low for valid test suites. |
| **Container Isolation** | OCI Linux Container via Docker / WSL2 backend | Combines Filesystem, Network, and Resource isolation under unprivileged UID namespaces. | Kernel exploits (shared host/WSL2 kernel), Docker daemon socket exposure if mounted. |
