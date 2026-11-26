"""Unit tests for PatchProof command-line interface."""

from __future__ import annotations

import io
import json
import pathlib
from unittest.mock import MagicMock, patch

import pytest

from patchproof.cli import (
    ExitCode,
    build_parser,
    format_json_report,
    format_terminal_report,
    main,
)
from patchproof.exceptions import DockerUnavailableError, SandboxSecurityError
from patchproof.models.sandbox import (
    ComparativeExecutionResult,
    ComparativeTestCase,
    ExecutionResult,
    PatchVerdict,
    RegressionClassification,
    TestOutcome,
    TestRunReport,
)
from patchproof.sandbox.comparative import ComparativeRunner


def make_mock_result(
    verdict: PatchVerdict = PatchVerdict.ACCEPTED,
    summary: str = "Test summary",
    introduced_regressions: int = 0,
    resolved_defects: int = 0,
    suppressed_tests: int = 0,
    pre_existing_failures: int = 0,
    collection_failed: bool = False,
    comparisons: tuple[ComparativeTestCase, ...] = (),
) -> ComparativeExecutionResult:
    """Helper to synthesize ComparativeExecutionResult for CLI testing."""
    dummy_exec = ExecutionResult(
        exit_code=0 if verdict == PatchVerdict.ACCEPTED else 1,
        duration_seconds=0.5,
        test_report=TestRunReport(
            test_cases=(),
            exit_code=0,
            duration_seconds=0.5,
            total_collected=1,
        ),
    )
    return ComparativeExecutionResult(
        baseline_result=dummy_exec,
        patched_result=dummy_exec,
        verdict=verdict,
        summary=summary,
        introduced_regressions_count=introduced_regressions,
        resolved_defects_count=resolved_defects,
        suppressed_tests_count=suppressed_tests,
        pre_existing_failures_count=pre_existing_failures,
        collection_failure_occurred=collection_failed,
        test_comparisons=comparisons,
    )


class TestCliArgParser:
    """Tests for CLI argument parsing and validation."""

    def test_default_verify_args(self) -> None:
        parser = build_parser()
        args = parser.parse_args(["verify", "my.patch"])
        assert args.command == "verify"
        assert args.patch == "my.patch"
        assert args.repo == "."
        assert args.image is None
        assert args.timeout == 15.0
        assert args.json is False
        assert args.verbose is False

    def test_custom_verify_args(self) -> None:
        parser = build_parser()
        args = parser.parse_args(
            [
                "verify",
                "custom.patch",
                "--repo",
                "/custom/repo",
                "--image",
                "custom-img:latest",
                "--timeout",
                "45.5",
                "--json",
                "--verbose",
            ]
        )
        assert args.command == "verify"
        assert args.patch == "custom.patch"
        assert args.repo == "/custom/repo"
        assert args.image == "custom-img:latest"
        assert args.timeout == 45.5
        assert args.json is True
        assert args.verbose is True

    def test_missing_subcommand_exits(self) -> None:
        parser = build_parser()
        with pytest.raises(SystemExit) as exc:
            parser.parse_args([])
        assert exc.value.code == ExitCode.CLI_ERROR

    def test_missing_patch_argument_exits(self) -> None:
        ret = main(["verify"])
        assert ret == ExitCode.CLI_ERROR


