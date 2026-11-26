"""Unit and integration tests for ComparativeRunner and dual-run orchestration."""

from __future__ import annotations

import pathlib
import tempfile
from unittest.mock import MagicMock, patch

import pytest

from patchproof.core.applier import apply_patch
from patchproof.exceptions import SandboxSecurityError
from patchproof.models.sandbox import (
    ContainerConfig,
    ExecutionResult,
    PatchVerdict,
    RegressionClassification,
    ResourceLimits,
    SandboxFailureReason,
)
from patchproof.sandbox.comparative import (
    ComparativeRunner,
    safe_cleanup_staging,
    safe_copy_repository,
)
from patchproof.sandbox.runner import (
    ContainerRunner,
    is_docker_available,
)


@pytest.fixture
def repo_dir(tmp_path: pathlib.Path) -> pathlib.Path:
    repo = tmp_path / "original_repo"
    repo.mkdir()
    (repo / "calc.py").write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
    (repo / "test_calc.py").write_text(
        "from calc import add\n\ndef test_add():\n    assert add(1, 2) == 3\n",
        encoding="utf-8",
    )
    return repo


@pytest.fixture
def config(repo_dir: pathlib.Path) -> ContainerConfig:
    return ContainerConfig(
        host_repo_path=str(repo_dir),
        resources=ResourceLimits(timeout_seconds=5.0),
    )


