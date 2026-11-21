"""PatchProof - Deterministic Code-Patch Verification & Reliability Engine."""

from patchproof.core.ast_analyzer import (
    analyze_patch_ast,
    analyze_python_source,
    extract_delta_line_ranges,
)
from patchproof.core.normalizer import parse_patch
from patchproof.core.search_replace import parse_search_replace
from patchproof.exceptions import InvalidPatchFormatError, PatchProofError
from patchproof.models.ast import (
    ASTAnalysisReport,
    ASTEntity,
    EntityType,
    FileASTReport,
)
from patchproof.models.patch import (
    CanonicalPatch,
    ChangeType,
    FileDelta,
    PatchHunk,
    PatchType,
)

__version__ = "0.2.0"

__all__ = [
    "ASTAnalysisReport",
    "ASTEntity",
    "CanonicalPatch",
    "ChangeType",
    "EntityType",
    "FileASTReport",
    "FileDelta",
    "InvalidPatchFormatError",
    "PatchHunk",
    "PatchProofError",
    "PatchType",
    "analyze_patch_ast",
    "analyze_python_source",
    "extract_delta_line_ranges",
    "parse_patch",
    "parse_search_replace",
]