class TestCliExecutionFlow:
    """Tests for CLI execution flow, error paths, and exit codes."""

    def test_missing_patch_file_returns_cli_error(self, tmp_path: pathlib.Path) -> None:
        non_existent = tmp_path / "does_not_exist.patch"
        ret = main(["verify", str(non_existent), "--repo", str(tmp_path)])
        assert ret == ExitCode.CLI_ERROR

    def test_invalid_repo_dir_returns_cli_error(self, tmp_path: pathlib.Path) -> None:
        patch_file = tmp_path / "test.patch"
        patch_file.write_text("--- a/f\n+++ b/f\n", encoding="utf-8")
        not_a_dir = tmp_path / "not_a_dir"

        ret = main(["verify", str(patch_file), "--repo", str(not_a_dir)])
        assert ret == ExitCode.CLI_ERROR

    def test_invalid_timeout_returns_cli_error(self, tmp_path: pathlib.Path) -> None:
        patch_file = tmp_path / "test.patch"
        patch_file.write_text("--- a/f\n+++ b/f\n", encoding="utf-8")

        ret = main(["verify", str(patch_file), "--repo", str(tmp_path), "--timeout", "0.0"])
        assert ret == ExitCode.CLI_ERROR

        ret_neg = main(["verify", str(patch_file), "--repo", str(tmp_path), "--timeout", "-1.0"])
        assert ret_neg == ExitCode.CLI_ERROR

        ret_huge = main(["verify", str(patch_file), "--repo", str(tmp_path), "--timeout", "500.0"])
        assert ret_huge == ExitCode.CLI_ERROR

    @patch.object(ComparativeRunner, "run")
    def test_accepted_patch_returns_zero(
        self, mock_runner_run: MagicMock, tmp_path: pathlib.Path
    ) -> None:
        patch_file = tmp_path / "test.patch"
        patch_file.write_text("diff content", encoding="utf-8")
        mock_runner_run.return_value = make_mock_result(
            verdict=PatchVerdict.ACCEPTED, summary="Verified clean"
        )

        ret = main(["verify", str(patch_file), "--repo", str(tmp_path)])
        assert ret == ExitCode.ACCEPTED

    @patch.object(ComparativeRunner, "run")
    def test_rejected_patch_returns_one(
        self, mock_runner_run: MagicMock, tmp_path: pathlib.Path
    ) -> None:
        patch_file = tmp_path / "test.patch"
        patch_file.write_text("diff content", encoding="utf-8")
        mock_runner_run.return_value = make_mock_result(
            verdict=PatchVerdict.REJECTED,
            summary="Regressions introduced",
            introduced_regressions=1,
        )

        ret = main(["verify", str(patch_file), "--repo", str(tmp_path)])
        assert ret == ExitCode.REJECTED

    @patch.object(ComparativeRunner, "run")
    def test_suspicious_patch_returns_two(
        self, mock_runner_run: MagicMock, tmp_path: pathlib.Path
    ) -> None:
        patch_file = tmp_path / "test.patch"
        patch_file.write_text("diff content", encoding="utf-8")
        mock_runner_run.return_value = make_mock_result(
            verdict=PatchVerdict.SUSPICIOUS,
            summary="Tests suppressed",
            suppressed_tests=1,
        )

        ret = main(["verify", str(patch_file), "--repo", str(tmp_path)])
        assert ret == ExitCode.SUSPICIOUS

    @patch.object(ComparativeRunner, "run")
    def test_inconclusive_patch_returns_three(
        self, mock_runner_run: MagicMock, tmp_path: pathlib.Path
    ) -> None:
        patch_file = tmp_path / "test.patch"
        patch_file.write_text("diff content", encoding="utf-8")
        mock_runner_run.return_value = make_mock_result(
            verdict=PatchVerdict.INCONCLUSIVE,
            summary="No tests discovered",
        )

        ret = main(["verify", str(patch_file), "--repo", str(tmp_path)])
        assert ret == ExitCode.INCONCLUSIVE

    @patch.object(ComparativeRunner, "run")
    def test_infrastructure_failure_returns_four(
        self, mock_runner_run: MagicMock, tmp_path: pathlib.Path
    ) -> None:
        patch_file = tmp_path / "test.patch"
        patch_file.write_text("diff content", encoding="utf-8")
        mock_runner_run.return_value = make_mock_result(
            verdict=PatchVerdict.INFRASTRUCTURE_FAILURE,
            summary="Docker daemon unreachable",
        )

        ret = main(["verify", str(patch_file), "--repo", str(tmp_path)])
        assert ret == ExitCode.INFRASTRUCTURE_FAILURE

    @patch.object(ComparativeRunner, "run", side_effect=SandboxSecurityError("Path traversal"))
    def test_security_error_during_run_returns_rejected(
        self, mock_runner_run: MagicMock, tmp_path: pathlib.Path
    ) -> None:
        patch_file = tmp_path / "test.patch"
        patch_file.write_text("diff content", encoding="utf-8")

        ret = main(["verify", str(patch_file), "--repo", str(tmp_path)])
        assert ret == ExitCode.REJECTED

    @patch.object(
        ComparativeRunner, "run", side_effect=DockerUnavailableError("Docker not running")
    )
    def test_docker_unavailable_during_run_returns_infra_failure(
        self, mock_runner_run: MagicMock, tmp_path: pathlib.Path
    ) -> None:
        patch_file = tmp_path / "test.patch"
        patch_file.write_text("diff content", encoding="utf-8")

        ret = main(["verify", str(patch_file), "--repo", str(tmp_path)])
        assert ret == ExitCode.INFRASTRUCTURE_FAILURE

    @patch.object(ComparativeRunner, "run")
    def test_read_patch_from_stdin(
        self, mock_runner_run: MagicMock, tmp_path: pathlib.Path
    ) -> None:
        mock_runner_run.return_value = make_mock_result(
            verdict=PatchVerdict.ACCEPTED, summary="Verified from stdin"
        )
        fake_stdin = io.StringIO("stdin diff content")
        with patch("sys.stdin", fake_stdin):
            ret = main(["verify", "-", "--repo", str(tmp_path)])

        assert ret == ExitCode.ACCEPTED
        mock_runner_run.assert_called_once()
        assert mock_runner_run.call_args[0][1] == "stdin diff content"


