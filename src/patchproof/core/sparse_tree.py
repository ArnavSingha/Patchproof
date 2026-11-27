"""Monorepo Sparse Tree and Scoped Staging Subsystem.

Calculates minimal common ancestor directories for patches to enable fast scoped
git archive extraction in multi-gigabyte monorepos.
"""

from __future__ import annotations

import re
from collections.abc import Sequence


def compute_minimal_common_ancestor(modified_paths: Sequence[str]) -> str | None:
    """Calculate the shallowest common directory among all modified files in a patch.

    Example:
        ['services/auth/models.py', 'services/auth/tests/test_login.py']
        -> 'services/auth'
        ['foo.py', 'bar.py']
        -> None (root directory)
    """
    clean_paths: list[list[str]] = []
    for p in modified_paths:
        normalized = p.replace("\\", "/").strip("/")
        if not normalized or normalized in ("/dev/null", "dev/null"):
            continue
        parts = normalized.split("/")
        # Path must have at least 2 parts (dir + filename) to have a parent directory
        if len(parts) > 1:
            clean_paths.append(parts[:-1])  # Keep only directory segments
        else:
            # File is in root directory; no common non-root ancestor can exist
            return None

    if not clean_paths:
        return None

    common_segments: list[str] = []
    for segments in zip(*clean_paths, strict=False):
        if len(set(segments)) == 1:
            common_segments.append(segments[0])
        else:
            break

    return "/".join(common_segments) if common_segments else None


def extract_patch_modified_paths(patch_content: str) -> list[str]:
    """Extract list of target file paths modified by a unified diff or git patch."""
    paths: list[str] = []
    for line in patch_content.splitlines():
        if line.startswith("+++ "):
            # Strip +++ and b/ prefix if present
            raw_target = line[4:].strip()
            # Handle timestamps e.g. +++ b/file.py \t 2026-01-01
            target = raw_target.split("\t")[0].strip()
            if target.startswith("b/"):
                target = target[2:]
            if target and target != "/dev/null" and target not in paths:
                paths.append(target)
        elif line.startswith("diff --git "):
            match = re.match(r"diff --git a/(.*?) b/(.*)", line)
            if match:
                b_path = match.group(2).strip()
                if b_path and b_path != "/dev/null" and b_path not in paths:
                    paths.append(b_path)

    return paths


def should_use_sparse_extraction(
    modified_paths: Sequence[str], min_depth: int = 1
) -> str | None:
    """Determine if a patch qualifies for scoped monorepo sparse extraction.

    Args:
        modified_paths: List of relative file paths in patch.
        min_depth: Minimum directory hierarchy depth required to activate scoping.

    Returns:
        str | None: Subtree directory path if applicable, None otherwise.
    """
    ancestor = compute_minimal_common_ancestor(modified_paths)
    if not ancestor:
        return None

    depth = len(ancestor.split("/"))
    if depth >= min_depth:
        return ancestor
    return None
