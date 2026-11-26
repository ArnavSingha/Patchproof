"""Unit tests for AST-based predictive test slicing."""

from __future__ import annotations

import pathlib

from patchproof.core.normalizer import parse_patch
from patchproof.core.slicer import slice_test_suite


def create_dummy_repo(tmp_path: pathlib.Path) -> pathlib.Path:
    """Helper to populate a sample repository with source and test files."""
    src_dir = tmp_path / "src" / "pkg"
    src_dir.mkdir(parents=True)
    (src_dir / "__init__.py").write_text("", encoding="utf-8")
    (src_dir / "billing.py").write_text(
        "def calculate_tax(x: float) -> float:\n    return x * 0.1\n",
        encoding="utf-8",
    )
    (src_dir / "auth.py").write_text(
        "def check_auth() -> bool:\n    return True\n",
        encoding="utf-8",
    )

    tests_dir = tmp_path / "tests"
    tests_dir.mkdir(parents=True)
    (tests_dir / "test_billing.py").write_text(
        "from pkg.billing import calculate_tax\n\n"
        "def test_tax():\n    assert calculate_tax(10) == 1.0\n",
        encoding="utf-8",
    )
    (tests_dir / "test_auth.py").write_text(
        "from pkg.auth import check_auth\n\ndef test_auth():\n    assert check_auth() is True\n",
        encoding="utf-8",
    )
    return tmp_path


def test_slice_selects_direct_importing_test(tmp_path: pathlib.Path) -> None:
    """Modifying billing.py should select test_billing.py and ignore test_auth.py."""
    repo = create_dummy_repo(tmp_path)
    diff = """--- a/src/pkg/billing.py
+++ b/src/pkg/billing.py
@@ -1,2 +1,2 @@
 def calculate_tax(x: float) -> float:
-    return x * 0.1
+    return x * 0.2
"""
    patch = parse_patch(diff)
    result = slice_test_suite(repo, patch)

    assert not result.is_full_suite_fallback
    assert len(result.selected_test_paths) == 1
    assert any("test_billing.py" in p for p in result.selected_test_paths)
    assert not any("test_auth.py" in p for p in result.selected_test_paths)


def test_slice_falls_back_on_config_file_change(tmp_path: pathlib.Path) -> None:
    """Touching non-python configuration files should trigger full-suite fallback."""
    repo = create_dummy_repo(tmp_path)
    (repo / "pyproject.toml").write_text("[project]\nname='pkg'\n", encoding="utf-8")
    diff = """--- a/pyproject.toml
+++ b/pyproject.toml
@@ -1,2 +1,2 @@
 [project]
-name='pkg'
+name='pkg-new'
"""
    patch = parse_patch(diff)
    result = slice_test_suite(repo, patch)

    assert result.is_full_suite_fallback
    assert "configuration" in (result.fallback_reason or "").lower()
    assert result.selected_test_paths == ()


def test_slice_direct_test_file_edit(tmp_path: pathlib.Path) -> None:
    """Modifying a test file directly selects that test file."""
    repo = create_dummy_repo(tmp_path)
    diff = """--- a/tests/test_auth.py
+++ b/tests/test_auth.py
@@ -3,2 +3,2 @@
 def test_auth():
-    assert check_auth() is True
+    assert check_auth()
"""
    patch = parse_patch(diff)
    result = slice_test_suite(repo, patch)

    assert not result.is_full_suite_fallback
    assert len(result.selected_test_paths) == 1
    assert any("test_auth.py" in p for p in result.selected_test_paths)


def test_slice_empty_or_unmatched_falls_back(tmp_path: pathlib.Path) -> None:
    """Touching a file with no corresponding tests falls back safely to full suite."""
    repo = create_dummy_repo(tmp_path)
    (repo / "src" / "pkg" / "orphan.py").write_text("x = 1\n", encoding="utf-8")
    diff = """--- a/src/pkg/orphan.py
+++ b/src/pkg/orphan.py
@@ -1,1 +1,1 @@
-x = 1
+x = 2
"""
    patch = parse_patch(diff)
    result = slice_test_suite(repo, patch)

    assert result.is_full_suite_fallback
    assert "no direct or importing test files" in (result.fallback_reason or "").lower()
