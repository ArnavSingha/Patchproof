"""PatchProof custom exception hierarchy."""


class PatchProofError(Exception):
    """Base exception for all PatchProof errors."""


class InvalidPatchFormatError(PatchProofError):
    """Raised when a patch cannot be parsed or violates unified diff specifications."""


class SandboxSecurityError(PatchProofError):
    """Raised when an invalid, insecure, or prohibited sandbox configuration is attempted."""


class DockerUnavailableError(PatchProofError):
    """Raised when the Docker daemon is inactive, inaccessible, or unresponsive."""


class UnsupportedDockerEngineError(DockerUnavailableError):
    """Raised when Docker Desktop is running in Windows Containers mode instead of Linux/WSL2."""


class ContainerExecutionTimeoutError(PatchProofError):
    """Raised when an isolated test container exceeds the maximum execution timeout."""


class PatchApplicationError(PatchProofError):
    """Raised when a patch cannot be applied cleanly to the staging worktree."""


class GitRepositoryError(PatchProofError):
    """Raised when a Git command fails or the target path is not a Git repository."""


class NoChangesDetectedError(PatchProofError):
    """Raised when no diff changes are detected in the working tree or target commit."""
