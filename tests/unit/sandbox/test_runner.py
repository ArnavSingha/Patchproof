"""Unit and integration tests for ContainerRunner and lifecycle management."""

from __future__ import annotations

import io
import json
import pathlib
import subprocess
from unittest.mock import MagicMock, patch

import pytest

from patchproof.exceptions import (
    DockerUnavailableError,
    SandboxSecurityError,
    UnsupportedDockerEngineError,
)
from patchproof.models.sandbox import (
    ContainerConfig,
    ExecutionResult,
    ResourceLimits,
    SandboxFailureReason,
)
from patchproof.sandbox.docker_cli import DockerPreflight, DockerPreflightResult
from patchproof.sandbox.runner import (
    ContainerRunner,
    OrphanSweeper,
    is_docker_available,
)


@pytest.fixture
def temp_repo_dir(tmp_path: pathlib.Path) -> str:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "test_file.py").write_text("def test_dummy(): assert True\n", encoding="utf-8")
    return str(repo)


@pytest.fixture
def standard_config(temp_repo_dir: str) -> ContainerConfig:
    return ContainerConfig(
        host_repo_path=temp_repo_dir,
        resources=ResourceLimits(timeout_seconds=5.0),
    )


class TestContainerRunnerLifecycle:
    """Test ContainerRunner execution lifecycle, timeout, OOM, and cleanup behavior."""

    @patch.object(DockerPreflight, "check")
    @patch("subprocess.Popen")
    @patch("subprocess.run")
    def test_runner_successful_execution(
        self,
        mock_subproc_run: MagicMock,
        mock_popen: MagicMock,
        mock_preflight: MagicMock,
        standard_config: ContainerConfig,
    ) -> None:
        mock_preflight.return_value = DockerPreflightResult(
            is_available=True, os_type="linux", binary_path="/usr/bin/docker"
        )

        mock_proc = MagicMock()
        mock_proc.poll.return_value = 0
        mock_proc.returncode = 0
        mock_proc.stdout = io.BytesIO(b"==== 1 passed in 0.01s ====\n")
        mock_proc.stderr = io.BytesIO(b"")
        mock_popen.return_value = mock_proc

        inspect_state = {
            "Status": "exited",
            "ExitCode": 0,
            "OOMKilled": False,
        }
        mock_subproc_run.side_effect = [
            # 1. State inspect
            subprocess.CompletedProcess(
                args=["docker", "inspect"], returncode=0, stdout=json.dumps(inspect_state)
            ),
            # 2. Ownership verification before rm
            subprocess.CompletedProcess(args=["docker", "inspect"], returncode=0, stdout="true\n"),
            # 3. docker rm -f
            subprocess.CompletedProcess(
                args=["docker", "rm", "-f"], returncode=0, stdout="container_name\n"
            ),
        ]

        runner = ContainerRunner()
        result: ExecutionResult = runner.run(standard_config)

        assert result.exit_code == 0
        assert result.is_success is True
        assert result.failure_reason == SandboxFailureReason.NONE
        assert result.oom_killed is False
        assert result.timeout_triggered is False
        assert "1 passed" in result.stdout

        # Verify close_fds and shell=False were passed to Popen
        assert mock_popen.call_args.kwargs.get("shell") is False
        assert mock_popen.call_args.kwargs.get("close_fds") is True

    @patch.object(DockerPreflight, "check")
    @patch("subprocess.Popen")
    @patch("subprocess.run")
    def test_runner_execution_failure_exit_code(
        self,
        mock_subproc_run: MagicMock,
        mock_popen: MagicMock,
        mock_preflight: MagicMock,
        standard_config: ContainerConfig,
    ) -> None:
        mock_preflight.return_value = DockerPreflightResult(
            is_available=True, os_type="linux", binary_path="/usr/bin/docker"
        )

        mock_proc = MagicMock()
        mock_proc.poll.return_value = 1
        mock_proc.returncode = 1
        mock_proc.stdout = io.BytesIO(b"==== 1 failed in 0.02s ====\n")
        mock_proc.stderr = io.BytesIO(b"AssertionError\n")
        mock_popen.return_value = mock_proc

        inspect_state = {
            "Status": "exited",
            "ExitCode": 1,
            "OOMKilled": False,
        }
        mock_subproc_run.side_effect = [
            subprocess.CompletedProcess(
                args=["docker", "inspect"], returncode=0, stdout=json.dumps(inspect_state)
            ),
            subprocess.CompletedProcess(args=["docker", "inspect"], returncode=0, stdout="true\n"),
            subprocess.CompletedProcess(args=["docker", "rm", "-f"], returncode=0, stdout="ok\n"),
        ]

        runner = ContainerRunner()
        result: ExecutionResult = runner.run(standard_config)

        assert result.exit_code == 1
        assert result.is_success is False
        assert result.failure_reason == SandboxFailureReason.EXECUTION_ERROR
        assert result.oom_killed is False
        assert result.timeout_triggered is False

    @patch.object(DockerPreflight, "check")
    @patch("subprocess.Popen")
    @patch("subprocess.run")
    def test_runner_timeout_enforcement(
        self,
        mock_subproc_run: MagicMock,
        mock_popen: MagicMock,
        mock_preflight: MagicMock,
        temp_repo_dir: str,
    ) -> None:
        mock_preflight.return_value = DockerPreflightResult(
            is_available=True, os_type="linux", binary_path="/usr/bin/docker"
        )

        config = ContainerConfig(
            host_repo_path=temp_repo_dir,
            resources=ResourceLimits(timeout_seconds=0.1),
        )

        mock_proc = MagicMock()
        # Simulate container hanging until killed
        mock_proc.poll.return_value = None
        mock_proc.wait.return_value = 137
        mock_proc.returncode = 137
        mock_proc.stdout = io.BytesIO(b"running test...")
        mock_proc.stderr = io.BytesIO(b"")
        mock_popen.return_value = mock_proc

        inspect_state = {
            "Status": "exited",
            "ExitCode": 137,
            "OOMKilled": False,
        }
        mock_subproc_run.side_effect = [
            # docker kill
            subprocess.CompletedProcess(args=["docker", "kill"], returncode=0, stdout="ok\n"),
            # state inspect
            subprocess.CompletedProcess(
                args=["docker", "inspect"], returncode=0, stdout=json.dumps(inspect_state)
            ),
            # ownership check
            subprocess.CompletedProcess(args=["docker", "inspect"], returncode=0, stdout="true\n"),
            # docker rm -f
            subprocess.CompletedProcess(args=["docker", "rm", "-f"], returncode=0, stdout="ok\n"),
        ]

        runner = ContainerRunner()
        result: ExecutionResult = runner.run(config)

        assert result.timeout_triggered is True
        assert result.failure_reason == SandboxFailureReason.TIMEOUT
        assert result.oom_killed is False
        assert result.exit_code == 137

        # Verify docker kill was called
        kill_calls = [
            c for c in mock_subproc_run.call_args_list if c.args[0][0:2] == ["docker", "kill"]
        ]
        assert len(kill_calls) == 1

    @patch.object(DockerPreflight, "check")
    @patch("subprocess.Popen")
    @patch("subprocess.run")
    def test_runner_oom_killed_classification(
        self,
        mock_subproc_run: MagicMock,
        mock_popen: MagicMock,
        mock_preflight: MagicMock,
        standard_config: ContainerConfig,
    ) -> None:
        mock_preflight.return_value = DockerPreflightResult(
            is_available=True, os_type="linux", binary_path="/usr/bin/docker"
        )

        mock_proc = MagicMock()
        mock_proc.poll.return_value = 137
        mock_proc.returncode = 137
        mock_proc.stdout = io.BytesIO(b"")
        mock_proc.stderr = io.BytesIO(b"Killed\n")
        mock_popen.return_value = mock_proc

        inspect_state = {
            "Status": "exited",
            "ExitCode": 137,
            "OOMKilled": True,
        }
        mock_subproc_run.side_effect = [
            subprocess.CompletedProcess(
                args=["docker", "inspect"], returncode=0, stdout=json.dumps(inspect_state)
            ),
            subprocess.CompletedProcess(args=["docker", "inspect"], returncode=0, stdout="true\n"),
            subprocess.CompletedProcess(args=["docker", "rm", "-f"], returncode=0, stdout="ok\n"),
        ]

        runner = ContainerRunner()
        result: ExecutionResult = runner.run(standard_config)

        assert result.exit_code == 137
        assert result.oom_killed is True
        assert result.failure_reason == SandboxFailureReason.OOM_KILLED
        assert result.timeout_triggered is False

    @patch.object(DockerPreflight, "check")
    @patch("subprocess.Popen")
    @patch("subprocess.run")
    def test_runner_exit_code_137_without_oom_or_timeout_is_execution_error(
        self,
        mock_subproc_run: MagicMock,
        mock_popen: MagicMock,
        mock_preflight: MagicMock,
        standard_config: ContainerConfig,
    ) -> None:
        mock_preflight.return_value = DockerPreflightResult(
            is_available=True, os_type="linux", binary_path="/usr/bin/docker"
        )

        mock_proc = MagicMock()
        mock_proc.poll.return_value = 137
        mock_proc.returncode = 137
        mock_proc.stdout = io.BytesIO(b"")
        mock_proc.stderr = io.BytesIO(b"Terminated\n")
        mock_popen.return_value = mock_proc

        inspect_state = {
            "Status": "exited",
            "ExitCode": 137,
            "OOMKilled": False,
        }
        mock_subproc_run.side_effect = [
            subprocess.CompletedProcess(
                args=["docker", "inspect"], returncode=0, stdout=json.dumps(inspect_state)
            ),
            subprocess.CompletedProcess(args=["docker", "inspect"], returncode=0, stdout="true\n"),
            subprocess.CompletedProcess(args=["docker", "rm", "-f"], returncode=0, stdout="ok\n"),
        ]

        runner = ContainerRunner()
        result: ExecutionResult = runner.run(standard_config)

        assert result.exit_code == 137
        assert result.oom_killed is False
        assert result.timeout_triggered is False
        assert result.failure_reason == SandboxFailureReason.EXECUTION_ERROR

    @patch.object(DockerPreflight, "check")
    @patch("subprocess.Popen")
    @patch("subprocess.run")
    def test_inspect_oom_killed_handles_invalid_json(
        self,
        mock_subproc_run: MagicMock,
        mock_popen: MagicMock,
        mock_preflight: MagicMock,
        standard_config: ContainerConfig,
    ) -> None:
        mock_preflight.return_value = DockerPreflightResult(
            is_available=True, os_type="linux", binary_path="/usr/bin/docker"
        )

        mock_proc = MagicMock()
        mock_proc.poll.return_value = 1
        mock_proc.returncode = 1
        mock_proc.stdout = io.BytesIO(b"")
        mock_proc.stderr = io.BytesIO(b"")
        mock_popen.return_value = mock_proc

        mock_subproc_run.side_effect = [
            # Invalid json from inspect
            subprocess.CompletedProcess(
                args=["docker", "inspect"], returncode=0, stdout="not-json\n"
            ),
            # Ownership check during cleanup
            subprocess.CompletedProcess(args=["docker", "inspect"], returncode=0, stdout="true\n"),
            # docker rm -f
            subprocess.CompletedProcess(args=["docker", "rm", "-f"], returncode=0, stdout="ok\n"),
        ]

        runner = ContainerRunner()
        result = runner.run(standard_config)

        assert result.oom_killed is False
        assert result.failure_reason == SandboxFailureReason.EXECUTION_ERROR

    @patch("subprocess.run")
    def test_inspect_oom_killed_handles_timeout(
        self,
        mock_subproc_run: MagicMock,
    ) -> None:
        mock_subproc_run.side_effect = subprocess.TimeoutExpired(cmd="docker inspect", timeout=5.0)

        runner = ContainerRunner()
        assert runner._inspect_oom_killed("container-test") is False

    @patch.object(DockerPreflight, "check")
    @patch("subprocess.Popen")
    @patch("subprocess.run")
    def test_runner_reaps_proc_in_finally_if_still_running(
        self,
        mock_subproc_run: MagicMock,
        mock_popen: MagicMock,
        mock_preflight: MagicMock,
        standard_config: ContainerConfig,
    ) -> None:
        mock_preflight.return_value = DockerPreflightResult(
            is_available=True, os_type="linux", binary_path="/usr/bin/docker"
        )

        mock_proc = MagicMock()
        mock_proc.returncode = None
        # Simulate proc running when exception is raised
        mock_proc.poll.side_effect = KeyboardInterrupt()
        mock_popen.return_value = mock_proc

        mock_subproc_run.side_effect = [
            subprocess.CompletedProcess(args=["docker", "inspect"], returncode=0, stdout="true\n"),
            subprocess.CompletedProcess(args=["docker", "rm", "-f"], returncode=0, stdout="ok\n"),
        ]

        runner = ContainerRunner()
        with pytest.raises(KeyboardInterrupt):
            runner.run(standard_config)

        mock_proc.kill.assert_called_once()
        mock_proc.wait.assert_called_once()

    @patch.object(DockerPreflight, "check")
    @patch("subprocess.Popen")
    @patch("subprocess.run")
    def test_runner_guaranteed_cleanup_on_exception(
        self,
        mock_subproc_run: MagicMock,
        mock_popen: MagicMock,
        mock_preflight: MagicMock,
        standard_config: ContainerConfig,
    ) -> None:
        mock_preflight.return_value = DockerPreflightResult(
            is_available=True, os_type="linux", binary_path="/usr/bin/docker"
        )

        mock_proc = MagicMock()
        mock_proc.returncode = None
        mock_proc.poll.side_effect = RuntimeError("Fatal monitoring error")
        mock_popen.return_value = mock_proc

        mock_subproc_run.side_effect = [
            # Ownership check during cleanup
            subprocess.CompletedProcess(args=["docker", "inspect"], returncode=0, stdout="true\n"),
            # docker rm -f
            subprocess.CompletedProcess(args=["docker", "rm", "-f"], returncode=0, stdout="ok\n"),
        ]

        runner = ContainerRunner()
        with pytest.raises(RuntimeError, match="Fatal monitoring error"):
            runner.run(standard_config)

        # Confirm cleanup docker rm -f was still called in finally block
        rm_calls = [
            c for c in mock_subproc_run.call_args_list if c.args[0][0:3] == ["docker", "rm", "-f"]
        ]
        assert len(rm_calls) == 1

    @patch.object(DockerPreflight, "check")
    @patch("subprocess.Popen")
    @patch("subprocess.run")
    def test_runner_bounded_output_capture(
        self,
        mock_subproc_run: MagicMock,
        mock_popen: MagicMock,
        mock_preflight: MagicMock,
        standard_config: ContainerConfig,
    ) -> None:
        mock_preflight.return_value = DockerPreflightResult(
            is_available=True, os_type="linux", binary_path="/usr/bin/docker"
        )

        large_output = b"A" * (200 * 1024)  # 200 KB
        mock_proc = MagicMock()
        mock_proc.poll.return_value = 0
        mock_proc.returncode = 0
        mock_proc.stdout = io.BytesIO(large_output)
        mock_proc.stderr = io.BytesIO(b"")
        mock_popen.return_value = mock_proc

        inspect_state = {
            "Status": "exited",
            "ExitCode": 0,
            "OOMKilled": False,
        }
        mock_subproc_run.side_effect = [
            subprocess.CompletedProcess(
                args=["docker", "inspect"], returncode=0, stdout=json.dumps(inspect_state)
            ),
            subprocess.CompletedProcess(args=["docker", "inspect"], returncode=0, stdout="true\n"),
            subprocess.CompletedProcess(args=["docker", "rm", "-f"], returncode=0, stdout="ok\n"),
        ]

        runner = ContainerRunner(max_output_bytes=50 * 1024)  # 50 KB bound
        result = runner.run(standard_config)

        assert len(result.stdout) < 60 * 1024
        assert "[TRUNCATED: Output exceeded limit]" in result.stdout

    @patch.object(DockerPreflight, "check")
    def test_runner_fails_closed_when_preflight_fails(
        self,
        mock_preflight: MagicMock,
        standard_config: ContainerConfig,
    ) -> None:
        mock_preflight.side_effect = DockerUnavailableError("Docker daemon is stopped")

        runner = ContainerRunner()
        with pytest.raises(DockerUnavailableError, match="Docker daemon is stopped"):
            runner.run(standard_config)

    @patch.object(DockerPreflight, "check")
    @patch("subprocess.Popen")
    @patch("subprocess.run")
    def test_runner_unique_execution_ids(
        self,
        mock_subproc_run: MagicMock,
        mock_popen: MagicMock,
        mock_preflight: MagicMock,
        standard_config: ContainerConfig,
    ) -> None:
        mock_preflight.return_value = DockerPreflightResult(
            is_available=True, os_type="linux", binary_path="/usr/bin/docker"
        )

        mock_proc = MagicMock()
        mock_proc.poll.return_value = 0
        mock_proc.returncode = 0
        mock_proc.stdout = io.BytesIO(b"ok")
        mock_proc.stderr = io.BytesIO(b"")
        mock_popen.return_value = mock_proc

        inspect_state = {"Status": "exited", "ExitCode": 0, "OOMKilled": False}
        mock_subproc_run.side_effect = [
            subprocess.CompletedProcess(
                args=["docker", "inspect"], returncode=0, stdout=json.dumps(inspect_state)
            ),
            subprocess.CompletedProcess(args=["docker", "inspect"], returncode=0, stdout="true\n"),
            subprocess.CompletedProcess(args=["docker", "rm", "-f"], returncode=0, stdout="ok\n"),
            subprocess.CompletedProcess(
                args=["docker", "inspect"], returncode=0, stdout=json.dumps(inspect_state)
            ),
            subprocess.CompletedProcess(args=["docker", "inspect"], returncode=0, stdout="true\n"),
            subprocess.CompletedProcess(args=["docker", "rm", "-f"], returncode=0, stdout="ok\n"),
        ]

        runner = ContainerRunner()
        runner.run(standard_config)
        runner.run(standard_config)

        # Extract container names from the two subprocess.Popen calls
        first_cmd: list[str] = mock_popen.call_args_list[0].args[0]
        second_cmd: list[str] = mock_popen.call_args_list[1].args[0]

        first_name_idx = first_cmd.index("--name")
        second_name_idx = second_cmd.index("--name")

        first_name = first_cmd[first_name_idx + 1]
        second_name = second_cmd[second_name_idx + 1]

        assert first_name.startswith("patchproof-exec-")
        assert second_name.startswith("patchproof-exec-")
        assert first_name != second_name

    @patch("subprocess.run")
    def test_cleanup_refuses_unmanaged_container(
        self,
        mock_subproc_run: MagicMock,
    ) -> None:
        # Label check returns empty or not "true"
        mock_subproc_run.return_value = subprocess.CompletedProcess(
            args=["docker", "inspect"], returncode=0, stdout="false\n"
        )

        runner = ContainerRunner()
        with pytest.raises(SandboxSecurityError, match="Refusing to clean up container"):
            runner.cleanup_container("unmanaged-container")

        # docker rm -f must NOT be called
        rm_calls = [
            c for c in mock_subproc_run.call_args_list if c.args[0][0:3] == ["docker", "rm", "-f"]
        ]
        assert len(rm_calls) == 0


