"""Unit tests for PatchProof Milestone M3 sandbox and execution domain models."""

import re
from typing import Any, cast

import pytest
from pydantic import ValidationError

from patchproof.exceptions import SandboxSecurityError
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


class TestResourceLimits:
    """Test validation and immutability of ResourceLimits."""

    def test_default_resource_limits_valid(self) -> None:
        limits = ResourceLimits()
        assert limits.cpu_count == 1.0
        assert limits.memory_limit_mb == 1024
        assert limits.pids_limit == 128
        assert limits.timeout_seconds == 15.0
        assert limits.workspace_tmpfs_size_mb == 256

    def test_resource_limits_immutability(self) -> None:
        limits = ResourceLimits()
        with pytest.raises(ValidationError):
            limits.cpu_count = 2.0

    @pytest.mark.parametrize(
        ("cpu", "mem", "pids", "timeout", "tmpfs"),
        [
            (-1.0, 1024, 128, 15.0, 256),  # negative CPU
            (0.0, 1024, 128, 15.0, 256),  # zero CPU
            (5.0, 1024, 128, 15.0, 256),  # excessive CPU (>4.0)
            (1.0, 64, 128, 15.0, 256),  # memory too low (<128MB)
            (1.0, 8192, 128, 15.0, 256),  # memory too high (>4096MB)
            (1.0, 1024, 8, 15.0, 256),  # pids too low (<16)
            (1.0, 1024, 1024, 15.0, 256),  # pids too high (>512)
            (1.0, 1024, 128, 0.0, 256),  # zero timeout
            (1.0, 1024, 128, -5.0, 256),  # negative timeout
            (1.0, 1024, 128, 301.0, 256),  # excessive timeout (>300s)
            (1.0, 1024, 128, 15.0, 32),  # tmpfs too low (<64MB)
            (1.0, 1024, 128, 15.0, 2048),  # tmpfs too high (>1024MB)
        ],
    )
    def test_invalid_resource_limits_rejected(
        self, cpu: float, mem: int, pids: int, timeout: float, tmpfs: int
    ) -> None:
        with pytest.raises(ValidationError):
            ResourceLimits(
                cpu_count=cpu,
                memory_limit_mb=mem,
                pids_limit=pids,
                timeout_seconds=timeout,
                workspace_tmpfs_size_mb=tmpfs,
            )


class TestSecurityPolicy:
    """Test security policy validation, default invariants, and rejection of unsafe configs."""

    def test_default_security_policy_safe(self) -> None:
        policy = SecurityPolicy()
        assert policy.network_mode == NetworkMode.NONE
        assert policy.user == "10001:10001"
        assert policy.drop_capabilities == ("ALL",)
        assert policy.no_new_privileges is True
        assert policy.mask_git_dir is True
        assert policy.read_only_root is True
        assert policy.allow_host_fallback is False

    def test_security_policy_immutability(self) -> None:
        policy = SecurityPolicy()
        with pytest.raises(ValidationError):
            policy.allow_host_fallback = True

    def test_reject_host_fallback(self) -> None:
        with pytest.raises(
            (ValidationError, SandboxSecurityError),
            match="Host fallback execution is strictly prohibited",
        ):
            SecurityPolicy(allow_host_fallback=True)

    def test_reject_non_none_network(self) -> None:
        with pytest.raises(
            (ValidationError, SandboxSecurityError),
            match=re.escape("Only NetworkMode.NONE is permitted"),
        ):
            SecurityPolicy(network_mode=cast(Any, "bridge"))

    @pytest.mark.parametrize(
        "unsafe_user",
        [
            "root",
            "0",
            "0:0",
            "root:root",
            "root:wheel",
            "root:10001",
            "0:users",
            "00:00",
            "00",
        ],
    )
    def test_reject_root_user(self, unsafe_user: str) -> None:
        with pytest.raises(
            (ValidationError, SandboxSecurityError),
            match="Root container execution is prohibited",
        ):
            SecurityPolicy(user=unsafe_user)

    def test_reject_missing_all_capabilities_drop(self) -> None:
        with pytest.raises(
            (ValidationError, SandboxSecurityError),
            match="drop_capabilities must contain 'ALL'",
        ):
            SecurityPolicy(drop_capabilities=("CHOWN", "SETUID"))

    def test_reject_disabled_no_new_privileges(self) -> None:
        with pytest.raises(
            (ValidationError, SandboxSecurityError),
            match="no_new_privileges must be True",
        ):
            SecurityPolicy(no_new_privileges=False)

    def test_reject_disabled_git_masking(self) -> None:
        with pytest.raises(
            (ValidationError, SandboxSecurityError),
            match="mask_git_dir must be True",
        ):
            SecurityPolicy(mask_git_dir=False)