class TestComparativeRunnerOrchestration:
    """Test full dual-run comparative execution workflow with mocked container runs."""

    @patch.object(ContainerRunner, "run")
    def test_comparative_runner_clean_patch_accepted(
        self,
        mock_runner_run: MagicMock,
        config: ContainerConfig,
        repo_dir: pathlib.Path,
    ) -> None:
        stdout_base = "test_calc.py::test_add PASSED [100%]\n==== 1 passed in 0.05s ====\n"
        stdout_patch = (
            "test_calc.py::test_add PASSED [ 50%]\n"
            "test_calc.py::test_new PASSED [100%]\n"
            "==== 2 passed in 0.08s ====\n"
        )

        mock_runner_run.side_effect = [
            ExecutionResult(exit_code=0, stdout=stdout_base, duration_seconds=0.05),
            ExecutionResult(exit_code=0, stdout=stdout_patch, duration_seconds=0.08),
        ]

        patch_diff = (
            "--- a/test_calc.py\n"
            "+++ b/test_calc.py\n"
            "@@ -3,2 +3,5 @@\n"
            " def test_add():\n"
            "     assert add(1, 2) == 3\n"
            "+\n"
            "+def test_new():\n"
            "+    assert add(2, 2) == 4\n"
        )

        orig_content_before = (repo_dir / "test_calc.py").read_text(encoding="utf-8")

        comp_runner = ComparativeRunner()
        result = comp_runner.run(config, patch_diff)

        assert result.verdict == PatchVerdict.ACCEPTED
        assert result.introduced_regressions_count == 0
        assert len(result.test_comparisons) == 2

        # Verify original workspace was strictly preserved
        orig_content_after = (repo_dir / "test_calc.py").read_text(encoding="utf-8")
        assert orig_content_before == orig_content_after

    @patch.object(ContainerRunner, "run")
    def test_comparative_runner_introduced_regression_rejected(
        self,
        mock_runner_run: MagicMock,
        config: ContainerConfig,
    ) -> None:
        stdout_base = "test_calc.py::test_add PASSED [100%]\n==== 1 passed in 0.05s ====\n"
        stdout_patch = "test_calc.py::test_add FAILED [100%]\n==== 1 failed in 0.08s ====\n"

        mock_runner_run.side_effect = [
            ExecutionResult(exit_code=0, stdout=stdout_base, duration_seconds=0.05),
            ExecutionResult(exit_code=1, stdout=stdout_patch, duration_seconds=0.08),
        ]

        patch_diff = (
            "--- a/calc.py\n"
            "+++ b/calc.py\n"
            "@@ -1,2 +1,2 @@\n"
            " def add(a, b):\n"
            "-    return a + b\n"
            "+    return a - b\n"
        )

        comp_runner = ComparativeRunner()
        result = comp_runner.run(config, patch_diff)

        assert result.verdict == PatchVerdict.REJECTED
        assert result.introduced_regressions_count == 1
        comp = result.test_comparisons[0]
        assert comp.classification == RegressionClassification.INTRODUCED_REGRESSION

    @patch.object(ContainerRunner, "run")
    def test_comparative_runner_resolves_defect(
        self,
        mock_runner_run: MagicMock,
        config: ContainerConfig,
        repo_dir: pathlib.Path,
    ) -> None:
        # Prepare calc.py with buggy implementation
        (repo_dir / "calc.py").write_text("def add(a, b):\n    return a - b\n", encoding="utf-8")

        stdout_base = "test_calc.py::test_add FAILED [100%]\n==== 1 failed in 0.05s ====\n"
        stdout_patch = "test_calc.py::test_add PASSED [100%]\n==== 1 passed in 0.08s ====\n"

        mock_runner_run.side_effect = [
            ExecutionResult(
                exit_code=1,
                stdout=stdout_base,
                duration_seconds=0.05,
                failure_reason=SandboxFailureReason.EXECUTION_ERROR,
            ),
            ExecutionResult(
                exit_code=0,
                stdout=stdout_patch,
                duration_seconds=0.08,
                failure_reason=SandboxFailureReason.NONE,
            ),
        ]

        patch_diff = (
            "--- a/calc.py\n"
            "+++ b/calc.py\n"
            "@@ -1,2 +1,2 @@\n"
            " def add(a, b):\n"
            "-    return a - b\n"
            "+    return a + b\n"
        )

        comp_runner = ComparativeRunner()
        result = comp_runner.run(config, patch_diff)

        assert result.verdict == PatchVerdict.ACCEPTED
        assert result.resolved_defects_count == 1

    @patch.object(ContainerRunner, "run")
    def test_comparative_runner_patch_application_failure(
        self,
        mock_runner_run: MagicMock,
        config: ContainerConfig,
    ) -> None:
        # Baseline passes
        stdout_base = "test_calc.py::test_add PASSED [100%]\n==== 1 passed in 0.05s ====\n"
        mock_runner_run.return_value = ExecutionResult(
            exit_code=0, stdout=stdout_base, duration_seconds=0.05
        )

        # Patch contains non-matching context
        invalid_patch = (
            "--- a/calc.py\n+++ b/calc.py\n@@ -1,2 +1,2 @@\n def non_existent():\n-    x\n+    y\n"
        )

        comp_runner = ComparativeRunner()
        result = comp_runner.run(config, invalid_patch)

        assert result.verdict == PatchVerdict.REJECTED
        assert (
            "setup failed" in result.summary.lower() or "context mismatch" in result.summary.lower()
        )
        # Patched container should NOT have been invoked
        assert mock_runner_run.call_count == 1

    @patch.object(ContainerRunner, "run")
    def test_comparative_runner_collection_failure_in_patch(
        self,
        mock_runner_run: MagicMock,
        config: ContainerConfig,
    ) -> None:
        stdout_base = "test_calc.py::test_add PASSED [100%]\n==== 1 passed in 0.05s ====\n"
        stdout_patch = (
            "=================================== ERRORS ====================================\n"
            "___________________ ERROR collecting test_calc.py ____________________\n"
            "SyntaxError: invalid syntax\n"
            "============================== 1 error in 0.05s ===============================\n"
        )

        mock_runner_run.side_effect = [
            ExecutionResult(exit_code=0, stdout=stdout_base, duration_seconds=0.05),
            ExecutionResult(exit_code=2, stdout=stdout_patch, duration_seconds=0.05),
        ]

        patch_diff = (
            "--- a/calc.py\n"
            "+++ b/calc.py\n"
            "@@ -1,2 +1,2 @@\n"
            " def add(a, b):\n"
            "-    return a + b\n"
            "+    return a ++\n"
        )

        comp_runner = ComparativeRunner()
        result = comp_runner.run(config, patch_diff)

        assert result.verdict == PatchVerdict.REJECTED
        assert result.collection_failure_occurred is True

    @patch.object(ContainerRunner, "run")
    def test_comparative_runner_baseline_infrastructure_failure(
        self,
        mock_runner_run: MagicMock,
        config: ContainerConfig,
    ) -> None:
        # Baseline times out
        mock_runner_run.return_value = ExecutionResult(
            exit_code=137,
            duration_seconds=5.0,
            failure_reason=SandboxFailureReason.TIMEOUT,
            timeout_triggered=True,
        )

        valid_patch = (
            "--- a/calc.py\n"
            "+++ b/calc.py\n"
            "@@ -1,2 +1,2 @@\n"
            " def add(a, b):\n"
            "-    return a + b\n"
            "+    return a * b\n"
        )

        comp_runner = ComparativeRunner()
        result = comp_runner.run(config, valid_patch)

        assert result.verdict == PatchVerdict.INFRASTRUCTURE_FAILURE
        # Patched container should NOT have been invoked
        assert mock_runner_run.call_count == 1

    def test_comparative_runner_invalid_patch_format_rejects_immediately(
        self,
        config: ContainerConfig,
    ) -> None:
        comp_runner = ComparativeRunner()
        result = comp_runner.run(config, "completely invalid patch without diff markers")

        assert result.verdict == PatchVerdict.REJECTED
        assert "invalid patch format" in result.summary.lower()

    @patch.object(ContainerRunner, "run")
    def test_comparative_runner_timeout_in_patched_run(
        self,
        mock_runner_run: MagicMock,
        config: ContainerConfig,
    ) -> None:
        stdout_base = "test_calc.py::test_add PASSED [100%]\n==== 1 passed in 0.05s ====\n"
        mock_runner_run.side_effect = [
            ExecutionResult(exit_code=0, stdout=stdout_base, duration_seconds=0.05),
            ExecutionResult(
                exit_code=137,
                duration_seconds=5.0,
                failure_reason=SandboxFailureReason.TIMEOUT,
                timeout_triggered=True,
            ),
        ]

        patch_diff = (
            "--- a/calc.py\n"
            "+++ b/calc.py\n"
            "@@ -1,2 +1,3 @@\n"
            " def add(a, b):\n"
            "+    while True: pass\n"
            "     return a + b\n"
        )

        comp_runner = ComparativeRunner()
        result = comp_runner.run(config, patch_diff)

        assert result.verdict == PatchVerdict.REJECTED
        assert "timeout" in result.summary.lower()

    @patch.object(ContainerRunner, "run")
    def test_comparative_runner_disambiguate_flakes(
        self,
        mock_runner_run: MagicMock,
        config: ContainerConfig,
    ) -> None:
        stdout_base = "test_calc.py::test_add PASSED [100%]\n==== 1 passed in 0.05s ====\n"
        stdout_patch = "test_calc.py::test_add FAILED [100%]\n==== 1 failed in 0.05s ====\n"
        retry1 = ExecutionResult(exit_code=1, stdout="FAILED", duration_seconds=0.01)
        retry2 = ExecutionResult(exit_code=0, stdout="PASSED", duration_seconds=0.01)
        retry3 = ExecutionResult(exit_code=1, stdout="FAILED", duration_seconds=0.01)

        mock_runner_run.side_effect = [
            ExecutionResult(exit_code=0, stdout=stdout_base, duration_seconds=0.05),
            ExecutionResult(exit_code=1, stdout=stdout_patch, duration_seconds=0.05),
            retry1,
            retry2,
            retry3,
        ]

        patch_diff = (
            "--- a/calc.py\n"
            "+++ b/calc.py\n"
            "@@ -1,2 +1,2 @@\n"
            " def add(a, b):\n"
            "-    return a + b\n"
            "+    return a + b + 0\n"
        )

        comp_runner = ComparativeRunner()
        result = comp_runner.run(config, patch_diff, disambiguate_flakes=True)

        assert result.verdict == PatchVerdict.SUSPICIOUS
        assert result.flaky_tests_count == 1
        assert result.introduced_regressions_count == 0
        assert (
            result.test_comparisons[0].classification == RegressionClassification.FLAKY_REGRESSION
        )


