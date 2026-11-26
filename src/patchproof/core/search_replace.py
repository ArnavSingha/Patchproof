"""Aider SEARCH/REPLACE block parsing and normalization into canonical patch representations."""

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

SEARCH_START = "<<<<<<< SEARCH"
DIVIDER = "======="
REPLACE_END = ">>>>>>> REPLACE"


def _clean_filename(line: str) -> str:
    """Extract and sanitize filename preceding a search/replace block."""
    cleaned = line.strip()
    cleaned = cleaned.strip("`'\"#* ")
    if ":" in cleaned:
        parts = cleaned.split(":", 1)
        if parts[0].strip().lower() in ("file", "filename", "path"):
            cleaned = parts[1].strip().strip("`'\" ")
    return cleaned


class _SearchReplaceParser:
    """State-machine parser for Aider-style SEARCH/REPLACE edit blocks."""

    def __init__(self, raw_text: str) -> None:
        self.raw_text = raw_text.replace("\r\n", "\n").replace("\r", "\n")
        self.lines = self.raw_text.split("\n")
        if self.lines and self.lines[-1] == "":
            self.lines.pop()
        self.idx = 0
        self.total = len(self.lines)

    def parse(self) -> CanonicalPatch:
        if not self.raw_text.strip():
            msg = "Search/Replace content is empty or contains only whitespace"
            raise InvalidPatchFormatError(msg)

        if SEARCH_START not in self.raw_text:
            if DIVIDER in self.raw_text or REPLACE_END in self.raw_text:
                raise InvalidPatchFormatError("Unexpected delimiter found outside an active block")
            raise InvalidPatchFormatError(
                f"Missing required delimiter '{SEARCH_START}' in search/replace input"
            )

        file_deltas_map: dict[str, list[PatchHunk]] = {}
        current_file: str | None = None

        while self.idx < self.total:
            line = self.lines[self.idx].strip()

            if line == SEARCH_START:
                if not current_file:
                    raise InvalidPatchFormatError(
                        f"Found '{SEARCH_START}' at line {self.idx + 1} without a target filename"
                    )
                hunk = self._parse_block()
                file_deltas_map.setdefault(current_file, []).append(hunk)
                continue

            if self._is_potential_file_path(line):
                next_non_empty = self._peek_next_non_empty()
                if next_non_empty == SEARCH_START:
                    current_file = _clean_filename(line)
                    self.idx += 1
                    continue

            if line in (DIVIDER, REPLACE_END):
                msg = (
                    f"Unexpected delimiter '{line}' found outside an active block "
                    f"at line {self.idx + 1}"
                )
                raise InvalidPatchFormatError(msg)

            self.idx += 1

        if not file_deltas_map:
            raise InvalidPatchFormatError("No valid SEARCH/REPLACE blocks successfully parsed")

        deltas: list[FileDelta] = []
        for path, hunks in file_deltas_map.items():
            all_additions = all(h.old_lines == 0 and h.new_lines > 0 for h in hunks)
            all_deletions = all(h.new_lines == 0 and h.old_lines > 0 for h in hunks)
            if all_additions:
                change_type = ChangeType.ADD
            elif all_deletions:
                change_type = ChangeType.DELETE
            else:
                change_type = ChangeType.MODIFY

            delta = FileDelta(
                path=path,
                change_type=change_type,
                old_path=path,
                new_path=path,
                hunks=tuple(hunks),
            )
            deltas.append(delta)

        return CanonicalPatch(
            patch_type=PatchType.SEARCH_REPLACE,
            file_deltas=tuple(deltas),
            raw_diff=self.raw_text,
        )

    def _is_potential_file_path(self, line: str) -> bool:
        if not line:
            return False
        if line.startswith("<") or line.startswith("=") or line.startswith(">"):
            return False
        cleaned = _clean_filename(line)
        if not cleaned:
            return False
        if re.search(r"\.[A-Za-z0-9_-]+$", cleaned) or "/" in cleaned or "\\" in cleaned:
            return True
        return bool(re.match(r"^[A-Za-z0-9_.-]+$", cleaned))

    def _peek_next_non_empty(self) -> str | None:
        lookahead = self.idx + 1
        while lookahead < self.total:
            item = self.lines[lookahead].strip()
            if item:
                return item
            lookahead += 1
        return None

    def _parse_block(self) -> PatchHunk:
        start_line_idx = self.idx + 1
        self.idx += 1

        search_lines: list[str] = []
        replace_lines: list[str] = []
        found_divider = False
        found_end = False

        while self.idx < self.total:
            line = self.lines[self.idx]
            if line.strip() == DIVIDER:
                found_divider = True
                self.idx += 1
                break
            if line.strip() == SEARCH_START:
                raise InvalidPatchFormatError(
                    f"Nested or unclosed '{SEARCH_START}' at line {self.idx + 1}"
                )
            if line.strip() == REPLACE_END:
                raise InvalidPatchFormatError(
                    f"Found '{REPLACE_END}' before '{DIVIDER}' at line {self.idx + 1}"
                )
            search_lines.append(line)
            self.idx += 1

        if not found_divider:
            raise InvalidPatchFormatError(
                f"Block starting at line {start_line_idx} missing '{DIVIDER}' delimiter"
            )

        while self.idx < self.total:
            line = self.lines[self.idx]
            if line.strip() == REPLACE_END:
                found_end = True
                self.idx += 1
                break
            if line.strip() in (SEARCH_START, DIVIDER):
                delim = line.strip()
                msg = f"Unexpected delimiter '{delim}' inside REPLACE block at line {self.idx + 1}"
                raise InvalidPatchFormatError(msg)
            replace_lines.append(line)
            self.idx += 1

        if not found_end:
            raise InvalidPatchFormatError(
                f"Block starting at line {start_line_idx} missing '{REPLACE_END}' delimiter"
            )

        old_count = len(search_lines)
        new_count = len(replace_lines)

        if old_count == 0 and new_count == 0:
            raise InvalidPatchFormatError(
                f"Block starting at line {start_line_idx} has both empty SEARCH and empty REPLACE"
            )

        hunk_body = tuple(
            ["-" + line_text for line_text in search_lines]
            + ["+" + line_text for line_text in replace_lines]
        )
        header = f"@@ -1,{old_count} +1,{new_count} @@"

        return PatchHunk(
            old_start=1 if old_count > 0 else 0,
            old_lines=old_count,
            new_start=1 if new_count > 0 else 0,
            new_lines=new_count,
            header=header,
            lines=hunk_body,
        )


def parse_search_replace(raw_text: str) -> CanonicalPatch:
    """Parse an Aider-style SEARCH/REPLACE text block into a CanonicalPatch model.

    Args:
        raw_text: Raw string containing one or more SEARCH/REPLACE edit blocks.

    Returns:
        CanonicalPatch: Immutable canonical representation of the edits.

    Raises:
        InvalidPatchFormatError: If delimiters are missing, ambiguous, or malformed.
    """
    parser = _SearchReplaceParser(raw_text)
    return parser.parse()
