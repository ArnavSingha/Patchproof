# PatchProof

**Deterministic Code-Patch Verification & Reliability Engine for AI-Assisted Engineering**

PatchProof is an independent verification subsystem designed to evaluate, test, and validate AI-generated code patches using actual execution evidence, isolated sandboxes, test-integrity analysis, and regression verification.

---

## Milestone M1: Core Patch Normalizer

Milestone M1 establishes the fundamental data structures and the canonical patch normalizer:

- **Immutable Domain Models:** `CanonicalPatch`, `FileDelta`, `PatchHunk`, `PatchType`, `ChangeType`.
- **Deterministic Normalizer:** `parse_patch(raw_diff: str) -> CanonicalPatch` parses unified diffs, normalizes line endings (`CRLF` -> `LF`), validates hunk line counts, and tracks single-file, multi-file, additions, and deletions.
- **Fail-Fast Validation:** Detects malformed diffs, missing headers, or corrupt line counts via `InvalidPatchFormatError`.

---

## Installation & Development

```bash
# Clone and setup
pip install -e ".[dev]"

# Run tests
pytest

# Static analysis
ruff check .
mypy src/patchproof tests/
```

---

## Architecture Blueprint

Detailed architectural specifications and design records are located in:
- `docs/ARCHITECTURE_REPORT.md` (Initial comprehensive upstream investigation)
- `docs/PATCHPROOF_ARCHITECTURE_REFINEMENT.md` (Refined threat model and verification lifecycle)