class TestStagingAndSymlinkSecurity:
    """Adversarial tests for symlink traversal prevention and staging directory isolation."""

    def test_staging_copy_rejects_symlink_pointing_outside_repository(
        self, tmp_path: pathlib.Path
    ) -> None:
        repo = tmp_path / "repo_with_link"
        repo.mkdir()
        (repo / "safe.txt").write_text("safe content", encoding="utf-8")

        outside_secret = tmp_path / "secret.txt"
        outside_secret.write_text("SUPER_SECRET_KEY", encoding="utf-8")

        symlink_path = repo / "link_to_secret"
        try:
            symlink_path.symlink_to(outside_secret)
        except (OSError, NotImplementedError):
            pytest.skip("Symlink creation requires elevated privileges on Windows")

        staging_dst = tmp_path / "staging_dst"
        with pytest.raises(SandboxSecurityError, match="Symlink traversal detected"):
            safe_copy_repository(repo, staging_dst)

    def test_staging_copy_detects_symlink_traversal_mocked(self, tmp_path: pathlib.Path) -> None:
        repo = tmp_path / "mocked_repo"
        repo.mkdir()
        fake_file = repo / "linked_file"
        fake_file.write_text("content", encoding="utf-8")

        staging_dst = tmp_path / "staging_dst_mocked"

        real_resolve = pathlib.Path.resolve

        def mock_resolve(self_path: pathlib.Path, strict: bool = False) -> pathlib.Path:
            if self_path.name == "linked_file":
                return pathlib.Path("/outside/secrets")
            return real_resolve(self_path, strict=strict)

        def mock_is_symlink(self_path: pathlib.Path) -> bool:
            return self_path.name == "linked_file"

        with (
            patch("pathlib.Path.is_symlink", side_effect=mock_is_symlink, autospec=True),
            patch("pathlib.Path.resolve", side_effect=mock_resolve, autospec=True),
        ):
            with pytest.raises(SandboxSecurityError, match="Symlink traversal detected"):
                safe_copy_repository(repo, staging_dst)

    def test_staging_cleanup_refuses_to_delete_non_temp_dir(self, tmp_path: pathlib.Path) -> None:
        # Non-temp directory
        non_temp = tmp_path / "non_temp_dir"
        non_temp.mkdir()
        safe_cleanup_staging(non_temp)
        assert non_temp.exists()

        # Inside temp dir but missing patchproof-staging- prefix
        temp_dir = pathlib.Path(tempfile.gettempdir())
        unprefixed = temp_dir / "unprefixed_test_dir_not_ours"
        unprefixed.mkdir(exist_ok=True)
        try:
            safe_cleanup_staging(unprefixed)
            assert unprefixed.exists()
        finally:
            unprefixed.rmdir()

    def test_patch_applier_rejects_targeting_symlink(self, tmp_path: pathlib.Path) -> None:
        ws = tmp_path / "ws_symlink"
        ws.mkdir()
        target_file = ws / "target.py"
        target_file.write_text("x = 1\n", encoding="utf-8")

        link_file = ws / "link.py"
        try:
            link_file.symlink_to(target_file)
        except (OSError, NotImplementedError):
            pytest.skip("Symlink creation requires elevated privileges on Windows")

        diff = "--- a/link.py\n+++ b/link.py\n@@ -1,1 +1,1 @@\n-x = 1\n+x = 2\n"
        with pytest.raises(SandboxSecurityError, match="cannot target a symbolic link"):
            apply_patch(diff, ws)

    @patch.object(ContainerRunner, "run")
    def test_comparative_runner_rejects_malicious_symlink_repo(
        self,
        mock_runner_run: MagicMock,
        tmp_path: pathlib.Path,
    ) -> None:
        repo = tmp_path / "malicious_repo"
        repo.mkdir()
        outside_target = tmp_path / "host_sensitive.txt"
        outside_target.write_text("sensitive", encoding="utf-8")

        symlink_entry = repo / "evil_link"
        try:
            symlink_entry.symlink_to(outside_target)
        except (OSError, NotImplementedError):
            pytest.skip("Symlink creation requires elevated privileges on Windows")

        config = ContainerConfig(
            host_repo_path=str(repo),
            resources=ResourceLimits(timeout_seconds=5.0),
        )

        mock_runner_run.return_value = ExecutionResult(
            exit_code=0, stdout="==== 1 passed in 0.01s ====\n", duration_seconds=0.01
        )

        valid_patch = "--- a/file.py\n+++ b/file.py\n@@ -0,0 +1,1 @@\n+test\n"

        comp_runner = ComparativeRunner()
        result = comp_runner.run(config, valid_patch)

        assert result.verdict == PatchVerdict.REJECTED
        assert "security violation" in result.summary.lower()


class TestComparativeIntegration:
    """Docker-gated integration tests for ComparativeRunner."""

    @pytest.mark.skipif(
        not is_docker_available(),
        reason="Docker daemon not available or not Linux mode",
    )
    def test_live_docker_comparative_evaluation(self, repo_dir: pathlib.Path) -> None:
        config = ContainerConfig(
            image="alpine:latest",
            host_repo_path=str(repo_dir),
            entrypoint_command=("sh", "-c", "echo 'test_calc.py::test_add PASSED'"),
            resources=ResourceLimits(timeout_seconds=10.0),
        )

        patch_diff = (
            "--- a/calc.py\n"
            "+++ b/calc.py\n"
            "@@ -1,2 +1,3 @@\n"
            " def add(a, b):\n"
            "+    # comment\n"
            "     return a + b\n"
        )

        runner = ComparativeRunner()
        result = runner.run(config, patch_diff)

        assert result.verdict == PatchVerdict.ACCEPTED
