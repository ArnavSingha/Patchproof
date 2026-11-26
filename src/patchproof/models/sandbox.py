"""Domain models for isolated sandbox configuration, execution, and regression analysis."""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from patchproof.exceptions import SandboxSecurityError

DISALLOWED_ENV_PATTERNS = (
    "TOKEN",
    "KEY",
    "SECRET",
    "PASSWORD",
    "AUTH",
    "CREDENTIAL",
    "AWS",
    "GITHUB",
    "GH_",
    "DOCKER",
    "SSH",
)

ALLOWLISTED_EXACT_VARS = {
    "PATH",
    "LANG",
    "LC_ALL",
    "PYTHONUNBUFFERED",
    "PYTHONDONTWRITEBYTECODE",
    "PYTHONPATH",
    "CI",
    "TERM",
}

ALLOWLISTED_PREFIXES = ("PYTEST_",)

FORBIDDEN_HOST_PATHS = (
    ".ssh",
    ".aws",
    "docker.sock",
    "docker_engine",
    "/etc/shadow",
    "/etc/passwd",
    "system32",
)

BROAD_ROOT_PATHS = (
    "",
    "/",
    "c:",
    "c:/windows",
    "c:/users",
    "/home",
    "/root",
)


class NetworkMode(StrEnum):
    """Allowed network isolation modes."""

    NONE = "none"


class TestOutcome(StrEnum):
    """Result of an individual test case execution."""

    __test__ = False

    PASS = "pass"
    FAIL = "fail"
    ERROR = "error"
    SKIPPED = "skipped"
    XFAIL = "xfail"
    XPASS = "xpass"


class RegressionClassification(StrEnum):
    """Classification of test outcome evolution between baseline and patched code."""

    CLEAN_PRESERVATION = "clean_preservation"
    INTRODUCED_REGRESSION = "introduced_regression"
    RESOLVED_DEFECT = "resolved_defect"
    PRE_EXISTING_FAILURE = "pre_existing_failure"
    SUPPRESSION_ALERT = "suppression_alert"
    PRE_EXISTING_SKIP = "pre_existing_skip"
    NEW_TEST_PASSING = "new_test_passing"
    NEW_TEST_FAILING = "new_test_failing"
    NEW_TEST_SKIPPED = "new_test_skipped"
    REMOVED_TEST = "removed_test"
    FLAKY_REGRESSION = "flaky_regression"
    TEST_TAMPERING = "test_tampering"


class PatchVerdict(StrEnum):
    """High-level evaluation verdict for a patch."""

    ACCEPTED = "accepted"
    REJECTED = "rejected"
    SUSPICIOUS = "suspicious"
    INCONCLUSIVE = "inconclusive"
    INFRASTRUCTURE_FAILURE = "infrastructure_failure"


class SandboxFailureReason(StrEnum):
    """Explicit taxonomy of sandbox and execution failures."""

    NONE = "none"
    TIMEOUT = "timeout"
    OOM_KILLED = "oom_killed"
    COLLECTION_FAILURE = "collection_failure"
    DOCKER_UNAVAILABLE = "docker_unavailable"
    UNSUPPORTED_ENGINE = "unsupported_engine"
    SETUP_ERROR = "setup_error"
    EXECUTION_ERROR = "execution_error"


class ResourceLimits(BaseModel):
    """Hardware resource limits and execution quotas enforced on test containers."""

    model_config = ConfigDict(frozen=True)

    cpu_count: float = Field(default=1.0, gt=0.0, le=4.0)
    memory_limit_mb: int = Field(default=1024, ge=128, le=4096)
    pids_limit: int = Field(default=128, ge=16, le=512)
    timeout_seconds: float = Field(default=15.0, gt=0.0, le=300.0)
    workspace_tmpfs_size_mb: int = Field(default=256, ge=64, le=1024)


