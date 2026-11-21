"""PatchProof custom exception hierarchy."""


class PatchProofError(Exception):
    """Base exception for all PatchProof errors."""


class InvalidPatchFormatError(PatchProofError):
    """Raised when a patch cannot be parsed or violates unified diff specifications."""
