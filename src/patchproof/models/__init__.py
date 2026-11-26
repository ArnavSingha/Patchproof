"""Domain models for PatchProof."""

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
from patchproof.models.sandbox import (
    ComparativeExecutionResult,
    ComparativeTestCase,
    ContainerConfig,
    ContainerEnvironment,
    ExecutionResult,
    NetworkMode,
    PatchVerdict,
    RegressionClassification,
    ResourceLimits,
    SandboxFailureReason,
    SecurityPolicy,
    TestCaseResult,
    TestOutcome,
    TestRunReport,
)

__all__ = [
    "ASTAnalysisReport",
    "ASTEntity",
    "CanonicalPatch",
    "ChangeType",
    "ComparativeExecutionResult",
    "ComparativeTestCase",
    "ContainerConfig",
    "ContainerEnvironment",
    "EntityType",
    "ExecutionResult",
    "FileASTReport",
    "FileDelta",
    "NetworkMode",
    "PatchHunk",
    "PatchType",
    "PatchVerdict",
    "RegressionClassification",
    "ResourceLimits",
    "SandboxFailureReason",
    "SecurityPolicy",
    "TestCaseResult",
    "TestOutcome",
    "TestRunReport",
]
