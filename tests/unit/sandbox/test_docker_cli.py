"""Unit tests for Docker CLI command builder, environment sanitizer, and preflight checks."""

from __future__ import annotations

import pathlib
import re
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
    ContainerEnvironment,
    ResourceLimits,
    SecurityPolicy,
)
from patchproof.sandbox.docker_cli import (
    DockerCommandBuilder,
    DockerPreflight,
    DockerPreflightResult,
    EnvironmentSanitizer,
)


class TestEnvironmentSanitizer:
    """Test environment isolation, allowlisting, and rejection of credentials."""

    def test_default_safe_environment_constructed(self) -> None:
        sanitizer = EnvironmentSanitizer()
        env = sanitizer.sanitize()

        assert "PATH" in env
        assert "LANG" in env
        assert env["PYTHONUNBUFFERED"] == "1"
        assert env["PYTHONDONTWRITEBYTECODE"] == "1"
        assert env["PYTHONPATH"] == "/workspace"

    def test_does_not_inherit_host_os_environ(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("HOST_SECRET_TOKEN", "super_secret_token_123")
        monkeypatch.setenv("AWS_ACCESS_KEY_ID", "AKIAIOSFODNN7EXAMPLE")

        sanitizer = EnvironmentSanitizer()
        env = sanitizer.sanitize()

        assert "HOST_SECRET_TOKEN" not in env
        assert "AWS_ACCESS_KEY_ID" not in env

    def test_allowlisted_custom_variables_pass(self) -> None:
        custom_env = ContainerEnvironment(
            env_vars=(
                ("CI", "true"),
                ("TERM", "xterm-256color"),
                ("PYTEST_ADDOPTS", "-v --tb=short"),
            )
        )
        sanitizer = EnvironmentSanitizer()
        env = sanitizer.sanitize(custom_env)

        assert env["CI"] == "true"
        assert env["TERM"] == "xterm-256color"
        assert env["PYTEST_ADDOPTS"] == "-v --tb=short"

    @pytest.mark.parametrize(
        ("key", "val"),
        [
            ("AWS_SECRET_ACCESS_KEY", "secret"),
            ("GITHUB_TOKEN", "ghp_12345"),
            ("OPENAI_API_KEY", "sk-12345"),
            ("DOCKER_HOST", "tcp://127.0.0.1:2375"),
            ("DATABASE_PASSWORD", "admin123"),
            ("SSH_AUTH_SOCK", "/tmp/ssh.sock"),
            ("UNAPPROVED_SECRET", "val"),
        ],
    )
    def test_reject_sensitive_and_unknown_variables(self, key: str, val: str) -> None:
        sanitizer = EnvironmentSanitizer()
        with pytest.raises(SandboxSecurityError, match="violates credential isolation policy"):
            sanitizer.validate_env_pair(key, val)


class TestDockerCommandBuilder:
    """Test deterministic generation of docker run commands and security flag verification."""

    @pytest.fixture
    def safe_repo_path(self, tmp_path: pathlib.Path) -> str:
        repo_dir = tmp_path / "test_repo"
        repo_dir.mkdir()
        return str(repo_dir)

    def test_build_full_command_argument_list(self, safe_repo_path: str) -> None:
        config = ContainerConfig(
            host_repo_path=safe_repo_path,
            resources=ResourceLimits(
                cpu_count=1.5,
                memory_limit_mb=512,
                pids_limit=64,
                timeout_seconds=10.0,
                workspace_tmpfs_size_mb=128,
            ),
            security=SecurityPolicy(),
            entrypoint_command=("pytest", "-v"),
        )
        builder = DockerCommandBuilder(config)
        cmd = builder.build()

        assert isinstance(cmd, list)
        assert cmd[0] == "docker"
        assert cmd[1] == "run"
        assert "--rm" in cmd

        # Security Invariants
        assert "--network" in cmd
        assert cmd[cmd.index("--network") + 1] == "none"
        assert "--user" in cmd
        assert cmd[cmd.index("--user") + 1] == "10001:10001"
        assert "--cap-drop" in cmd
        assert cmd[cmd.index("--cap-drop") + 1] == "ALL"
        assert "--security-opt" in cmd
        assert cmd[cmd.index("--security-opt") + 1] == "no-new-privileges"
        assert "--read-only" in cmd

        # Resource limits
        assert "--cpus" in cmd
        assert cmd[cmd.index("--cpus") + 1] == "1.5"
        assert "--memory" in cmd
        assert cmd[cmd.index("--memory") + 1] == "512m"
        assert "--memory-swap" in cmd
        assert cmd[cmd.index("--memory-swap") + 1] == "512m"
        assert "--pids-limit" in cmd
        assert cmd[cmd.index("--pids-limit") + 1] == "64"

        # Mounts
        assert any(
            arg.startswith("type=bind,source=") and "target=/workspace" in arg for arg in cmd
        )
        # .git mask
        assert any("destination=/workspace/.git" in arg and "type=tmpfs" in arg for arg in cmd)
        # /tmp tmpfs
        assert any("destination=/tmp" in arg and "type=tmpfs" in arg for arg in cmd)

        # Labels
        assert "--label" in cmd
        assert cmd[cmd.index("--label") + 1] == "patchproof.managed=true"

        # Image & entrypoint
        assert cmd[-2:] == ["pytest", "-v"]

    def test_verify_command_security_passes_valid_command(self, safe_repo_path: str) -> None:
        config = ContainerConfig(host_repo_path=safe_repo_path)
        builder = DockerCommandBuilder(config)
        cmd = builder.build()

        # Should pass without raising
        builder.verify_command_security(cmd)

    @pytest.mark.parametrize(
        "missing_flag",
        [
            "--network",
            "--user",
            "--cap-drop",
            "--security-opt",
            "--read-only",
            "--cpus",
            "--memory",
            "--pids-limit",
        ],
    )
    def test_verify_command_security_fails_if_flag_removed(
        self, safe_repo_path: str, missing_flag: str
    ) -> None:
        config = ContainerConfig(host_repo_path=safe_repo_path)
        builder = DockerCommandBuilder(config)
        cmd = builder.build()

        # Corrupt command by removing the required security flag and its value
        idx = cmd.index(missing_flag)
        corrupted_cmd = cmd[:idx] + cmd[idx + 2 :]

        with pytest.raises(SandboxSecurityError, match="Missing required security flag"):
            builder.verify_command_security(corrupted_cmd)

    def test_verify_command_security_fails_if_network_not_none(self, safe_repo_path: str) -> None:
        config = ContainerConfig(host_repo_path=safe_repo_path)
        builder = DockerCommandBuilder(config)
        cmd = builder.build()

        # Corrupt network flag
        idx = cmd.index("--network")
        cmd[idx + 1] = "bridge"

        with pytest.raises(SandboxSecurityError, match="Invalid network mode"):
            builder.verify_command_security(cmd)

    def test_verify_command_security_fails_if_git_mask_missing(self, safe_repo_path: str) -> None:
        config = ContainerConfig(host_repo_path=safe_repo_path)
        builder = DockerCommandBuilder(config)
        cmd = builder.build()

        # Remove git mask mount
        corrupted_cmd = [c for c in cmd if "/workspace/.git" not in c]

        with pytest.raises(SandboxSecurityError, match="Missing git credential mask"):
            builder.verify_command_security(corrupted_cmd)

    def test_verify_command_security_fails_if_managed_label_missing(
        self, safe_repo_path: str
    ) -> None:
        config = ContainerConfig(host_repo_path=safe_repo_path)
        builder = DockerCommandBuilder(config)
        cmd = builder.build()

        corrupted_cmd = [c for c in cmd if "patchproof.managed=true" not in c]

        with pytest.raises(
            SandboxSecurityError,
            match=re.escape("Missing required label 'patchproof.managed=true'"),
        ):
            builder.verify_command_security(corrupted_cmd)

    def test_reject_nonexistent_host_path(self, tmp_path: pathlib.Path) -> None:
        nonexistent = str(tmp_path / "does_not_exist_repo")
        config = ContainerConfig(host_repo_path=nonexistent)
        builder = DockerCommandBuilder(config)

        with pytest.raises(SandboxSecurityError, match="Host repository path does not exist"):
            builder.build()

    def test_reject_path_traversal_to_sensitive_path(self, tmp_path: pathlib.Path) -> None:
        traversal_path = str(tmp_path / "safe" / ".." / ".." / ".ssh")
        with pytest.raises(SandboxSecurityError, match="prohibited from mount"):
            ContainerConfig(host_repo_path=traversal_path)

    def test_reject_path_traversal_to_nonexistent(self, tmp_path: pathlib.Path) -> None:
        traversal_path = str(tmp_path / "safe" / ".." / ".." / "nonexistent_dir")
        config = ContainerConfig(host_repo_path=traversal_path)
        builder = DockerCommandBuilder(config)

        with pytest.raises(SandboxSecurityError, match="does not exist"):
            builder.build()


class TestDockerPreflight:
    """Test Docker daemon health checks, engine mode verification, and fail-closed timeout logic."""

    @patch("shutil.which", return_value=None)
    def test_docker_binary_missing_fails_closed(self, mock_which: MagicMock) -> None:
        preflight = DockerPreflight(timeout_seconds=2.0)
        with pytest.raises(DockerUnavailableError, match="Docker binary not found"):
            preflight.check()

    @patch("shutil.which", return_value="/usr/bin/docker")
    @patch("subprocess.run")
    def test_docker_daemon_unreachable_fails_closed(
        self, mock_run: MagicMock, mock_which: MagicMock
    ) -> None:
        mock_run.return_value = subprocess.CompletedProcess(
            args=["docker", "info"],
            returncode=1,
            stdout="",
            stderr="Cannot connect to the Docker daemon",
        )
        preflight = DockerPreflight(timeout_seconds=2.0)
        with pytest.raises(DockerUnavailableError, match="Docker daemon is not responsive"):
            preflight.check()

    @patch("shutil.which", return_value="/usr/bin/docker")
    @patch(
        "subprocess.run",
        side_effect=subprocess.TimeoutExpired(cmd=["docker", "info"], timeout=2.0),
    )
    def test_docker_daemon_timeout_fails_closed(
        self, mock_run: MagicMock, mock_which: MagicMock
    ) -> None:
        preflight = DockerPreflight(timeout_seconds=2.0)
        with pytest.raises(DockerUnavailableError, match="probe timed out"):
            preflight.check()

    @patch("shutil.which", return_value="/usr/bin/docker")
    @patch("subprocess.run")
    def test_docker_windows_containers_mode_fails_closed(
        self, mock_run: MagicMock, mock_which: MagicMock
    ) -> None:
        mock_run.return_value = subprocess.CompletedProcess(
            args=["docker", "info"],
            returncode=0,
            stdout="windows\n",
            stderr="",
        )
        preflight = DockerPreflight(timeout_seconds=2.0)
        with pytest.raises(UnsupportedDockerEngineError, match="Linux Containers mode"):
            preflight.check()

    @patch("shutil.which", return_value="/usr/bin/docker")
    @patch("subprocess.run")
    def test_docker_linux_containers_mode_succeeds(
        self, mock_run: MagicMock, mock_which: MagicMock
    ) -> None:
        mock_run.return_value = subprocess.CompletedProcess(
            args=["docker", "info"],
            returncode=0,
            stdout="linux\n",
            stderr="",
        )
        preflight = DockerPreflight(timeout_seconds=2.0)
        result = preflight.check()

        assert isinstance(result, DockerPreflightResult)
        assert result.is_available is True
        assert result.os_type == "linux"

        # Verify subprocess parameters
        mock_run.assert_called_once()
        _, kwargs = mock_run.call_args
        assert kwargs["shell"] is False
        assert kwargs["close_fds"] is True
        assert kwargs["timeout"] == 2.0


class TestParseTestCommand:
    """Test custom test command parsing, normalization, and security rejection."""

    def test_valid_pytest_command(self) -> None:
        from patchproof.sandbox.docker_cli import parse_test_command

        cmd = parse_test_command("pytest tests/test_calc.py -q")
        assert cmd == ("pytest", "tests/test_calc.py", "-q")

    def test_valid_quoted_arguments(self) -> None:
        from patchproof.sandbox.docker_cli import parse_test_command

        cmd = parse_test_command('pytest -k "test_foo or test_bar" -v')
        assert cmd == ("pytest", "-k", "test_foo or test_bar", "-v")

    def test_valid_python_module_command(self) -> None:
        from patchproof.sandbox.docker_cli import parse_test_command

        cmd = parse_test_command("python -m pytest tests/unit/")
        assert cmd == ("python", "-m", "pytest", "tests/unit/")

    def test_empty_command_rejected(self) -> None:
        from patchproof.sandbox.docker_cli import parse_test_command

        with pytest.raises(SandboxSecurityError, match="cannot be empty"):
            parse_test_command("   ")

    @pytest.mark.parametrize("char", [";", "&", "|", "`", "$", ">", "<", "\n"])
    def test_shell_metacharacters_rejected(self, char: str) -> None:
        from patchproof.sandbox.docker_cli import parse_test_command

        malicious = f"pytest {char} echo hack"
        with pytest.raises(SandboxSecurityError, match="prohibited character"):
            parse_test_command(malicious)
