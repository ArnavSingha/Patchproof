# Contributing to PatchProof

Thank you for your interest in contributing to **PatchProof**! PatchProof is designed to provide deterministic code-patch verification, sandboxing, and regression detection for AI-assisted engineering.

We welcome bug reports, feature proposals, and pull requests.

---

## Development Setup

1. **Prerequisites:**
   - Python 3.11 or newer
   - Git
   - Docker (optional, required for running sandbox container tests)

2. **Clone and Install in Editable Mode:**
   ```bash
   git clone https://github.com/ArnavSingha/Patchproof.git
   cd patchproof
   python -m venv .venv
   source .venv/bin/activate  # On Windows: .venv\Scripts\activate
   pip install -e ".[dev]"
   ```

---

## Quality Gates & Verification

Before submitting a pull request, ensure all quality gates pass locally:

```bash
# 1. Run the test suite
pytest

# 2. Check formatting and lint rules
ruff check .

# 3. Verify strict static type annotations
mypy src/ tests/
```

Every new feature or bugfix should include corresponding unit or integration tests under `tests/`.

---

## Pull Request Guidelines

1. **Topic Branch:** Create your branch from `main` or `master` (e.g., `git checkout -b fix/search-replace-bounds`).
2. **Atomic Commits:** Make small, clear commits with descriptive commit messages.
3. **No Private/Generated Artifacts:** Ensure temporary test outputs, environment variables, or IDE files are not included.
4. **Pass CI:** All automated checks in GitHub Actions must pass before merging.

---

## Code of Conduct

Please help us maintain a respectful, inclusive, and professional environment. Treat all contributors and users with kindness and constructive feedback.
