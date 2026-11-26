"""Unified diff parsing and normalization into canonical patch representations."""

from __future__ import annotations

import re

from patchproof.exceptions import InvalidPatchFormatError
from patchproof.models.patch import (
    CanonicalPatch,
    ChangeType,
    FileDelta,
    PatchHunk,
    PatchType,
)

HUNK_HEADER_RE = re.compile(
    r"^@@ -(?P<old_start>\d+)(?:,(?P<old_lines>\d+))? "
    r"\+(?P<new_start>\d+)(?:,(?P<new_lines>\d+))? @@(?P<header_rest>.*)$"
)

GIT_DIFF_RE = re.compile(
    r'^diff --git (?:"(?P<old_quoted>[^"]+)"|(?P<old_unquoted>\S+)) '
    r'(?:"(?P<new_quoted>[^"]+)"|(?P<new_unquoted>\S+))$'
)

BINARY_DIFF_RE = re.compile(
    r'^Binary files (?:"(?P<old_quoted>[^"]+)"|(?P<old_unquoted>\S+)) '
    r'and (?:"(?P<new_quoted>[^"]+)"|(?P<new_unquoted>\S+)) differ$'
)


def _clean_path(path: str) -> str:
    """Normalize file path by stripping leading a/ or b/ prefixes, quotes, and timestamps."""
    clean = path.split("\t")[0].strip()
    clean = clean.strip("\"'")
    if clean == "/dev/null":
        return "/dev/null"
    if clean.startswith("a/") or clean.startswith("b/"):
        return clean[2:]
    return clean