class TestContainerEnvironment:
    """Test environment variable sanitization and rejection of sensitive credentials."""

    def test_allowlisted_environment_variables_pass(self) -> None:
        env = ContainerEnvironment(
            env_vars=(
                ("PATH", "/usr/local/bin:/usr/bin:/bin"),
                ("LANG", "C.UTF-8"),
                ("PYTHONUNBUFFERED", "1"),
                ("PYTHONDONTWRITEBYTECODE", "1"),
                ("PYTHONPATH", "/workspace"),
            )
        )
        assert len(env.env_vars) == 5
        assert env.as_dict()["PYTHONUNBUFFERED"] == "1"

    def test_environment_immutability(self) -> None:
        env = ContainerEnvironment()
        with pytest.raises(ValidationError):
            env.env_vars = (("PATH", "/bin"),)

    @pytest.mark.parametrize(
        "forbidden_key",
        [
            "AWS_ACCESS_KEY_ID",
            "AWS_SECRET_ACCESS_KEY",
            "GITHUB_TOKEN",
            "GH_TOKEN",
            "OPENAI_API_KEY",
            "GEMINI_API_KEY",
            "DOCKER_HOST",
            "DOCKER_AUTH",
            "DATABASE_PASSWORD",
            "USER_SECRET",
            "SSH_AUTH_SOCK",
            "API_CREDENTIALS",
        ],
    )
    def test_reject_sensitive_environment_variables(self, forbidden_key: str) -> None:
        with pytest.raises(
            (ValidationError, SandboxSecurityError),
            match="violates credential isolation policy",
        ):
            ContainerEnvironment(env_vars=((forbidden_key, "secret_value"),))

    def test_reject_unknown_unallowlisted_environment_variables(self) -> None:
        with pytest.raises(
            (ValidationError, SandboxSecurityError),
            match="violates credential isolation policy",
        ):
            ContainerEnvironment(env_vars=(("CUSTOM_INJECTED_VAR", "payload"),))


class TestContainerConfig:
    """Test ContainerConfig validation and sensitive host path rejection."""

    def test_valid_container_config(self) -> None:
        config = ContainerConfig(
            host_repo_path="/safe/repo/checkout",
        )
        assert config.image == "patchproof-runner-python:3.11"
        assert config.resources.cpu_count == 1.0
        assert config.security.network_mode == NetworkMode.NONE
        assert config.container_workspace_path == "/workspace"
        assert config.container_repo_base_path == "/repo_base"
        assert config.entrypoint_command == ("pytest", "-q")

    def test_container_config_immutability(self) -> None:
        config = ContainerConfig(host_repo_path="/safe/repo")
        with pytest.raises(ValidationError):
            config.image = "malicious:latest"

    @pytest.mark.parametrize(
        "unsafe_path",
        [
            "/root/.ssh",
            "/home/user/.ssh",
            "C:\\Users\\user\\.ssh",
            "C:/Users/user/.ssh/id_rsa",
            "/home/user/.aws",
            "C:\\Users\\user\\.aws",
            "/var/run/docker.sock",
            "\\\\.\\pipe\\docker_engine",
            "/etc/shadow",
            "C:\\Windows\\System32",
            "/",
            "C:\\",
            "C:/",
            "C:\\Windows",
            "C:\\Users",
            "/home",
            "/root",
        ],
    )
    def test_reject_sensitive_host_mount_paths(self, unsafe_path: str) -> None:
        with pytest.raises(
            (ValidationError, SandboxSecurityError),
            match="prohibited from mount",
        ):
            ContainerConfig(host_repo_path=unsafe_path)


