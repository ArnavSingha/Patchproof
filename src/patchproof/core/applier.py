"""Safe, isolated application of canonical patches and diffs to staging workspaces."""

from __future__ import annotations

import pathlib
from collections.abc import Sequence

from patchproof.core.normalizer import parse_patch
from patchproof.core.search_replace import SEARCH_START, parse_search_replace
from patchproof.exceptions import (
    InvalidPatchFormatError,
    PatchApplicationError,
    SandboxSecurityError,
)
from patchproof.models.patch import (
    CanonicalPatch,
    ChangeType,
    PatchHunk,
    PatchType,
)


def _resolve_and_validate_path(target_base: pathlib.Path, rel_path: str) -> pathlib.Path:
    """Resolve file path within target_base, rejecting any directory traversal or symlinks."""
    target_candidate = target_base / rel_path
    if target_candidate.is_symlink():
        raise SandboxSecurityError(f"Patch cannot target a symbolic link: {rel_path}")

    resolved = target_candidate.resolve()
    if resolved == target_base:
        raise SandboxSecurityError(f"Patch target cannot be the workspace root: {rel_path}")
    try:
        resolved.relative_to(target_base)
    except ValueError as err:
        raise SandboxSecurityError(f"Patch path traversal attempted: {rel_path}") from err
    return resolved


def _apply_hunks_to_lines(
    lines: list[str], hunks: Sequence[PatchHunk], file_path: str
) -> list[str]:
    """Apply sorted unified diff hunks to a list of file lines."""
    result = list(lines)
    offset = 0

    sorted_hunks = sorted(hunks, key=lambda h: h.old_start)
    for hunk in sorted_hunks:
        expected_start = max(0, hunk.old_start - 1 + offset)
        expected_old = [
            line_str[1:]
            for line_str in hunk.lines
            if line_str.startswith(" ") or line_str.startswith("-")
        ]
        new_slice = [
            line_str[1:]
            for line_str in hunk.lines
            if line_str.startswith(" ") or line_str.startswith("+")
        ]

        actual_slice = result[expected_start : expected_start + len(expected_old)]
        if actual_slice != expected_old:
            raise PatchApplicationError(
                f"Context mismatch in {file_path} at hunk starting at line {hunk.old_start}"
            )

        result[expected_start : expected_start + len(expected_old)] = new_slice
        offset += len(new_slice) - len(expected_old)

    return result


def apply_patch(
    patch: CanonicalPatch | str,
    target_dir: str | pathlib.Path,
) -> list[str]:
    """Apply a CanonicalPatch or raw diff string cleanly to an ephemeral staging workspace.

    Returns:
        List of relative repository paths modified, added, or deleted.

    Raises:
        PatchApplicationError: If patch formatting is invalid, context fails to match,
            or files are missing/unexpectedly present.
        SandboxSecurityError: If any path attempts to escape the staging workspace.
    """
    target_base = pathlib.Path(target_dir).resolve()
    if not target_base.exists() or not target_base.is_dir():
        raise PatchApplicationError(
            f"Staging directory does not exist or is not a directory: {target_dir}"
        )

    # Parse raw patch if supplied as string
    canonical_patch: CanonicalPatch
    if isinstance(patch, str):
        try:
            if SEARCH_START in patch:
                canonical_patch = parse_search_replace(patch)
            else:
                canonical_patch = parse_patch(patch)
        except InvalidPatchFormatError as err:
            raise PatchApplicationError(f"Cannot apply invalid patch format: {err}") from err
    else:
        canonical_patch = patch

    modified_paths: list[str] = []

    for delta in canonical_patch.file_deltas:
        target_file = _resolve_and_validate_path(target_base, delta.path)

        if delta.change_type == ChangeType.ADD:
            if target_file.exists():
                raise PatchApplicationError(f"Cannot create file that already exists: {delta.path}")
            target_file.parent.mkdir(parents=True, exist_ok=True)
            added_lines = [
                line[1:] for hunk in delta.hunks for line in hunk.lines if line.startswith("+")
            ]
            content = "\n".join(added_lines) + ("\n" if added_lines else "")
            target_file.write_text(content, encoding="utf-8")
            modified_paths.append(delta.path)

        elif delta.change_type == ChangeType.DELETE:
            if not target_file.exists():
                raise PatchApplicationError(f"Cannot delete non-existent file: {delta.path}")
            target_file.unlink()
            modified_paths.append(delta.path)

        elif delta.change_type == ChangeType.MODIFY:
            if not target_file.exists():
                raise PatchApplicationError(f"File to modify does not exist: {delta.path}")

            content = target_file.read_text(encoding="utf-8")

            if canonical_patch.patch_type == PatchType.SEARCH_REPLACE:
                for hunk in delta.hunks:
                    search_lines = [
                        line_str[1:] for line_str in hunk.lines if line_str.startswith("-")
                    ]
                    replace_lines = [
                        line_str[1:] for line_str in hunk.lines if line_str.startswith("+")
                    ]
                    search_text = "\n".join(search_lines)
                    replace_text = "\n".join(replace_lines)

                    if search_text not in content:
                        raise PatchApplicationError(
                            f"SEARCH block not found in {delta.path}: {search_text[:40]}..."
                        )
                    content = content.replace(search_text, replace_text, 1)

                target_file.write_text(content, encoding="utf-8")
            else:
                lines = content.splitlines(keepends=False)
                updated_lines = _apply_hunks_to_lines(lines, delta.hunks, delta.path)
                new_content = "\n".join(updated_lines) + ("\n" if updated_lines else "")
                target_file.write_text(new_content, encoding="utf-8")

            modified_paths.append(delta.path)

    return modified_paths