class _UnifiedDiffParser:
    """Internal state-machine parser for unified diff format."""

    def __init__(self, raw_diff: str) -> None:
        self.raw_diff = raw_diff.replace("\r\n", "\n").replace("\r", "\n")
        self.lines: list[str] = self.raw_diff.split("\n")
        if self.lines and self.lines[-1] == "":
            self.lines.pop()

        self.idx = 0
        self.total = len(self.lines)

    def parse(self) -> CanonicalPatch:
        if not self.raw_diff.strip():
            raise InvalidPatchFormatError("Patch content is empty or contains only whitespace")

        file_deltas: list[FileDelta] = []

        while self.idx < self.total:
            line = self.lines[self.idx]

            if not (
                line.startswith("diff --git")
                or line.startswith("--- ")
                or line.startswith("Index: ")
                or line.startswith("Binary files ")
            ):
                self.idx += 1
                continue

            start_idx = self.idx
            delta = self._parse_file_entry()
            if delta is not None:
                file_deltas.append(delta)
            elif self.idx == start_idx:
                # Guaranteed forward progress to prevent infinite loops
                self.idx += 1

        if not file_deltas:
            raise InvalidPatchFormatError(
                "No valid unified diff file headers found (expected '---'/'+++' or 'diff --git')"
            )

        return CanonicalPatch(
            patch_type=PatchType.UNIFIED_DIFF,
            file_deltas=tuple(file_deltas),
            raw_diff=self.raw_diff,
        )

    def _parse_file_entry(self) -> FileDelta | None:
        old_path: str | None = None
        new_path: str | None = None
        is_new_mode = False
        is_deleted_mode = False
        is_mode_change = False
        is_binary = False

        while self.idx < self.total:
            line = self.lines[self.idx]

            if line.startswith("diff --git"):
                match = GIT_DIFF_RE.match(line)
                if match:
                    old_path = match.group("old_quoted") or match.group("old_unquoted")
                    new_path = match.group("new_quoted") or match.group("new_unquoted")
                self.idx += 1
                continue

            if line.startswith("Index: "):
                idx_path = line[7:].strip().strip("\"'")
                if not old_path:
                    old_path = idx_path
                if not new_path:
                    new_path = idx_path
                self.idx += 1
                continue

            if line.startswith("===") or line.startswith("RCS file:"):
                # Subversion / CVS header dividers
                self.idx += 1
                continue

            if line.startswith("new file mode"):
                is_new_mode = True
                self.idx += 1
                continue

            if line.startswith("deleted file mode"):
                is_deleted_mode = True
                self.idx += 1
                continue

            if line.startswith("old mode ") or line.startswith("new mode "):
                is_mode_change = True
                self.idx += 1
                continue

            if line.startswith("rename from "):
                old_path = line[12:].strip().strip("\"'")
                self.idx += 1
                continue

            if line.startswith("rename to "):
                new_path = line[10:].strip().strip("\"'")
                self.idx += 1
                continue

            if line.startswith("Binary files "):
                is_binary = True
                bin_match = BINARY_DIFF_RE.match(line)
                if bin_match:
                    bin_old = bin_match.group("old_quoted") or bin_match.group("old_unquoted")
                    bin_new = bin_match.group("new_quoted") or bin_match.group("new_unquoted")
                    if not old_path:
                        old_path = bin_old
                    if not new_path:
                        new_path = bin_new
                self.idx += 1
                break

            if line.startswith("--- "):
                old_path = line[4:].strip().strip("\"'")
                self.idx += 1
                continue

            if line.startswith("+++ "):
                new_path = line[4:].strip().strip("\"'")
                self.idx += 1
                break

            if line.startswith("@@ "):
                break

            if line.startswith("index ") or line.startswith("similarity index "):
                self.idx += 1
                continue

            break

        if not old_path and not new_path:
            return None

        norm_old = _clean_path(old_path) if old_path else None
        norm_new = _clean_path(new_path) if new_path else None

        if norm_old == "/dev/null" or is_new_mode:
            change_type = ChangeType.ADD
            canonical_path = norm_new or norm_old or ""
        elif norm_new == "/dev/null" or is_deleted_mode:
            change_type = ChangeType.DELETE
            canonical_path = norm_old or norm_new or ""
        else:
            change_type = ChangeType.MODIFY
            canonical_path = norm_new or norm_old or ""

        if not canonical_path or canonical_path == "/dev/null":
            msg = f"Could not determine valid target file path: {old_path} -> {new_path}"
            raise InvalidPatchFormatError(msg)

        hunks: list[PatchHunk] = []
        if not is_binary:
            while self.idx < self.total:
                line = self.lines[self.idx]
                if (
                    line.startswith("diff --git")
                    or line.startswith("--- ")
                    or line.startswith("Index: ")
                    or line.startswith("Binary files ")
                ):
                    break

                if line.startswith("@@ "):
                    hunk = self._parse_hunk()
                    hunks.append(hunk)
                else:
                    if not line.strip():
                        self.idx += 1
                        continue
                    if is_mode_change or (old_path and new_path and old_path != new_path):
                        break
                    msg = (
                        f"Unexpected content before next hunk/file at line {self.idx + 1}: {line!r}"
                    )
                    raise InvalidPatchFormatError(msg)

        return FileDelta(
            path=canonical_path,
            change_type=change_type,
            old_path=old_path,
            new_path=new_path,
            hunks=tuple(hunks),
            is_binary=is_binary,
        )

    def _parse_hunk(self) -> PatchHunk:
        header_line = self.lines[self.idx]
        match = HUNK_HEADER_RE.match(header_line)
        if not match:
            msg = f"Malformed hunk header at line {self.idx + 1}: {header_line!r}"
            raise InvalidPatchFormatError(msg)

        old_start = int(match.group("old_start"))
        old_lines_raw = match.group("old_lines")
        old_lines = (
            int(old_lines_raw) if old_lines_raw is not None else (0 if old_start == 0 else 1)
        )

        new_start = int(match.group("new_start"))
        new_lines_raw = match.group("new_lines")
        new_lines = (
            int(new_lines_raw) if new_lines_raw is not None else (0 if new_start == 0 else 1)
        )

        self.idx += 1

        hunk_lines: list[str] = []
        actual_old_count = 0
        actual_new_count = 0

        while self.idx < self.total:
            line = self.lines[self.idx]

            if (
                line.startswith("@@ ")
                or line.startswith("diff --git")
                or line.startswith("--- ")
                or line.startswith("Index: ")
                or line.startswith("Binary files ")
            ):
                break

            if line.startswith(" "):
                hunk_lines.append(line)
                actual_old_count += 1
                actual_new_count += 1
                self.idx += 1
            elif line.startswith("-"):
                hunk_lines.append(line)
                actual_old_count += 1
                self.idx += 1
            elif line.startswith("+"):
                hunk_lines.append(line)
                actual_new_count += 1
                self.idx += 1
            elif line.startswith("\\"):
                hunk_lines.append(line)
                self.idx += 1
            elif line == "":
                if actual_old_count < old_lines or actual_new_count < new_lines:
                    hunk_lines.append(" ")
                    actual_old_count += 1
                    actual_new_count += 1
                    self.idx += 1
                else:
                    break
            else:
                msg = f"Invalid line marker in hunk body at line {self.idx + 1}: {line!r}"
                raise InvalidPatchFormatError(msg)

            if actual_old_count == old_lines and actual_new_count == new_lines:
                if self.idx < self.total and self.lines[self.idx].startswith("\\"):
                    hunk_lines.append(self.lines[self.idx])
                    self.idx += 1
                break

        if actual_old_count != old_lines or actual_new_count != new_lines:
            counts = (
                f"expected -{old_lines}/+{new_lines}, found -{actual_old_count}/+{actual_new_count}"
            )
            msg = f"Hunk line count mismatch for header '{header_line}': {counts}"
            raise InvalidPatchFormatError(msg)

        return PatchHunk(
            old_start=old_start,
            old_lines=old_lines,
            new_start=new_start,
            new_lines=new_lines,
            header=header_line,
            lines=tuple(hunk_lines),
        )


def parse_patch(raw_diff: str) -> CanonicalPatch:
    """Parse a unified diff string into an immutable CanonicalPatch model.

    Args:
        raw_diff: Raw unified diff string (supports CRLF and LF line endings).

    Returns:
        CanonicalPatch: Immutable representation containing parsed file deltas and hunks.

    Raises:
        InvalidPatchFormatError: If raw_diff is empty, corrupted, or violates diff syntax.
    """
    parser = _UnifiedDiffParser(raw_diff)
    return parser.parse()
