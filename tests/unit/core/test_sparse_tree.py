"""Unit tests for Monorepo Sparse Tree and Common Ancestor Extraction."""

from __future__ import annotations

from patchproof.core.sparse_tree import (
    compute_minimal_common_ancestor,
    extract_patch_modified_paths,
    should_use_sparse_extraction,
)


def test_compute_minimal_common_ancestor_microservice() -> None:
    """Computes common directory for multi-file patch in subproject."""
    paths = [
        "services/auth/models.py",
        "services/auth/views.py",
        "services/auth/tests/test_login.py",
    ]
    ancestor = compute_minimal_common_ancestor(paths)
    assert ancestor == "services/auth"


def test_compute_minimal_common_ancestor_single_nested_file() -> None:
    """Single nested file returns its parent directory."""
    paths = ["packages/frontend/src/index.ts"]
    ancestor = compute_minimal_common_ancestor(paths)
    assert ancestor == "packages/frontend/src"


def test_compute_minimal_common_ancestor_root_files() -> None:
    """Files in root directory have no common sub-directory."""
    paths = ["setup.py", "README.md", "pyproject.toml"]
    assert compute_minimal_common_ancestor(paths) is None


def test_compute_minimal_common_ancestor_mixed_root_and_nested() -> None:
    """Mix of root and nested files has no common ancestor directory."""
    paths = ["services/auth/models.py", "pyproject.toml"]
    assert compute_minimal_common_ancestor(paths) is None


def test_compute_minimal_common_ancestor_empty_or_null() -> None:
    """Empty path list returns None."""
    assert compute_minimal_common_ancestor([]) is None
    assert compute_minimal_common_ancestor(["/dev/null"]) is None


def test_extract_patch_modified_paths_git_diff() -> None:
    """Extracts target file paths from standard unified diff."""
    diff = (
        "diff --git a/services/payment/charge.py b/services/payment/charge.py\n"
        "--- a/services/payment/charge.py\n"
        "+++ b/services/payment/charge.py\n"
        "@@ -1,3 +1,3 @@\n"
        "-old\n"
        "+new\n"
        "diff --git a/services/payment/tests/test_charge.py "
        "b/services/payment/tests/test_charge.py\n"
        "--- a/services/payment/tests/test_charge.py\n"
        "+++ b/services/payment/tests/test_charge.py\n"
        "@@ -1,3 +1,3 @@\n"
        "-old\n"
        "+new\n"
    )
    paths = extract_patch_modified_paths(diff)
    assert paths == ["services/payment/charge.py", "services/payment/tests/test_charge.py"]


def test_should_use_sparse_extraction() -> None:
    """Returns ancestor when depth requirement is satisfied."""
    paths = ["services/analytics/pipeline/agg.py", "services/analytics/pipeline/sink.py"]
    assert should_use_sparse_extraction(paths, min_depth=1) == "services/analytics/pipeline"
    assert should_use_sparse_extraction(paths, min_depth=4) is None
