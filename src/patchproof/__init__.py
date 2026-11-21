"""PatchProof - Deterministic Code-Patch Verification & Reliability Engine."""

from patchproof.core.normalizer import parse_patch
from patchproof.exceptions import InvalidPatchFormatError, PatchProofError
from patchproof.models.patch import (
    CanonicalPatch,
    ChangeType,
    FileDelta,
    PatchHunk,
    PatchType,
)

__version__ = "0.1.0"

__all__ = [
    "CanonicalPatch",
    "ChangeType",
    "FileDelta",
    "InvalidPatchFormatError",
    "PatchHunk",
    "PatchProofError",
    "PatchType",
    "parse_patch",
]
