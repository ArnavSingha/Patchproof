"""End-to-end integration tests for PatchProof CLI and verification workflows."""

from __future__ import annotations

import json
import pathlib
import subprocess
import tempfile
from unittest.mock import MagicMock, patch

import pytest

from patchproof.cli import ExitCode, main
from patchproof.models.sandbox import ExecutionResult
from patchproof.sandbox.runner import ContainerRunner


def is_docker_available() -> bool:
    """Check if Docker is installed, running, and in Linux container mode."""
    try:
        proc = subprocess.run(
            ["docker", "info", "--format", "{{.OSType}}"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        return proc.returncode == 0 and proc.stdout.strip() == "linux"
    except (subprocess.SubprocessError, FileNotFoundError, OSError):
        return False


@pytest.fixture
def sample_repo(tmp_path: pathlib.Path) -> pathlib.Path:
    """Create a minimal Python repository fixture for verification testing."""
    repo = tmp_path / "sample_repo"
    repo.mkdir()

    calc_file = repo / "calc.py"
    calc_file.write_text(
        "def add(a: int, b: int) -> int:\n"
        "    return a + b\n\n"
        "def divide(a: int, b: int) -> int:\n"
        "    return a // b\n",
        encoding="utf-8",
    )

    test_file = repo / "test_calc.py"
    test_file.write_text(
        "from calc import add, divide\n\n"
        "def test_add() -> None:\n"
        "    assert add(1, 2) == 3\n\n"
        "def test_divide() -> None:\n"
        "    assert divide(4, 2) == 2\n",
        encoding="utf-8",
    )
    return repo


class TestMockedCliWorkflows:
    """Mocked end-to-end workflow verification for PatchProof CLI."""

    @patch.object(ContainerRunner, "run")
    def test_e2e_clean_patch_accepted(
        self,
        mock_runner_run: MagicMock,
        sample_repo: pathlib.Path,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        patch_file = tmp_path / "clean.patch"
        patch_file.write_text(
            "--- a/calc.py\n"
            "+++ b/calc.py\n"
            "@@ -1,2 +1,3 @@\n"
            " def add(a: int, b: int) -> int:\n"
            "+    # Verified optimization\n"
            "     return a + b\n",
            encoding="utf-8",
        )

        mock_runner_run.return_value = ExecutionResult(
            exit_code=0,
            stdout=(
                "test_calc.py::test_add PASSED [ 50%]\n"
                "test_calc.py::test_divide PASSED [100%]\n"
                "==== 2 passed in 0.05s ====\n"
            ),
            duration_seconds=0.05,
        )

        initial_calc = (sample_repo / "calc.py").read_text(encoding="utf-8")
        initial_test = (sample_repo / "test_calc.py").read_text(encoding="utf-8")

        exit_code = main(["verify", str(patch_file), "--repo", str(sample_repo), "--json"])

        assert exit_code == ExitCode.ACCEPTED

        # Ensure repo was not modified on host
        assert (sample_repo / "calc.py").read_text(encoding="utf-8") == initial_calc
        assert (sample_repo / "test_calc.py").read_text(encoding="utf-8") == initial_test

        captured = capsys.readouterr()
        report = json.loads(captured.out)
        assert report["verdict"] == "accepted"
        assert report["metrics"]["introduced_regressions"] == 0

    @patch.object(ContainerRunner, "run")
    def test_e2e_regression_patch_rejected(
        self,
        mock_runner_run: MagicMock,
        sample_repo: pathlib.Path,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        patch_file = tmp_path / "break.patch"
        patch_file.write_text(
            "--- a/calc.py\n"
            "+++ b/calc.py\n"
            "@@ -1,2 +1,2 @@\n"
            " def add(a: int, b: int) -> int:\n"
            "-    return a + b\n"
            "+    return a - b\n",
            encoding="utf-8",
        )

        # Baseline: pass; Patched: test_add fails
        base_exec = ExecutionResult(
            exit_code=0,
            stdout=(
                "test_calc.py::test_add PASSED [ 50%]\n"
                "test_calc.py::test_divide PASSED [100%]\n"
                "==== 2 passed in 0.05s ====\n"
            ),
            duration_seconds=0.05,
        )
        patched_exec = ExecutionResult(
            exit_code=1,
            stdout=(
                "test_calc.py::test_add FAILED [ 50%]\n"
                "test_calc.py::test_divide PASSED [100%]\n"
                "==== 1 failed, 1 passed in 0.06s ====\n"
            ),
            duration_seconds=0.06,
        )
        mock_runner_run.side_effect = [base_exec, patched_exec]

        initial_calc = (sample_repo / "calc.py").read_text(encoding="utf-8")

        exit_code = main(["verify", str(patch_file), "--repo", str(sample_repo), "--json"])

        assert exit_code == ExitCode.REJECTED
        assert (sample_repo / "calc.py").read_text(encoding="utf-8") == initial_calc

        captured = capsys.readouterr()
        report = json.loads(captured.out)
        assert report["verdict"] == "rejected"
        assert report["metrics"]["introduced_regressions"] == 1

    @patch.object(ContainerRunner, "run")
    def test_e2e_bugfix_patch_accepted(
        self,
        mock_runner_run: MagicMock,
        sample_repo: pathlib.Path,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        patch_file = tmp_path / "fix.patch"
        patch_file.write_text(
            "--- a/calc.py\n"
            "+++ b/calc.py\n"
            "@@ -4,2 +4,4 @@\n"
            " def divide(a: int, b: int) -> int:\n"
            "+    if b == 0:\n"
            "+        raise ValueError('Cannot divide by zero')\n"
            "     return a // b\n",
            encoding="utf-8",
        )

        # Baseline: test_divide fails; Patched: all pass
        base_exec = ExecutionResult(
            exit_code=1,
            stdout=(
                "test_calc.py::test_add PASSED [ 50%]\n"
                "test_calc.py::test_divide FAILED [100%]\n"
                "==== 1 failed, 1 passed in 0.05s ====\n"
            ),
            duration_seconds=0.05,
        )
        patched_exec = ExecutionResult(
            exit_code=0,
            stdout=(
                "test_calc.py::test_add PASSED [ 50%]\n"
                "test_calc.py::test_divide PASSED [100%]\n"
                "==== 2 passed in 0.05s ====\n"
            ),
            duration_seconds=0.05,
        )
        mock_runner_run.side_effect = [base_exec, patched_exec]

        exit_code = main(["verify", str(patch_file), "--repo", str(sample_repo), "--json"])

        assert exit_code == ExitCode.ACCEPTED

        captured = capsys.readouterr()
        report = json.loads(captured.out)
        assert report["verdict"] == "accepted"
        assert report["metrics"]["resolved_defects"] == 1
        assert report["metrics"]["introduced_regressions"] == 0

    @patch.object(ContainerRunner, "run")
    def test_e2e_inconclusive_run_when_no_tests(
        self,
        mock_runner_run: MagicMock,
        sample_repo: pathlib.Path,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        patch_file = tmp_path / "no_tests.patch"
        patch_file.write_text(
            "--- a/calc.py\n"
            "+++ b/calc.py\n"
            "@@ -1,2 +1,3 @@\n"
            " def add(a: int, b: int) -> int:\n"
            "+    # no tests run comment\n"
            "     return a + b\n",
            encoding="utf-8",
        )

        mock_runner_run.return_value = ExecutionResult(
            exit_code=0,
            stdout="==== no tests ran in 0.01s ====\n",
            duration_seconds=0.01,
        )

        exit_code = main(["verify", str(patch_file), "--repo", str(sample_repo), "--json"])

        assert exit_code == ExitCode.INCONCLUSIVE

        captured = capsys.readouterr()
        report = json.loads(captured.out)
        assert report["verdict"] == "inconclusive"

    def test_e2e_malformed_patch_rejected(
        self,
        sample_repo: pathlib.Path,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        bad_patch = tmp_path / "garbage.patch"
        bad_patch.write_text("totally invalid patch syntax\nnot unified diff", encoding="utf-8")

        exit_code = main(["verify", str(bad_patch), "--repo", str(sample_repo), "--json"])

        assert exit_code == ExitCode.REJECTED

        captured = capsys.readouterr()
        report = json.loads(captured.out)
        assert report["verdict"] == "rejected"
        assert "invalid patch format" in report["summary"].lower()


class TestLiveDockerE2E:
    """Docker-gated end-to-end integration tests with real container execution."""

    @pytest.mark.skipif(
        not is_docker_available(),
        reason="Docker daemon not available or not Linux mode",
    )
    def test_live_docker_patch_verification_and_cleanup(
        self, sample_repo: pathlib.Path, tmp_path: pathlib.Path
    ) -> None:
        patch_file = tmp_path / "valid.patch"
        patch_file.write_text(
            "--- a/calc.py\n"
            "+++ b/calc.py\n"
            "@@ -1,2 +1,3 @@\n"
            " def add(a: int, b: int) -> int:\n"
            "+    # Verified inline comment\n"
            "     return a + b\n",
            encoding="utf-8",
        )

        # Snapshot initial repository files
        initial_calc = (sample_repo / "calc.py").read_text(encoding="utf-8")
        initial_test = (sample_repo / "test_calc.py").read_text(encoding="utf-8")

        # Snapshot temp dir before run
        temp_dir = pathlib.Path(tempfile.gettempdir())
        staging_before = set(temp_dir.glob("patchproof-staging-*"))

        exit_code = main(
            [
                "verify",
                str(patch_file),
                "--repo",
                str(sample_repo),
                "--image",
                "python:3.11-slim",
                "--timeout",
                "30.0",
                "--json",
            ]
        )

        # Verdict should be accepted or inconclusive if container python has no pytest installed
        assert exit_code in (ExitCode.ACCEPTED, ExitCode.INCONCLUSIVE)

        # Check repository immutability: Host repo must remain byte-for-byte identical
        assert (sample_repo / "calc.py").read_text(encoding="utf-8") == initial_calc
        assert (sample_repo / "test_calc.py").read_text(encoding="utf-8") == initial_test

        # Check ephemeral staging workspace cleanup
        staging_after = set(temp_dir.glob("patchproof-staging-*"))
        new_staging_dirs = staging_after - staging_before
        assert not new_staging_dirs, f"Lingering staging dirs: {new_staging_dirs}"
