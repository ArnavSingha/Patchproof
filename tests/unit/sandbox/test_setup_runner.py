"""Unit tests for PatchProof setup-runner command and image builder."""

from __future__ import annotations

import io
import pathlib
import subprocess
from unittest.mock import MagicMock, patch

from patchproof.exceptions import DockerUnavailableError
from patchproof.sandbox.docker_cli import DockerPreflightResult
from patchproof.sandbox.setup_runner import (
    build_runner_image,
    resolve_default_dockerfile,
)


class TestSetupRunner:
    """Test suite for runner image building and CLI workflow."""

    def test_resolve_default_dockerfile_locates_existing_file(self) -> None:
        path = resolve_default_dockerfile()
        assert path.exists()
        assert path.name == "runner.Dockerfile"

    @patch("patchproof.sandbox.setup_runner.DockerPreflight.check")
    @patch("subprocess.run")
    def test_build_runner_image_success(
        self,
        mock_run: MagicMock,
        mock_preflight: MagicMock,
        tmp_path: pathlib.Path,
    ) -> None:
        mock_preflight.return_value = DockerPreflightResult(
            is_available=True,
            os_type="linux",
            binary_path="/usr/bin/docker",
        )
        mock_run.return_value = subprocess.CompletedProcess(
            args=["docker", "build"],
            returncode=0,
            stdout="Successfully built abc123",
            stderr="",
        )

        dockerfile = tmp_path / "test.Dockerfile"
        dockerfile.write_text("FROM python:3.11-slim\n", encoding="utf-8")

        stdout_buf = io.StringIO()
        stderr_buf = io.StringIO()

        code = build_runner_image(
            image_tag="test-tag:latest",
            dockerfile_path=dockerfile,
            no_cache=False,
            stdout_stream=stdout_buf,
            stderr_stream=stderr_buf,
        )

        assert code == 0
        assert "Successfully built and tagged" in stdout_buf.getvalue()
        mock_run.assert_called_once()
        args = mock_run.call_args[0][0]
        assert args[1] == "build"
        assert "-t" in args
        assert args[args.index("-t") + 1] == "test-tag:latest"
        assert "-f" in args
        assert args[args.index("-f") + 1] == str(dockerfile.resolve())
        assert "--no-cache" not in args

    @patch("patchproof.sandbox.setup_runner.DockerPreflight.check")
    @patch("subprocess.run")
    def test_build_runner_image_with_no_cache(
        self,
        mock_run: MagicMock,
        mock_preflight: MagicMock,
        tmp_path: pathlib.Path,
    ) -> None:
        mock_preflight.return_value = DockerPreflightResult(
            is_available=True,
            os_type="linux",
            binary_path="/usr/bin/docker",
        )
        mock_run.return_value = subprocess.CompletedProcess(
            args=["docker", "build"],
            returncode=0,
            stdout="",
            stderr="",
        )

        dockerfile = tmp_path / "test.Dockerfile"
        dockerfile.write_text("FROM python:3.11-slim\n", encoding="utf-8")

        code = build_runner_image(
            image_tag="test-tag:latest",
            dockerfile_path=dockerfile,
            no_cache=True,
            stdout_stream=io.StringIO(),
            stderr_stream=io.StringIO(),
        )

        assert code == 0
        args = mock_run.call_args[0][0]
        assert "--no-cache" in args

    @patch("patchproof.sandbox.setup_runner.DockerPreflight.check")
    def test_build_runner_image_fails_when_docker_unavailable(
        self,
        mock_preflight: MagicMock,
        tmp_path: pathlib.Path,
    ) -> None:
        mock_preflight.side_effect = DockerUnavailableError("Docker daemon offline")

        dockerfile = tmp_path / "test.Dockerfile"
        dockerfile.write_text("FROM python:3.11-slim\n", encoding="utf-8")

        stderr_buf = io.StringIO()
        code = build_runner_image(
            dockerfile_path=dockerfile,
            stderr_stream=stderr_buf,
        )

        assert code == 1
        assert "Docker is unavailable" in stderr_buf.getvalue()
        assert "Remediation" in stderr_buf.getvalue()

    @patch("patchproof.sandbox.setup_runner.DockerPreflight.check")
    def test_build_runner_image_fails_when_dockerfile_missing(
        self,
        mock_preflight: MagicMock,
        tmp_path: pathlib.Path,
    ) -> None:
        mock_preflight.return_value = DockerPreflightResult(
            is_available=True,
            os_type="linux",
            binary_path="/usr/bin/docker",
        )

        nonexistent = tmp_path / "nonexistent.Dockerfile"
        stderr_buf = io.StringIO()
        code = build_runner_image(
            dockerfile_path=nonexistent,
            stderr_stream=stderr_buf,
        )

        assert code == 1
        assert "Runner Dockerfile not found" in stderr_buf.getvalue()

    @patch("patchproof.sandbox.setup_runner.DockerPreflight.check")
    @patch("subprocess.run")
    def test_build_runner_image_build_failure_returns_exit_code(
        self,
        mock_run: MagicMock,
        mock_preflight: MagicMock,
        tmp_path: pathlib.Path,
    ) -> None:
        mock_preflight.return_value = DockerPreflightResult(
            is_available=True,
            os_type="linux",
            binary_path="/usr/bin/docker",
        )
        mock_run.return_value = subprocess.CompletedProcess(
            args=["docker", "build"],
            returncode=125,
            stdout="",
            stderr="failed to fetch base image",
        )

        dockerfile = tmp_path / "test.Dockerfile"
        dockerfile.write_text("FROM python:3.11-slim\n", encoding="utf-8")

        stderr_buf = io.StringIO()
        code = build_runner_image(
            dockerfile_path=dockerfile,
            stderr_stream=stderr_buf,
        )

        assert code == 125
        assert "docker build failed with exit code 125" in stderr_buf.getvalue()
        assert "failed to fetch base image" in stderr_buf.getvalue()

    @patch("patchproof.sandbox.setup_runner.DockerPreflight.check")
    @patch("subprocess.run")
    def test_build_runner_image_timeout_handling(
        self,
        mock_run: MagicMock,
        mock_preflight: MagicMock,
        tmp_path: pathlib.Path,
    ) -> None:
        mock_preflight.return_value = DockerPreflightResult(
            is_available=True,
            os_type="linux",
            binary_path="/usr/bin/docker",
        )
        mock_run.side_effect = subprocess.TimeoutExpired(cmd=["docker", "build"], timeout=30.0)

        dockerfile = tmp_path / "test.Dockerfile"
        dockerfile.write_text("FROM python:3.11-slim\n", encoding="utf-8")

        stderr_buf = io.StringIO()
        code = build_runner_image(
            dockerfile_path=dockerfile,
            timeout_seconds=30.0,
            stderr_stream=stderr_buf,
        )

        assert code == 1
        assert "timed out after 30.0 seconds" in stderr_buf.getvalue()
