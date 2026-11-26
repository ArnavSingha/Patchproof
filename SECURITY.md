# Security Policy

## Reporting Security Vulnerabilities

The PatchProof team takes security seriously. Because PatchProof inspects and executes untrusted code diffs inside sandboxes, sandbox isolation and boundary enforcement are critical.

If you believe you have discovered a vulnerability, bypass, or security risk in PatchProof, please do **not** open a public issue.

### Reporting Process

1. Email your findings directly to the project maintainers or report via GitHub Private Vulnerability Reporting on the repository.
2. Please include:
   - A description of the issue and potential impact.
   - Minimal reproduction steps (e.g., sample malicious patch or payload).
   - Any proposed mitigations or fixes.
3. Maintainers will acknowledge your report within 48 hours and coordinate remediation before public release.

---

## Threat Model & Sandboxing Note

PatchProof provides isolated container execution for untrusted patches using Docker with:
- Disabled network access (`--network none`)
- Read-only filesystem mounts where appropriate
- Dropped privileges and resource caps

Please note that running untrusted patches without container isolation (`--no-sandbox` mode) executes code with host privileges. Always use containerized sandboxing when evaluating untrusted AI patches.