class SecurityPolicy(BaseModel):
    """Strict security invariants enforced on every container execution."""

    model_config = ConfigDict(frozen=True)

    network_mode: NetworkMode = Field(default=NetworkMode.NONE)
    user: str = Field(default="10001:10001")
    drop_capabilities: tuple[str, ...] = Field(default=("ALL",))
    no_new_privileges: bool = Field(default=True)
    mask_git_dir: bool = Field(default=True)
    read_only_root: bool = Field(default=True)
    allow_host_fallback: bool = Field(default=False)

    @field_validator("allow_host_fallback")
    @classmethod
    def _validate_host_fallback(cls, v: bool) -> bool:
        if v:
            raise SandboxSecurityError(
                "Host fallback execution is strictly prohibited by security policy"
            )
        return v

    @field_validator("network_mode", mode="before")
    @classmethod
    def _validate_network_mode(cls, v: Any) -> Any:
        if v not in (NetworkMode.NONE, "none"):
            raise SandboxSecurityError(
                "Only NetworkMode.NONE is permitted for untrusted code execution"
            )
        return v

    @field_validator("user")
    @classmethod
    def _validate_user(cls, v: str) -> str:
        cleaned = v.strip().lower()
        uid = cleaned.split(":", 1)[0].strip()
        if uid == "root" or (uid.isdigit() and int(uid) == 0):
            raise SandboxSecurityError("Root container execution is prohibited")
        return v

    @field_validator("drop_capabilities")
    @classmethod
    def _validate_capabilities(cls, v: tuple[str, ...]) -> tuple[str, ...]:
        if "ALL" not in v:
            raise SandboxSecurityError("drop_capabilities must contain 'ALL'")
        return v

    @field_validator("no_new_privileges")
    @classmethod
    def _validate_no_new_privileges(cls, v: bool) -> bool:
        if not v:
            raise SandboxSecurityError("no_new_privileges must be True")
        return v

    @field_validator("mask_git_dir")
    @classmethod
    def _validate_mask_git_dir(cls, v: bool) -> bool:
        if not v:
            raise SandboxSecurityError("mask_git_dir must be True to prevent credential leakage")
        return v


class ContainerEnvironment(BaseModel):
    """Sanitized, allowlist-only environment variables for container execution."""

    model_config = ConfigDict(frozen=True)

    env_vars: tuple[tuple[str, str], ...] = Field(default=())

    @field_validator("env_vars")
    @classmethod
    def _validate_env_vars(cls, v: tuple[tuple[str, str], ...]) -> tuple[tuple[str, str], ...]:
        for key, _ in v:
            key_upper = key.upper()
            for pattern in DISALLOWED_ENV_PATTERNS:
                if pattern in key_upper:
                    raise SandboxSecurityError(
                        f"Environment variable '{key}' violates credential isolation policy"
                    )
            if key not in ALLOWLISTED_EXACT_VARS and not any(
                key.startswith(p) for p in ALLOWLISTED_PREFIXES
            ):
                raise SandboxSecurityError(
                    f"Environment variable '{key}' violates credential isolation policy "
                    "(not in allowlist)"
                )
        return v

    def as_dict(self) -> dict[str, str]:
        """Convert tuple of key-value pairs into a standard dictionary."""
        return dict(self.env_vars)


class ContainerConfig(BaseModel):
    """Immutable specifications for launching an isolated execution container."""

    model_config = ConfigDict(frozen=True)

    image: str = Field(default="patchproof-runner-python:3.11")
    resources: ResourceLimits = Field(default_factory=ResourceLimits)
    security: SecurityPolicy = Field(default_factory=SecurityPolicy)
    environment: ContainerEnvironment = Field(default_factory=ContainerEnvironment)
    host_repo_path: str
    container_workspace_path: str = Field(default="/workspace")
    container_repo_base_path: str = Field(default="/repo_base")
    entrypoint_command: tuple[str, ...] = Field(default=("pytest", "-q"))

    @field_validator("host_repo_path")
    @classmethod
    def _validate_host_repo_path(cls, v: str) -> str:
        normalized = v.replace("\\", "/").lower().rstrip("/")
        if normalized in BROAD_ROOT_PATHS:
            raise SandboxSecurityError(f"Broad root host path prohibited from mount: {v}")
        for forbidden in FORBIDDEN_HOST_PATHS:
            if forbidden in normalized:
                raise SandboxSecurityError(f"Sensitive host path prohibited from mount: {v}")
        return v


class TestCaseResult(BaseModel):
    """Syntactic outcome and execution measurements of an individual test case."""

    __test__ = False
    model_config = ConfigDict(frozen=True)

    name: str = Field(description="Fully-qualified test identifier")
    outcome: TestOutcome = Field(description="Result of test execution")
    duration_seconds: float = Field(ge=0.0, description="Execution duration in seconds")
    failure_message: str | None = Field(default=None, description="Captured failure traceback")
    stdout: str | None = Field(default=None, description="Captured stdout for this test")
    stderr: str | None = Field(default=None, description="Captured stderr for this test")