class TestOrphanSweeper:
    """Test safe detection and pruning of orphaned PatchProof containers."""

    @patch("subprocess.run")
    def test_orphan_sweeper_identifies_and_cleans_managed_containers(
        self,
        mock_subproc_run: MagicMock,
    ) -> None:
        # 1. Query docker ps -a
        ps_output = "id1\tpatchproof-exec-1\t1700000000\nid2\tpatchproof-exec-2\t1700000010\n"
        mock_subproc_run.side_effect = [
            subprocess.CompletedProcess(args=["docker", "ps"], returncode=0, stdout=ps_output),
            # Verify id1 (managed=true, timestamp=1700000000, status=exited)
            subprocess.CompletedProcess(
                args=["docker", "inspect"], returncode=0, stdout="true\t1700000000\texited\n"
            ),
            # rm id1
            subprocess.CompletedProcess(args=["docker", "rm", "-f"], returncode=0, stdout="id1\n"),
            # Verify id2
            subprocess.CompletedProcess(
                args=["docker", "inspect"], returncode=0, stdout="true\t1700000010\texited\n"
            ),
            # rm id2
            subprocess.CompletedProcess(args=["docker", "rm", "-f"], returncode=0, stdout="id2\n"),
        ]

        sweeper = OrphanSweeper()
        cleaned = sweeper.cleanup_orphaned_containers()

        assert cleaned == ["id1", "id2"]

        # Ensure no system prune call
        for c in mock_subproc_run.call_args_list:
            assert "prune" not in " ".join(c.args[0])

    @patch("subprocess.run")
    def test_orphan_sweeper_skips_unverified_containers(
        self,
        mock_subproc_run: MagicMock,
    ) -> None:
        ps_output = "fake_id\tsuspicious_name\t1700000000\n"
        mock_subproc_run.side_effect = [
            subprocess.CompletedProcess(args=["docker", "ps"], returncode=0, stdout=ps_output),
            # Verify label fails
            subprocess.CompletedProcess(args=["docker", "inspect"], returncode=0, stdout="\n"),
        ]

        sweeper = OrphanSweeper()
        cleaned = sweeper.cleanup_orphaned_containers()

        assert cleaned == []
        rm_calls = [
            c for c in mock_subproc_run.call_args_list if c.args[0][0:3] == ["docker", "rm", "-f"]
        ]
        assert len(rm_calls) == 0

    @patch("subprocess.run")
    def test_orphan_sweeper_handles_no_orphans(
        self,
        mock_subproc_run: MagicMock,
    ) -> None:
        mock_subproc_run.return_value = subprocess.CompletedProcess(
            args=["docker", "ps"], returncode=0, stdout=""
        )

        sweeper = OrphanSweeper()
        cleaned = sweeper.cleanup_orphaned_containers()
        assert cleaned == []

    @patch("time.time", return_value=1700000100.0)
    @patch("subprocess.run")
    def test_orphan_sweeper_filters_by_max_age(
        self,
        mock_subproc_run: MagicMock,
        mock_time: MagicMock,
    ) -> None:
        ps_output = "id1\tpatchproof-exec-1\t1700000000\nid2\tpatchproof-exec-2\t1700000080\n"
        mock_subproc_run.side_effect = [
            subprocess.CompletedProcess(args=["docker", "ps"], returncode=0, stdout=ps_output),
            # id1: timestamp 1700000000 -> age 100s >= 60s (eligible)
            subprocess.CompletedProcess(
                args=["docker", "inspect"], returncode=0, stdout="true\t1700000000\texited\n"
            ),
            # rm id1
            subprocess.CompletedProcess(args=["docker", "rm", "-f"], returncode=0, stdout="id1\n"),
            # id2: timestamp 1700000080 -> age 20s < 60s (too young)
            subprocess.CompletedProcess(
                args=["docker", "inspect"], returncode=0, stdout="true\t1700000080\texited\n"
            ),
        ]

        sweeper = OrphanSweeper()
        cleaned = sweeper.cleanup_orphaned_containers(max_age_seconds=60.0)

        assert cleaned == ["id1"]

    @patch("subprocess.run")
    def test_orphan_sweeper_skips_running_when_max_age_zero(
        self,
        mock_subproc_run: MagicMock,
    ) -> None:
        ps_output = "running_id\tpatchproof-exec-run\t1700000000\n"
        mock_subproc_run.side_effect = [
            subprocess.CompletedProcess(args=["docker", "ps"], returncode=0, stdout=ps_output),
            # running container
            subprocess.CompletedProcess(
                args=["docker", "inspect"], returncode=0, stdout="true\t1700000000\trunning\n"
            ),
        ]

        sweeper = OrphanSweeper()
        cleaned = sweeper.cleanup_orphaned_containers(max_age_seconds=0.0)

        assert cleaned == []
        rm_calls = [
            c for c in mock_subproc_run.call_args_list if c.args[0][0:3] == ["docker", "rm", "-f"]
        ]
        assert len(rm_calls) == 0


