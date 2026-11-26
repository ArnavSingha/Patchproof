"""Unit tests for PatchProof System Doctor diagnostic subsystem."""

from __future__ import annotations

import json
import subprocess
import sys
from unittest.mock import MagicMock, patch

from patchproof.sandbox.doctor import DoctorCheckResult, SystemDoctor


class TestSystemDoctor:
    """Test suite for environment health diagnostics and remediation reports."""

    def test_check_python_version_passes_on_current_python(self) -> None:
        doctor = SystemDoctor()
        res = doctor.check_python_version()
        assert res.name == "Python Version"
        assert res.passed is True
        assert "Python" in res.details

    def test_check_python_version_fails_on_old_python(self) -> None:
        doctor = SystemDoctor()
        with patch.object(sys, "version_info", (3, 10, 5, "final", 0)):
            res = doctor.check_python_version()
            assert res.passed is False
            assert "unsupported" in res.details
            assert res.remediation is not None
            assert "Install Python 3.11" in res.remediation

    @patch("shutil.which")
    @patch("subprocess.run")
    def test_check_git_cli_passes_when_installed(
        self,
        mock_run: MagicMock,
        mock_which: MagicMock,
    ) -> None:
        mock_which.return_value = "/usr/bin/git"
        mock_run.return_value = subprocess.CompletedProcess(
            args=["git", "--version"],
            returncode=0,
            stdout="git version 2.43.0\n",
            stderr="",
        )

        doctor = SystemDoctor()
        res = doctor.check_git_cli()
        assert res.name == "Git CLI"
        assert res.passed is True
        assert "git version 2.43.0" in res.details

    @patch("shutil.which", return_value=None)
    def test_check_git_cli_fails_when_missing(self, mock_which: MagicMock) -> None:
        doctor = SystemDoctor()
        res = doctor.check_git_cli()
        assert res.passed is False
        assert "not found" in res.details
        assert res.remediation is not None

    @patch("shutil.which")
    @patch("subprocess.run")
    def test_check_docker_cli_passes_when_installed(
        self,
        mock_run: MagicMock,
        mock_which: MagicMock,
    ) -> None:
        mock_which.return_value = "/usr/bin/docker"
        mock_run.return_value = subprocess.CompletedProcess(
            args=["docker", "--version"],
            returncode=0,
            stdout="Docker version 25.0.3, build 4debf41\n",
            stderr="",
        )

        doctor = SystemDoctor()
        res = doctor.check_docker_cli()
        assert res.name == "Docker CLI"
        assert res.passed is True
        assert "Docker version" in res.details

    @patch("shutil.which", return_value=None)
    def test_check_docker_cli_fails_when_missing(self, mock_which: MagicMock) -> None:
        doctor = SystemDoctor()
        res = doctor.check_docker_cli()
        assert res.passed is False
        assert "not found" in res.details
        assert res.remediation is not None

    @patch("shutil.which", return_value="/usr/bin/docker")
    @patch("subprocess.run")
    def test_check_docker_daemon_active(
        self,
        mock_run: MagicMock,
        mock_which: MagicMock,
    ) -> None:
        mock_run.return_value = subprocess.CompletedProcess(
            args=["docker", "info"],
            returncode=0,
            stdout="25.0.3\n",
            stderr="",
        )

        doctor = SystemDoctor()
        res, active = doctor.check_docker_daemon()
        assert res.name == "Docker Daemon"
        assert res.passed is True
        assert active is True
        assert "25.0.3" in res.details

    @patch("shutil.which", return_value="/usr/bin/docker")
    @patch("subprocess.run")
    def test_check_docker_daemon_offline(
        self,
        mock_run: MagicMock,
        mock_which: MagicMock,
    ) -> None:
        mock_run.return_value = subprocess.CompletedProcess(
            args=["docker", "info"],
            returncode=1,
            stdout="",
            stderr="Cannot connect to the Docker daemon at unix:///var/run/docker.sock\n",
        )

        doctor = SystemDoctor()
        res, active = doctor.check_docker_daemon()
        assert res.passed is False
        assert active is False
        assert res.remediation is not None
        assert "Start Docker Desktop" in res.remediation

    @patch("shutil.which", return_value="/usr/bin/docker")
    @patch("subprocess.run")
    def test_check_docker_engine_mode_linux(
        self,
        mock_run: MagicMock,
        mock_which: MagicMock,
    ) -> None:
        mock_run.return_value = subprocess.CompletedProcess(
            args=["docker", "info"],
            returncode=0,
            stdout="linux\n",
            stderr="",
        )

        doctor = SystemDoctor()
        res = doctor.check_docker_engine_mode(daemon_active=True)
        assert res.name == "Docker Engine Mode"
        assert res.passed is True
        assert "Linux" in res.details

    @patch("shutil.which", return_value="/usr/bin/docker")
    @patch("subprocess.run")
    def test_check_docker_engine_mode_windows_containers_rejected(
        self,
        mock_run: MagicMock,
        mock_which: MagicMock,
    ) -> None:
        mock_run.return_value = subprocess.CompletedProcess(
            args=["docker", "info"],
            returncode=0,
            stdout="windows\n",
            stderr="",
        )

        doctor = SystemDoctor()
        res = doctor.check_docker_engine_mode(daemon_active=True)
        assert res.passed is False
        assert "windows" in res.details
        assert res.remediation is not None
        assert "Switch Docker Desktop to Linux" in res.remediation

    def test_check_docker_engine_mode_skipped_when_daemon_offline(self) -> None:
        doctor = SystemDoctor()
        res = doctor.check_docker_engine_mode(daemon_active=False)
        assert res.passed is False
        assert "Skipped" in res.details

    @patch("shutil.which", return_value="/usr/bin/docker")
    @patch("subprocess.run")
    def test_check_runner_image_exists_found(
        self,
        mock_run: MagicMock,
        mock_which: MagicMock,
    ) -> None:
        mock_run.return_value = subprocess.CompletedProcess(
            args=["docker", "image", "inspect"],
            returncode=0,
            stdout="sha256:1234567890abcdef1234\n",
            stderr="",
        )

        doctor = SystemDoctor()
        res, exists = doctor.check_runner_image_exists("patchproof-runner:test", daemon_active=True)
        assert res.passed is True
        assert exists is True
        assert "found locally" in res.details

    @patch("shutil.which", return_value="/usr/bin/docker")
    @patch("subprocess.run")
    def test_check_runner_image_missing(
        self,
        mock_run: MagicMock,
        mock_which: MagicMock,
    ) -> None:
        mock_run.return_value = subprocess.CompletedProcess(
            args=["docker", "image", "inspect"],
            returncode=1,
            stdout="",
            stderr="Error: No such image",
        )

        doctor = SystemDoctor()
        res, exists = doctor.check_runner_image_exists("patchproof-runner:test", daemon_active=True)
        assert res.passed is False
        assert exists is False
        assert "not found" in res.details
        assert res.remediation is not None
        assert "setup-runner" in res.remediation

    @patch("shutil.which", return_value="/usr/bin/docker")
    @patch("subprocess.run")
    def test_check_runner_image_compatibility_success(
        self,
        mock_run: MagicMock,
        mock_which: MagicMock,
    ) -> None:
        inspect_proc = subprocess.CompletedProcess(
            args=["docker", "image", "inspect"],
            returncode=0,
            stdout="amd64|true\n",
            stderr="",
        )
        run_proc = subprocess.CompletedProcess(
            args=["docker", "run"],
            returncode=0,
            stdout="pytest 8.1.1\n",
            stderr="",
        )
        mock_run.side_effect = [inspect_proc, run_proc]

        doctor = SystemDoctor()
        res = doctor.check_runner_image_compatibility("patchproof-runner:test", image_exists=True)
        assert res.name == "Runner Image Compatibility"
        assert res.passed is True
        assert "Compatible" in res.details
        assert "pytest 8.1.1" in res.details

        # Ensure container probe invoked with strict sandbox invariants
        probe_call_args = mock_run.call_args_list[1][0][0]
        assert "--network" in probe_call_args
        assert probe_call_args[probe_call_args.index("--network") + 1] == "none"
        assert "--user" in probe_call_args
        assert probe_call_args[probe_call_args.index("--user") + 1] == "10001:10001"
        assert "--read-only" in probe_call_args

    def test_format_report_human_and_json(self) -> None:
        doctor = SystemDoctor()
        results = [
            DoctorCheckResult(name="Test Check 1", passed=True, details="OK"),
            DoctorCheckResult(
                name="Test Check 2",
                passed=False,
                details="Failed",
                remediation="Fix it",
            ),
        ]

        text_report = doctor.format_report(results, json_output=False)
        assert "[PASS] Test Check 1" in text_report
        assert "[FAIL] Test Check 2" in text_report
        assert "Fix it" in text_report

        json_report = doctor.format_report(results, json_output=True)
        data = json.loads(json_report)
        assert data["all_passed"] is False
        assert len(data["checks"]) == 2
        assert data["checks"][0]["name"] == "Test Check 1"
        assert data["checks"][0]["passed"] is True
