"""Predictive Test Slicing via Python AST Import and Dependency Graph Analysis.

Selects the minimal impacted test closure for a patch to achieve sub-second verification,
falling back safely to the full test suite when non-Python files or configurations change.
"""

from __future__ import annotations

import ast
import logging
import pathlib
import time
from dataclasses import dataclass

from patchproof.core.ast_analyzer import _extract_imports
from patchproof.models.patch import CanonicalPatch

logger = logging.getLogger(__name__)

NON_PYTHON_EXTENSIONS = (
    ".toml",
    ".yaml",
    ".yml",
    ".json",
    ".ini",
    ".cfg",
    ".txt",
    ".md",
    ".sh",
    ".bat",
    ".dockerfile",
)


@dataclass(frozen=True)
class TestSliceResult:
    """Outcome of predictive test slicing."""

    __test__ = False

    selected_test_paths: tuple[str, ...]
    is_full_suite_fallback: bool
    fallback_reason: str | None = None
    impacted_modules: tuple[str, ...] = ()
    duration_ms: float = 0.0

    @property
    def has_targeted_selection(self) -> bool:
        """Return True if specific test targets were isolated."""
        return not self.is_full_suite_fallback and len(self.selected_test_paths) > 0


def _derive_module_candidates(rel_path_str: str) -> tuple[str, ...]:
    """Derive possible Python import module paths from relative file path."""
    clean = rel_path_str.replace("\\", "/").strip("/")
    if clean.endswith(".py"):
        clean = clean[:-3]
    parts = [p for p in clean.split("/") if p and p != "__init__"]
    candidates: list[str] = []

    # 1. Full dotted path
    candidates.append(".".join(parts))

    # 2. Path without leading 'src'
    if parts and parts[0] == "src":
        candidates.append(".".join(parts[1:]))

    # 3. Base filename only
    if parts:
        candidates.append(parts[-1])

    return tuple(sorted({p for p in candidates if p}))


def slice_test_suite(
    repo_path: pathlib.Path,
    patch: CanonicalPatch,
    tests_root: str = "tests",
) -> TestSliceResult:
    """Analyze patch impact and return targeted test suite slice."""
    start_time = time.perf_counter()

    # 1. Check for non-Python or configuration file changes
    for delta in patch.file_deltas:
        norm_path = delta.path.lower().replace("\\", "/")
        if (
            any(norm_path.endswith(ext) for ext in NON_PYTHON_EXTENSIONS)
            or "docker" in norm_path
            or norm_path.startswith(".github/")
        ):
            elapsed_ms = (time.perf_counter() - start_time) * 1000.0
            reason = (
                f"Configuration or non-Python change in '{delta.path}' "
                "requires full suite validation"
            )
            return TestSliceResult(
                selected_test_paths=(),
                is_full_suite_fallback=True,
                fallback_reason=reason,
                duration_ms=elapsed_ms,
            )

    selected_tests: set[str] = set()
    impacted_modules: set[str] = set()
    source_deltas: list[str] = []

    tests_dir = repo_path / tests_root

    # 2. Separate direct test modifications from source modifications
    for delta in patch.file_deltas:
        norm_path = delta.path.replace("\\", "/").strip("/")
        if (
            norm_path.startswith(f"{tests_root}/")
            or "test_" in norm_path
            or "_test.py" in norm_path
        ):
            # Direct test edit
            selected_tests.add(norm_path)
        else:
            source_deltas.append(norm_path)
            impacted_modules.update(_derive_module_candidates(norm_path))

    # 3. If source modules were modified, scan tests directory for importing test files
    if source_deltas and tests_dir.is_dir():
        for test_file in tests_dir.rglob("*.py"):
            if not test_file.is_file():
                continue
            rel_test_str = str(test_file.relative_to(repo_path)).replace("\\", "/")
            try:
                content = test_file.read_text(encoding="utf-8")
                tree = ast.parse(content, filename=str(test_file))
            except (SyntaxError, UnicodeDecodeError, OSError):
                continue

            file_imports = _extract_imports(tree)
            matched = False
            for imp in file_imports:
                for mod in impacted_modules:
                    if imp == mod or imp.startswith(f"{mod}.") or mod.endswith(f".{imp}"):
                        matched = True
                        break
                if matched:
                    break

            # Check filename counterpart heuristic (e.g. test_billing.py for billing.py)
            if not matched:
                for s in source_deltas:
                    base_stem = pathlib.Path(s).stem
                    if test_file.stem in (f"test_{base_stem}", f"{base_stem}_test"):
                        matched = True
                        break

            if matched:
                selected_tests.add(rel_test_str)

    elapsed_ms = (time.perf_counter() - start_time) * 1000.0

    if not selected_tests:
        return TestSliceResult(
            selected_test_paths=(),
            is_full_suite_fallback=True,
            fallback_reason="No direct or importing test files found for modified modules",
            impacted_modules=tuple(sorted(impacted_modules)),
            duration_ms=elapsed_ms,
        )

    return TestSliceResult(
        selected_test_paths=tuple(sorted(selected_tests)),
        is_full_suite_fallback=False,
        impacted_modules=tuple(sorted(impacted_modules)),
        duration_ms=elapsed_ms,
    )