class TestDockerAvailabilityHelper:
    """Test is_docker_available helper function."""

    @patch.object(DockerPreflight, "check")
    def test_is_docker_available_true_when_healthy(self, mock_check: MagicMock) -> None:
        mock_check.return_value = DockerPreflightResult(
            is_available=True, os_type="linux", binary_path="/usr/bin/docker"
        )
        assert is_docker_available() is True

    @patch.object(DockerPreflight, "check")
    def test_is_docker_available_false_when_unavailable(self, mock_check: MagicMock) -> None:
        mock_check.side_effect = DockerUnavailableError("Daemon down")
        assert is_docker_available() is False

    @patch.object(DockerPreflight, "check")
    def test_is_docker_available_false_when_unsupported_engine(self, mock_check: MagicMock) -> None:
        mock_check.side_effect = UnsupportedDockerEngineError("Windows mode")
        assert is_docker_available() is False


class TestDockerIntegration:
    """Docker-gated integration tests running live ephemeral containers."""

    @pytest.mark.skipif(
        not is_docker_available(),
        reason="Docker daemon not available or not Linux mode",
    )
    def test_live_docker_container_execution(self, temp_repo_dir: str) -> None:
        config = ContainerConfig(
            image="alpine:latest",
            host_repo_path=temp_repo_dir,
            entrypoint_command=("echo", "LIVE_DOCKER_SUCCESS"),
            resources=ResourceLimits(timeout_seconds=10.0),
        )

        runner = ContainerRunner()
        result = runner.run(config)

        assert result.exit_code == 0
        assert result.is_success is True
        assert "LIVE_DOCKER_SUCCESS" in result.stdout