class TestRunReport(BaseModel):
    """Consolidated test suite report extracted from a container run."""

    __test__ = False
    model_config = ConfigDict(frozen=True)

    test_cases: tuple[TestCaseResult, ...] = Field(default=())
    exit_code: int = Field(description="Process exit code from test runner")
    duration_seconds: float = Field(ge=0.0, description="Total duration of test runner execution")
    total_collected: int = Field(ge=0, description="Total number of tests discovered")
    collection_failed: bool = Field(default=False, description="True if test discovery failed")
    collection_error: str | None = Field(default=None, description="Captured collection error")

    @property
    def passed_count(self) -> int:
        """Count of passing test cases."""
        return sum(1 for t in self.test_cases if t.outcome == TestOutcome.PASS)

    @property
    def failed_count(self) -> int:
        """Count of failed or errored test cases."""
        return sum(1 for t in self.test_cases if t.outcome in (TestOutcome.FAIL, TestOutcome.ERROR))

    @property
    def skipped_count(self) -> int:
        """Count of skipped test cases."""
        return sum(1 for t in self.test_cases if t.outcome == TestOutcome.SKIPPED)

    @property
    def test_map(self) -> dict[str, TestCaseResult]:
        """Lookup dictionary mapping test name to TestCaseResult."""
        return {t.name: t for t in self.test_cases}


class ExecutionResult(BaseModel):
    """Low-level execution result from container execution."""

    model_config = ConfigDict(frozen=True)

    exit_code: int = Field(description="Container process exit code")
    stdout: str = Field(default="", description="Captured stdout from container")
    stderr: str = Field(default="", description="Captured stderr from container")
    duration_seconds: float = Field(ge=0.0, description="Total elapsed container execution time")
    failure_reason: SandboxFailureReason = Field(
        default=SandboxFailureReason.NONE,
        description="Explicit category of failure if execution did not complete normally",
    )
    oom_killed: bool = Field(default=False, description="True if container was OOM killed")
    timeout_triggered: bool = Field(default=False, description="True if container timed out")
    test_report: TestRunReport | None = Field(default=None, description="Parsed test suite report")

    @property
    def is_success(self) -> bool:
        """Return True if execution completed with exit code 0 and no failure signals."""
        return (
            self.exit_code == 0
            and self.failure_reason == SandboxFailureReason.NONE
            and not self.oom_killed
            and not self.timeout_triggered
        )


class ComparativeTestCase(BaseModel):
    """Comparison of a single test case between baseline and patched execution."""

    model_config = ConfigDict(frozen=True)

    test_name: str = Field(description="Test case identifier")
    baseline_outcome: TestOutcome | None = Field(default=None, description="Baseline result")
    patched_outcome: TestOutcome | None = Field(default=None, description="Patched result")
    classification: RegressionClassification = Field(description="Behavioral categorization")
    message: str | None = Field(default=None, description="Explanatory notes on classification")


class ComparativeExecutionResult(BaseModel):
    """Complete reconciliation report between baseline and patched container runs."""

    model_config = ConfigDict(frozen=True)

    baseline_result: ExecutionResult = Field(description="Execution result from pre-patch state")
    patched_result: ExecutionResult = Field(description="Execution result from post-patch state")
    verdict: PatchVerdict = Field(description="Final empirical evaluation verdict for the patch")
    test_comparisons: tuple[ComparativeTestCase, ...] = Field(default=())
    introduced_regressions_count: int = Field(ge=0, default=0)
    flaky_tests_count: int = Field(ge=0, default=0)
    resolved_defects_count: int = Field(ge=0, default=0)
    pre_existing_failures_count: int = Field(ge=0, default=0)
    suppressed_tests_count: int = Field(ge=0, default=0)
    removed_tests_count: int = Field(ge=0, default=0)
    test_tampering_detected: bool = Field(default=False)
    tampering_details: tuple[str, ...] = Field(default=())
    collection_failure_occurred: bool = Field(default=False)
    summary: str = Field(default="")

    @model_validator(mode="after")
    def _validate_verdict_consistency(self) -> ComparativeExecutionResult:
        if self.introduced_regressions_count > 0 and self.verdict in (
            PatchVerdict.ACCEPTED,
            PatchVerdict.INCONCLUSIVE,
        ):
            raise SandboxSecurityError(
                "Verdict cannot be ACCEPTED when introduced regressions exist"
            )
        if self.test_tampering_detected and self.verdict == PatchVerdict.ACCEPTED:
            raise SandboxSecurityError(
                "Verdict cannot be ACCEPTED when test tampering is detected"
            )
        if self.removed_tests_count > 0 and self.verdict == PatchVerdict.ACCEPTED:
            raise SandboxSecurityError(
                "Verdict cannot be ACCEPTED when tests were removed or uncollected"
            )
        if self.collection_failure_occurred and self.verdict != PatchVerdict.REJECTED:
            raise SandboxSecurityError("Verdict must be REJECTED when collection failure occurs")
        return self
