"""Immutable models representing canonical patch representations."""

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class PatchType(StrEnum):
    """Supported patch format types."""

    UNIFIED_DIFF = "unified_diff"
    SEARCH_REPLACE = "search_replace"


class ChangeType(StrEnum):
    """Type of file alteration within a patch."""

    ADD = "add"
    DELETE = "delete"
    MODIFY = "modify"


class PatchHunk(BaseModel):
    """Represents an individual unified diff hunk."""

    model_config = ConfigDict(frozen=True)

    old_start: int = Field(description="Starting line in original file")
    old_lines: int = Field(description="Number of lines in original file hunk")
    new_start: int = Field(description="Starting line in new file")
    new_lines: int = Field(description="Number of lines in new file hunk")
    header: str = Field(description="Raw @@ hunk header line")
    lines: tuple[str, ...] = Field(description="Hunk body lines including prefix (+, -, ' ', \\)")

    @property
    def added_lines_count(self) -> int:
        """Count of added lines starting with '+'."""
        return sum(1 for line in self.lines if line.startswith("+"))

    @property
    def deleted_lines_count(self) -> int:
        """Count of deleted lines starting with '-'."""
        return sum(1 for line in self.lines if line.startswith("-"))


class FileDelta(BaseModel):
    """Represents changes applied to a single file."""

    model_config = ConfigDict(frozen=True)

    path: str = Field(description="Canonical repository relative path")
    change_type: ChangeType = Field(description="Type of modification (add/delete/modify)")
    old_path: str | None = Field(default=None, description="Original path from diff header")
    new_path: str | None = Field(default=None, description="New path from diff header")
    hunks: tuple[PatchHunk, ...] = Field(default_factory=tuple, description="Ordered patch hunks")
    is_binary: bool = Field(default=False, description="Flag indicating binary file change")

    @property
    def is_new_file(self) -> bool:
        """Return True if this file is newly created."""
        return self.change_type == ChangeType.ADD

    @property
    def is_deleted_file(self) -> bool:
        """Return True if this file was removed."""
        return self.change_type == ChangeType.DELETE

    @property
    def total_added_lines(self) -> int:
        """Sum of all additions across all hunks for this file."""
        return sum(hunk.added_lines_count for hunk in self.hunks)

    @property
    def total_deleted_lines(self) -> int:
        """Sum of all deletions across all hunks for this file."""
        return sum(hunk.deleted_lines_count for hunk in self.hunks)


class CanonicalPatch(BaseModel):
    """Immutable root model representing a normalized patch."""

    model_config = ConfigDict(frozen=True)

    patch_type: PatchType = Field(default=PatchType.UNIFIED_DIFF)
    file_deltas: tuple[FileDelta, ...] = Field(default_factory=tuple)
    raw_diff: str = Field(description="Normalized raw diff string (LF normalized)")

    @property
    def modified_paths(self) -> tuple[str, ...]:
        """All file paths touched by this patch."""
        return tuple(delta.path for delta in self.file_deltas)

    @property
    def total_files(self) -> int:
        """Total number of modified, added, or deleted files."""
        return len(self.file_deltas)

    @property
    def total_additions(self) -> int:
        """Total additions across all files in the patch."""
        return sum(delta.total_added_lines for delta in self.file_deltas)

    @property
    def total_deletions(self) -> int:
        """Total deletions across all files in the patch."""
        return sum(delta.total_deleted_lines for delta in self.file_deltas)
