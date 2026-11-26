"""Unit and adversarial tests for workspace synchronization and staging isolation."""

from __future__ import annotations

import pathlib
import tempfile
from unittest.mock import MagicMock, patch

import pytest

from patchproof.models.sandbox import (
    ContainerConfig,
    ExecutionResult,
    PatchVerdict,
    SandboxFailureReason,
)
from patchproof.sandbox.comparative import ComparativeRunner
from patchproof.sandbox.docker_cli import DockerCommandBuilder
from patchproof.sandbox.runner import ContainerRunner


@pytest.fixture
def sample_repo(tmp_path: pathlib.Path) -> pathlib.Path:
    """Fixture providing a mock repository with source code and tests."""
    repo = tmp_path / "sample_project"
    repo.mkdir()
    (repo / "math_lib.py").write_text("def mul(a, b):\n    return a * b\n", encoding="utf-8")
    (repo / "test_math.py").write_text(
        "from math_lib import mul\n\ndef test_mul():\n    assert mul(2, 3) == 6\n",
        encoding="utf-8",
    )
    return repo


class TestWorkspaceSynchronization:
    """Tests verifying workspace mounting, source availability, and host repository immutability."""

    def test_staging_mount_targets_container_workspace(self, tmp_path: pathlib.Path) -> None:
        """Verify DockerCommandBuilder mounts staging directory directly to /workspace."""
        staging_dir = tmp_path / "patchproof-staging-test-123"
        staging_dir.mkdir()

        config = ContainerConfig(
            host_repo_path=str(staging_dir),
            container_workspace_path="/workspace",
        )
        builder = DockerCommandBuilder(config)
        cmd = builder.build()

        # Mount target must be /workspace
        workspace_mount = [arg for arg in cmd if "type=bind" in arg and "target=/workspace" in arg]
        assert len(workspace_mount) == 1
        assert f"source={staging_dir.resolve()}" in workspace_mount[0]

        # Working directory must be /workspace
        assert "-w" in cmd
        assert cmd[cmd.index("-w") + 1] == "/workspace"

        # Git mask must protect /workspace/.git
        git_mask = [
            arg for arg in cmd if "type=tmpfs" in arg and "destination=/workspace/.git" in arg
        ]
        assert len(git_mask) == 1

    def test_non_staging_directory_mounted_read_only(self, tmp_path: pathlib.Path) -> None:
        """Verify any host path without patchproof-staging- prefix is mounted readonly."""
        normal_repo = tmp_path / "regular_repo"
        normal_repo.mkdir()

        config = ContainerConfig(
            host_repo_path=str(normal_repo),
            container_workspace_path="/workspace",
        )
        builder = DockerCommandBuilder(config)
        cmd = builder.build()

        workspace_mount = [arg for arg in cmd if "type=bind" in arg and "target=/workspace" in arg]
        assert len(workspace_mount) == 1
        assert "readonly=true" in workspace_mount[0]

    def test_staging_directory_mounted_writable(self, tmp_path: pathlib.Path) -> None:
        """Verify staging directory inside tempdir is mounted writable for test caches."""
        temp_root = pathlib.Path(tempfile.gettempdir())
        staging_dir = temp_root / "patchproof-staging-writable-test"
        staging_dir.mkdir(exist_ok=True)

        try:
            config = ContainerConfig(
                host_repo_path=str(staging_dir),
                container_workspace_path="/workspace",
            )
            builder = DockerCommandBuilder(config)
            cmd = builder.build()

            workspace_mount = [
                arg for arg in cmd if "type=bind" in arg and "target=/workspace" in arg
            ]
            assert len(workspace_mount) == 1
            # Must NOT be readonly so pytest can generate caches and test outputs
            assert "readonly=true" not in workspace_mount[0]
        finally:
            staging_dir.rmdir()

    @patch.object(ContainerRunner, "run")
    def test_baseline_and_patched_run_in_separate_isolated_workspaces(
        self,
        mock_runner_run: MagicMock,
        sample_repo: pathlib.Path,
    ) -> None:
        """Verify baseline and patched runs execute against separate staging directories."""
        executed_configs: list[ContainerConfig] = []

        def capture_run(cfg: ContainerConfig) -> ExecutionResult:
            executed_configs.append(cfg)
            return ExecutionResult(
                exit_code=0,
                stdout="test_math.py::test_mul PASSED [100%]\n==== 1 passed in 0.05s ====\n",
                duration_seconds=0.05,
            )

        mock_runner_run.side_effect = capture_run

        patch_content = (
            "--- a/math_lib.py\n"
            "+++ b/math_lib.py\n"
            "@@ -1,2 +1,3 @@\n"
            " def mul(a, b):\n"
            "+    # verified multiplication\n"
            "     return a * b\n"
        )

        orig_before = (sample_repo / "math_lib.py").read_text(encoding="utf-8")

        comp_runner = ComparativeRunner()
        config = ContainerConfig(host_repo_path=str(sample_repo))
        result = comp_runner.run(config, patch_content)

        assert result.verdict == PatchVerdict.ACCEPTED
        assert len(executed_configs) == 2

        base_cfg, patch_cfg = executed_configs
        # Baseline and patched runs must execute on distinct paths
        assert base_cfg.host_repo_path != patch_cfg.host_repo_path
        # Neither run may execute directly against the original repository
        assert base_cfg.host_repo_path != str(sample_repo.resolve())
        assert patch_cfg.host_repo_path != str(sample_repo.resolve())
        # Both paths must be staging directories
        assert "patchproof-staging-base-" in base_cfg.host_repo_path
        assert "patchproof-staging-patch-" in patch_cfg.host_repo_path

        # Original repo must remain completely unmodified
        orig_after = (sample_repo / "math_lib.py").read_text(encoding="utf-8")
        assert orig_before == orig_after

    @patch.object(ContainerRunner, "run")
    def test_host_repo_not_polluted_by_temporary_files_or_caches(
        self,
        mock_runner_run: MagicMock,
        sample_repo: pathlib.Path,
    ) -> None:
        """Verify test caches (.pytest_cache, __pycache__) do not pollute the host repository."""

        def simulate_test_run(cfg: ContainerConfig) -> ExecutionResult:
            # Simulate pytest writing caches to the staging workspace
            staging_path = pathlib.Path(cfg.host_repo_path)
            cache_dir = staging_path / ".pytest_cache"
            cache_dir.mkdir(exist_ok=True)
            (cache_dir / "README.md").write_text("pytest cache", encoding="utf-8")
            (staging_path / "temp_output.log").write_text("test output", encoding="utf-8")

            return ExecutionResult(
                exit_code=0,
                stdout="test_math.py::test_mul PASSED [100%]\n==== 1 passed in 0.05s ====\n",
                duration_seconds=0.05,
            )

        mock_runner_run.side_effect = simulate_test_run

        patch_content = (
            "--- a/math_lib.py\n"
            "+++ b/math_lib.py\n"
            "@@ -1,2 +1,2 @@\n"
            " def mul(a, b):\n"
            "-    return a * b\n"
            "+    return b * a\n"
        )

        comp_runner = ComparativeRunner()
        config = ContainerConfig(host_repo_path=str(sample_repo))
        result = comp_runner.run(config, patch_content)

        assert result.verdict == PatchVerdict.ACCEPTED

        # Ensure original host repository has NO .pytest_cache or temp_output.log
        assert not (sample_repo / ".pytest_cache").exists()
        assert not (sample_repo / "temp_output.log").exists()

    @patch.object(ContainerRunner, "run")
    def test_workspace_cleaned_up_on_execution_failure(
        self,
        mock_runner_run: MagicMock,
        sample_repo: pathlib.Path,
    ) -> None:
        """Verify staging workspaces are guaranteed to be cleaned up on timeout/failure."""
        staging_paths_seen: list[pathlib.Path] = []

        def failing_run(cfg: ContainerConfig) -> ExecutionResult:
            p = pathlib.Path(cfg.host_repo_path)
            staging_paths_seen.append(p)
            return ExecutionResult(
                exit_code=137,
                duration_seconds=5.0,
                failure_reason=SandboxFailureReason.TIMEOUT,
                timeout_triggered=True,
            )

        mock_runner_run.side_effect = failing_run

        patch_content = (
            "--- a/math_lib.py\n"
            "+++ b/math_lib.py\n"
            "@@ -1,2 +1,2 @@\n"
            " def mul(a, b):\n"
            "-    return a * b\n"
            "+    return 0\n"
        )

        comp_runner = ComparativeRunner()
        config = ContainerConfig(host_repo_path=str(sample_repo))
        result = comp_runner.run(config, patch_content)

        assert result.verdict == PatchVerdict.INFRASTRUCTURE_FAILURE
        assert len(staging_paths_seen) >= 1

        # Verify all staged directories have been safely cleaned up
        for staged in staging_paths_seen:
            assert not staged.exists(), f"Staging workspace '{staged}' was not cleaned up!"