class TestTestExecutionReports:
    """Test TestCaseResult, TestRunReport, and ExecutionResult models."""

    def test_test_case_result_creation_and_immutability(self) -> None:
        case = TestCaseResult(
            name="tests/test_math.py::test_add",
            outcome=TestOutcome.PASS,
            duration_seconds=0.05,
        )
        assert case.name == "tests/test_math.py::test_add"
        assert case.outcome == TestOutcome.PASS
        assert case.duration_seconds == 0.05
        assert case.failure_message is None

        with pytest.raises(ValidationError):
            case.outcome = TestOutcome.FAIL

    def test_test_run_report_aggregation(self) -> None:
        c1 = TestCaseResult(name="t1", outcome=TestOutcome.PASS, duration_seconds=0.01)
        c2 = TestCaseResult(
            name="t2",
            outcome=TestOutcome.FAIL,
            duration_seconds=0.02,
            failure_message="AssertionError",
        )
        c3 = TestCaseResult(name="t3", outcome=TestOutcome.SKIPPED, duration_seconds=0.00)

        report = TestRunReport(
            test_cases=(c1, c2, c3),
            exit_code=1,
            duration_seconds=0.05,
            total_collected=3,
        )
        assert report.passed_count == 1
        assert report.failed_count == 1
        assert report.skipped_count == 1
        assert report.total_collected == 3
        assert report.collection_failed is False
        assert "t1" in report.test_map
        assert report.test_map["t2"].outcome == TestOutcome.FAIL

    def test_collection_failure_report(self) -> None:
        report = TestRunReport(
            test_cases=(),
            exit_code=2,
            duration_seconds=0.01,
            total_collected=0,
            collection_failed=True,
            collection_error="SyntaxError in test_module.py",
        )
        assert report.collection_failed is True
        assert report.collection_error == "SyntaxError in test_module.py"
        assert report.passed_count == 0

    def test_execution_result_success(self) -> None:
        rep = TestRunReport(test_cases=(), exit_code=0, duration_seconds=0.1, total_collected=0)
        res = ExecutionResult(
            exit_code=0,
            stdout="OK",
            stderr="",
            duration_seconds=0.1,
            test_report=rep,
        )
        assert res.is_success is True
        assert res.failure_reason == SandboxFailureReason.NONE
        assert res.oom_killed is False
        assert res.timeout_triggered is False

    def test_execution_result_oom_killed(self) -> None:
        res = ExecutionResult(
            exit_code=137,
            stdout="",
            stderr="Killed",
            duration_seconds=1.2,
            failure_reason=SandboxFailureReason.OOM_KILLED,
            oom_killed=True,
        )
        assert res.is_success is False
        assert res.oom_killed is True

    def test_execution_result_timeout(self) -> None:
        res = ExecutionResult(
            exit_code=124,
            stdout="",
            stderr="Timeout exceeded",
            duration_seconds=15.1,
            failure_reason=SandboxFailureReason.TIMEOUT,
            timeout_triggered=True,
        )
        assert res.is_success is False
        assert res.timeout_triggered is True