class TestCliReporting:
    """Tests for terminal and JSON report generation."""

    def test_json_reporting_schema(self, tmp_path: pathlib.Path) -> None:
        tc = ComparativeTestCase(
            test_name="tests/test_foo.py::test_bar",
            baseline_outcome=TestOutcome.FAIL,
            patched_outcome=TestOutcome.PASS,
            classification=RegressionClassification.RESOLVED_DEFECT,
            message="Defect resolved",
        )
        result = make_mock_result(
            verdict=PatchVerdict.ACCEPTED,
            summary="All clear",
            resolved_defects=1,
            comparisons=(tc,),
        )

        json_str = format_json_report(result)
        parsed = json.loads(json_str)

        assert parsed["verdict"] == "accepted"
        assert parsed["summary"] == "All clear"
        assert parsed["metrics"]["resolved_defects"] == 1
        assert parsed["metrics"]["introduced_regressions"] == 0
        assert len(parsed["tests"]) == 1
        assert parsed["tests"][0]["name"] == "tests/test_foo.py::test_bar"
        assert parsed["tests"][0]["baseline_outcome"] == "fail"
        assert parsed["tests"][0]["patched_outcome"] == "pass"
        assert parsed["tests"][0]["classification"] == "resolved_defect"

    def test_terminal_reporting_normal_and_verbose(self) -> None:
        tc_clean = ComparativeTestCase(
            test_name="tests/test_foo.py::test_clean",
            baseline_outcome=TestOutcome.PASS,
            patched_outcome=TestOutcome.PASS,
            classification=RegressionClassification.CLEAN_PRESERVATION,
        )
        tc_fixed = ComparativeTestCase(
            test_name="tests/test_foo.py::test_fixed",
            baseline_outcome=TestOutcome.FAIL,
            patched_outcome=TestOutcome.PASS,
            classification=RegressionClassification.RESOLVED_DEFECT,
        )
        result = make_mock_result(
            verdict=PatchVerdict.ACCEPTED,
            summary="Clean",
            resolved_defects=1,
            comparisons=(tc_clean, tc_fixed),
        )

        normal_out = format_terminal_report(result, verbose=False)
        assert "PATCHPROOF VERIFICATION REPORT" in normal_out
        assert "Verdict:    ACCEPTED" in normal_out
        # Non-verbose should show RESOLVED_DEFECT but not CLEAN_PRESERVATION
        assert "test_fixed" in normal_out
        assert "test_clean" not in normal_out

        verbose_out = format_terminal_report(result, verbose=True)
        assert "test_fixed" in verbose_out
        assert "test_clean" in verbose_out

    @patch.object(ComparativeRunner, "run")
    def test_main_outputs_json(
        self,
        mock_runner_run: MagicMock,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        patch_file = tmp_path / "test.patch"
        patch_file.write_text("diff content", encoding="utf-8")
        mock_runner_run.return_value = make_mock_result(
            verdict=PatchVerdict.ACCEPTED, summary="JSON output verified"
        )

        ret = main(["verify", str(patch_file), "--repo", str(tmp_path), "--json"])
        assert ret == ExitCode.ACCEPTED

        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert data["verdict"] == "accepted"
        assert data["summary"] == "JSON output verified"

    def test_setup_runner_arg_parsing(self) -> None:
        parser = build_parser()
        args = parser.parse_args(["setup-runner", "--tag", "my-tag:1.0", "--no-cache", "--quiet"])
        assert args.command == "setup-runner"
        assert args.tag == "my-tag:1.0"
        assert args.no_cache is True
        assert args.quiet is True

    def test_doctor_arg_parsing(self) -> None:
        parser = build_parser()
        args = parser.parse_args(
            ["doctor", "--image", "custom-runner:3.11", "--json", "--timeout", "10.0"]
        )
        assert args.command == "doctor"
        assert args.image == "custom-runner:3.11"
        assert args.json is True
        assert args.timeout == 10.0

    @patch("patchproof.cli.build_runner_image", return_value=0)
    def test_main_setup_runner_success(self, mock_build: MagicMock) -> None:
        ret = main(["setup-runner", "--tag", "test:3.11"])
        assert ret == ExitCode.ACCEPTED
        mock_build.assert_called_once_with(
            image_tag="test:3.11",
            dockerfile_path=None,
            no_cache=False,
            quiet=False,
        )

    @patch("patchproof.cli.build_runner_image", return_value=1)
    def test_main_setup_runner_failure(self, mock_build: MagicMock) -> None:
        ret = main(["setup-runner"])
        assert ret == ExitCode.CLI_ERROR

    @patch("patchproof.cli.SystemDoctor")
    def test_main_doctor_success(
        self,
        mock_doctor_cls: MagicMock,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        mock_instance = MagicMock()
        mock_check = MagicMock()
        mock_check.passed = True
        mock_instance.run_all_checks.return_value = [mock_check]
        mock_instance.format_report.return_value = "All checks passed"
        mock_doctor_cls.return_value = mock_instance

        ret = main(["doctor"])
        assert ret == ExitCode.ACCEPTED
        captured = capsys.readouterr()
        assert "All checks passed" in captured.out

    @patch("patchproof.cli.SystemDoctor")
    def test_main_doctor_failure(
        self,
        mock_doctor_cls: MagicMock,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        mock_instance = MagicMock()
        mock_check = MagicMock()
        mock_check.passed = False
        mock_instance.run_all_checks.return_value = [mock_check]
        mock_instance.format_report.return_value = "1 check failed"
        mock_doctor_cls.return_value = mock_instance

        ret = main(["doctor"])
        assert ret == ExitCode.REJECTED
        captured = capsys.readouterr()
        assert "1 check failed" in captured.out

    @patch("patchproof.cli.ComparativeRunner")
    def test_main_verify_custom_test_command_passed_to_config(
        self,
        mock_runner_cls: MagicMock,
        tmp_path: pathlib.Path,
    ) -> None:
        patch_file = tmp_path / "valid.patch"
        patch_file.write_text("--- a/foo.py\n+++ b/foo.py\n@@ -1 +1 @@\n-a\n+b\n")

        mock_runner = MagicMock()
        mock_runner.run.return_value = make_mock_result(verdict=PatchVerdict.ACCEPTED)
        mock_runner_cls.return_value = mock_runner

        ret = main(["verify", str(patch_file), "--test-command", "pytest tests/test_calc.py -q"])
        assert ret == ExitCode.ACCEPTED

        mock_runner.run.assert_called_once()
        config_passed = mock_runner.run.call_args[0][0]
        assert config_passed.entrypoint_command == ("pytest", "tests/test_calc.py", "-q")

    @patch("patchproof.cli.ComparativeRunner")
    def test_main_verify_custom_test_command_short_flag(
        self,
        mock_runner_cls: MagicMock,
        tmp_path: pathlib.Path,
    ) -> None:
        patch_file = tmp_path / "valid.patch"
        patch_file.write_text("--- a/foo.py\n+++ b/foo.py\n@@ -1 +1 @@\n-a\n+b\n")

        mock_runner = MagicMock()
        mock_runner.run.return_value = make_mock_result(verdict=PatchVerdict.ACCEPTED)
        mock_runner_cls.return_value = mock_runner

        ret = main(["verify", str(patch_file), "-c", "pytest -k test_foo"])
        assert ret == ExitCode.ACCEPTED

        mock_runner.run.assert_called_once()
        config_passed = mock_runner.run.call_args[0][0]
        assert config_passed.entrypoint_command == ("pytest", "-k", "test_foo")

    @patch("patchproof.cli.ComparativeRunner")
    def test_main_verify_custom_test_command_invalid_metacharacter(
        self,
        mock_runner_cls: MagicMock,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        patch_file = tmp_path / "valid.patch"
        patch_file.write_text("--- a/foo.py\n+++ b/foo.py\n@@ -1 +1 @@\n-a\n+b\n")

        ret = main(["verify", str(patch_file), "-c", "pytest; rm -rf /"])
        assert ret == ExitCode.CLI_ERROR
        captured = capsys.readouterr()
        assert "invalid test command" in captured.err.lower()

    @patch("patchproof.cli.get_uncommitted_diff")
    @patch("patchproof.cli.ComparativeRunner")
    def test_main_verify_with_git_flag(
        self,
        mock_runner_cls: MagicMock,
        mock_get_diff: MagicMock,
        tmp_path: pathlib.Path,
    ) -> None:
        mock_get_diff.return_value = "--- a/x.py\n+++ b/x.py\n@@ -1 +1 @@\n-1\n+2\n"
        mock_runner = MagicMock()
        mock_runner.run.return_value = make_mock_result(verdict=PatchVerdict.ACCEPTED)
        mock_runner_cls.return_value = mock_runner

        ret = main(["verify", "--git", "--repo", str(tmp_path)])
        assert ret == ExitCode.ACCEPTED
        mock_get_diff.assert_called_once_with(tmp_path)
        mock_runner.run.assert_called_once()

    @patch("patchproof.cli.get_uncommitted_diff")
    def test_main_verify_with_git_clean_working_tree(
        self,
        mock_get_diff: MagicMock,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        from patchproof.exceptions import NoChangesDetectedError

        mock_get_diff.side_effect = NoChangesDetectedError("Working tree clean")
        ret = main(["verify", "--git", "--repo", str(tmp_path)])
        assert ret == ExitCode.ACCEPTED
        captured = capsys.readouterr()
        assert "clean" in captured.out.lower()

    @patch("patchproof.cli.get_head_diff")
    @patch("patchproof.cli.ComparativeRunner")
    def test_main_verify_with_head_flag(
        self,
        mock_runner_cls: MagicMock,
        mock_get_head: MagicMock,
        tmp_path: pathlib.Path,
    ) -> None:
        mock_get_head.return_value = "--- a/y.py\n+++ b/y.py\n@@ -1 +1 @@\n-a\n+b\n"
        mock_runner = MagicMock()
        mock_runner.run.return_value = make_mock_result(verdict=PatchVerdict.ACCEPTED)
        mock_runner_cls.return_value = mock_runner

        ret = main(["verify", "--head", "--repo", str(tmp_path)])
        assert ret == ExitCode.ACCEPTED
        mock_get_head.assert_called_once_with(tmp_path)
        mock_runner.run.assert_called_once()

    def test_main_verify_mutually_exclusive_inputs(
        self,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        patch_file = tmp_path / "valid.patch"
        patch_file.write_text("dummy")

        ret = main(["verify", str(patch_file), "--git", "--repo", str(tmp_path)])
        assert ret == ExitCode.CLI_ERROR
        captured = capsys.readouterr()
        assert "specify only one" in captured.err.lower()

    def test_main_verify_missing_all_inputs(
        self,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        ret = main(["verify", "--repo", str(tmp_path)])
        assert ret == ExitCode.CLI_ERROR
        captured = capsys.readouterr()
        assert "must specify patch file, --git, or --head" in captured.err.lower()

    @patch("patchproof.cli.install_pre_commit_hook")
    def test_main_install_hooks_success(
        self,
        mock_install: MagicMock,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        mock_install.return_value = tmp_path / ".git" / "hooks" / "pre-commit"
        ret = main(["install-hooks", "--repo", str(tmp_path)])
        assert ret == ExitCode.ACCEPTED
        mock_install.assert_called_once_with(tmp_path)
        captured = capsys.readouterr()
        assert "installed successfully" in captured.out.lower()

    @patch("patchproof.cli.MCPServer")
    def test_main_mcp_command_starts_server(
        self,
        mock_mcp_cls: MagicMock,
    ) -> None:
        mock_server = MagicMock()
        mock_mcp_cls.return_value = mock_server

        ret = main(["mcp"])
        assert ret == ExitCode.ACCEPTED
        mock_server.run_stdio.assert_called_once()

    @patch("patchproof.cli.install_pre_commit_hook")
    def test_main_install_hooks_failure(
        self,
        mock_install: MagicMock,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        from patchproof.exceptions import GitRepositoryError

        mock_install.side_effect = GitRepositoryError("Not a git repository")
        ret = main(["install-hooks", "--repo", str(tmp_path)])
        assert ret == ExitCode.CLI_ERROR
        captured = capsys.readouterr()
        assert "not a git repository" in captured.err.lower()

    @patch("patchproof.cli.ComparativeRunner")
    def test_main_verify_html_flag_writes_file(
        self,
        mock_runner_cls: MagicMock,
        tmp_path: pathlib.Path,
    ) -> None:
        patch_file = tmp_path / "valid.patch"
        patch_file.write_text("--- a/foo.py\n+++ b/foo.py\n@@ -1 +1 @@\n-a\n+b\n")
        html_out = tmp_path / "report.html"

        mock_runner = MagicMock()
        mock_runner.run.return_value = make_mock_result(verdict=PatchVerdict.ACCEPTED)
        mock_runner_cls.return_value = mock_runner

        ret = main(["verify", str(patch_file), "--html", str(html_out), "--repo", str(tmp_path)])
        assert ret == ExitCode.ACCEPTED
        assert html_out.is_file()
        content = html_out.read_text(encoding="utf-8")
        assert "<!DOCTYPE html>" in content
        assert "PatchProof Verification Report" in content

    @patch("patchproof.cli.ComparativeRunner")
    def test_main_verify_auto_detects_node_project(
        self,
        mock_runner_cls: MagicMock,
        tmp_path: pathlib.Path,
    ) -> None:
        (tmp_path / "package.json").write_text('{"name": "test-pkg"}\n')
        patch_file = tmp_path / "valid.patch"
        patch_file.write_text("--- a/index.js\n+++ b/index.js\n@@ -1 +1 @@\n-1\n+2\n")

        mock_runner = MagicMock()
        mock_runner.run.return_value = make_mock_result(verdict=PatchVerdict.ACCEPTED)
        mock_runner_cls.return_value = mock_runner

        ret = main(["verify", str(patch_file), "--repo", str(tmp_path)])
        assert ret == ExitCode.ACCEPTED

        mock_runner.run.assert_called_once()
        config_passed = mock_runner.run.call_args[0][0]
        assert config_passed.image == "patchproof-runner-node:20"
        assert config_passed.entrypoint_command == ("npm", "test")

    @patch("patchproof.cli.ComparativeRunner")
    def test_main_verify_ai_feedback_flag(
        self,
        mock_runner_cls: MagicMock,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        patch_file = tmp_path / "valid.patch"
        patch_file.write_text("--- a/foo.py\n+++ b/foo.py\n@@ -1 +1 @@\n-a\n+b\n")

        mock_runner = MagicMock()
        mock_runner.run.return_value = make_mock_result(
            verdict=PatchVerdict.REJECTED,
            introduced_regressions=1,
        )
        mock_runner_cls.return_value = mock_runner

        ret = main(["verify", str(patch_file), "--ai-feedback", "--repo", str(tmp_path)])
        assert ret == ExitCode.REJECTED
        captured = capsys.readouterr()
        assert "PATCHPROOF VERIFICATION FAILED (VERDICT: REJECTED)" in captured.err

    def test_main_init_agent(
        self,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        ret = main(["init-agent", "all", "--repo", str(tmp_path)])
        assert ret == ExitCode.ACCEPTED
        captured = capsys.readouterr()
        assert "successfully initialized AI agent configuration" in captured.out
        assert (tmp_path / ".aider.conf.yml").is_file()
        assert (tmp_path / ".cursor" / "mcp.json").is_file()

    @patch("patchproof.cli.ComparativeRunner")
    def test_main_verify_slice_and_flakes_flags(
        self,
        mock_runner_cls: MagicMock,
        tmp_path: pathlib.Path,
    ) -> None:
        # Create minimal repo structure
        src_dir = tmp_path / "src"
        src_dir.mkdir(parents=True)
        (src_dir / "mod.py").write_text("x = 1\n", encoding="utf-8")
        tests_dir = tmp_path / "tests"
        tests_dir.mkdir(parents=True)
        (tests_dir / "test_mod.py").write_text("from mod import x\n", encoding="utf-8")

        patch_file = tmp_path / "mod.patch"
        patch_file.write_text(
            "--- a/src/mod.py\n+++ b/src/mod.py\n@@ -1,1 +1,1 @@\n-x = 1\n+x = 2\n"
        )

        mock_runner = MagicMock()
        mock_runner.run.return_value = make_mock_result(verdict=PatchVerdict.ACCEPTED)
        mock_runner_cls.return_value = mock_runner

        ret = main(
            [
                "verify",
                str(patch_file),
                "--slice",
                "--disambiguate-flakes",
                "--repo",
                str(tmp_path),
            ]
        )
        assert ret == ExitCode.ACCEPTED
        mock_runner.run.assert_called_once()
        _, kwargs = mock_runner.run.call_args
        assert kwargs.get("disambiguate_flakes") is True
        config_passed = mock_runner.run.call_args[0][0]
        assert any("test_mod.py" in arg for arg in config_passed.entrypoint_command)

    @patch("patchproof.cli.ComparativeRunner")
    def test_main_verify_with_cert_flag(
        self,
        mock_runner_cls: MagicMock,
        tmp_path: pathlib.Path,
    ) -> None:
        patch_file = tmp_path / "valid.patch"
        patch_file.write_text("--- a/foo.py\n+++ b/foo.py\n@@ -1 +1 @@\n-a\n+b\n")
        cert_out = tmp_path / "cert.json"

        mock_runner = MagicMock()
        mock_runner.run.return_value = make_mock_result(verdict=PatchVerdict.ACCEPTED)
        mock_runner_cls.return_value = mock_runner

        ret = main(["verify", str(patch_file), "--cert", str(cert_out), "--repo", str(tmp_path)])
        assert ret == ExitCode.ACCEPTED
        assert cert_out.is_file()

        # Validate with verify-cert command
        ret_cert = main(["verify-cert", str(cert_out)])
        assert ret_cert == ExitCode.ACCEPTED

    def test_main_verify_cert_tampered(
        self,
        tmp_path: pathlib.Path,
    ) -> None:
        cert_file = tmp_path / "tampered.cert.json"
        cert_file.write_text(
            json.dumps(
                {
                    "version": "1.0",
                    "patch_sha256": "fake",
                    "base_commit": None,
                    "verdict": "accepted",
                    "total_tests": 1,
                    "passed_tests": 1,
                    "introduced_regressions": 0,
                    "sandbox_isolated": True,
                    "created_at_iso": "2026-11-26T00:00:00Z",
                    "signature": "invalidsignature",
                }
            )
        )
        ret = main(["verify-cert", str(cert_file)])
        assert ret == ExitCode.REJECTED

    @patch("patchproof.cli.AutonomousHealer")
    def test_main_heal_command(
        self,
        mock_healer_cls: MagicMock,
        tmp_path: pathlib.Path,
    ) -> None:
        patch_file = tmp_path / "test.patch"
        patch_file.write_text("--- a/x.py\n+++ b/x.py\n@@ -1 +1 @@\n-1\n+2\n")

        mock_healer = MagicMock()
        mock_healer.heal.return_value = MagicMock(success=True, summary="Healed successfully")
        mock_healer_cls.return_value = mock_healer

        ret = main(["heal", str(patch_file), "--repo", str(tmp_path)])
        assert ret == ExitCode.ACCEPTED