class TestComparativeExecutionResult:
    """Test ComparativeTestCase and ComparativeExecutionResult decision models."""

    def test_comparative_result_clean_pass(self) -> None:
        base_exec = ExecutionResult(exit_code=0, stdout="OK", stderr="", duration_seconds=0.2)
        patch_exec = ExecutionResult(exit_code=0, stdout="OK", stderr="", duration_seconds=0.2)

        comp_case = ComparativeTestCase(
            test_name="tests/test_app.py::test_init",
            baseline_outcome=TestOutcome.PASS,
            patched_outcome=TestOutcome.PASS,
            classification=RegressionClassification.CLEAN_PRESERVATION,
        )

        comp_res = ComparativeExecutionResult(
            baseline_result=base_exec,
            patched_result=patch_exec,
            verdict=PatchVerdict.ACCEPTED,
            test_comparisons=(comp_case,),
            introduced_regressions_count=0,
            resolved_defects_count=0,
            pre_existing_failures_count=0,
            suppressed_tests_count=0,
            summary="All tests cleanly preserved.",
        )

        assert comp_res.verdict == PatchVerdict.ACCEPTED
        assert comp_res.introduced_regressions_count == 0
        assert comp_res.collection_failure_occurred is False

    def test_comparative_result_introduced_regression(self) -> None:
        base_exec = ExecutionResult(exit_code=0, stdout="OK", stderr="", duration_seconds=0.2)
        patch_exec = ExecutionResult(exit_code=1, stdout="FAIL", stderr="", duration_seconds=0.2)

        comp_case = ComparativeTestCase(
            test_name="tests/test_app.py::test_login",
            baseline_outcome=TestOutcome.PASS,
            patched_outcome=TestOutcome.FAIL,
            classification=RegressionClassification.INTRODUCED_REGRESSION,
            message="Test failed after patch.",
        )

        comp_res = ComparativeExecutionResult(
            baseline_result=base_exec,
            patched_result=patch_exec,
            verdict=PatchVerdict.REJECTED,
            test_comparisons=(comp_case,),
            introduced_regressions_count=1,
            resolved_defects_count=0,
            pre_existing_failures_count=0,
            suppressed_tests_count=0,
            summary="Patch introduced 1 regression.",
        )

        assert comp_res.verdict == PatchVerdict.REJECTED
        assert comp_res.introduced_regressions_count == 1

    def test_comparative_result_collection_failure(self) -> None:
        base_exec = ExecutionResult(exit_code=0, stdout="OK", stderr="", duration_seconds=0.2)
        patch_exec = ExecutionResult(
            exit_code=2,
            stdout="",
            stderr="SyntaxError",
            duration_seconds=0.1,
            failure_reason=SandboxFailureReason.COLLECTION_FAILURE,
        )

        comp_res = ComparativeExecutionResult(
            baseline_result=base_exec,
            patched_result=patch_exec,
            verdict=PatchVerdict.REJECTED,
            test_comparisons=(),
            collection_failure_occurred=True,
            summary="Patched code failed test collection.",
        )

        assert comp_res.verdict == PatchVerdict.REJECTED
        assert comp_res.collection_failure_occurred is True

    def test_verdict_consistency_regression_rejected(self) -> None:
        base_exec = ExecutionResult(exit_code=0, stdout="OK", stderr="", duration_seconds=0.2)
        patch_exec = ExecutionResult(exit_code=1, stdout="FAIL", stderr="", duration_seconds=0.2)

        with pytest.raises(
            (ValidationError, SandboxSecurityError),
            match="Verdict cannot be ACCEPTED when introduced regressions exist",
        ):
            ComparativeExecutionResult(
                baseline_result=base_exec,
                patched_result=patch_exec,
                verdict=PatchVerdict.ACCEPTED,  # Inconsistent with regression!
                introduced_regressions_count=2,
            )

    def test_verdict_consistency_collection_failure_rejected(self) -> None:
        base_exec = ExecutionResult(exit_code=0, stdout="OK", stderr="", duration_seconds=0.2)
        patch_exec = ExecutionResult(exit_code=2, stdout="", stderr="Crash", duration_seconds=0.1)

        with pytest.raises(
            (ValidationError, SandboxSecurityError),
            match="Verdict must be REJECTED when collection failure occurs",
        ):
            ComparativeExecutionResult(
                baseline_result=base_exec,
                patched_result=patch_exec,
                verdict=PatchVerdict.ACCEPTED,  # Inconsistent with collection failure!
                collection_failure_occurred=True,
            )
