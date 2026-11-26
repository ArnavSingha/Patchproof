"""Integration test suite covering the 10 core PatchProof M3 verification scenarios."""

from __future__ import annotations

import io
import pathlib
from unittest.mock import patch

from patchproof.cli import ExitCode, main
from patchproof.models.sandbox import (
    ExecutionResult,
    PatchVerdict,
    SandboxFailureReason,
    TestCaseResult,
    TestOutcome,
    TestRunReport,
)
from patchproof.sandbox.classifier import RegressionClassifier
from patchproof.sandbox.runner import ContainerRunner


class TestM3Scenarios:
    """Test suite executing the 10 M3 mandatory scenarios."""

    def test_scenario_1_clean_patch(self, tmp_path: pathlib.Path) -> None:
        """Scenario 1: Clean patch - preserves passing tests, zero regressions."""
        repo = tmp_path / "repo"
        repo.mkdir()
        (repo / "calc.py").write_text("def add(a: int, b: int) -> int:\n    return a + b\n")
        patch_file = tmp_path / "clean.patch"
        patch_file.write_text(
            "--- a/calc.py\n+++ b/calc.py\n@@ -1,2 +1,3 @@\n"
            " def add(a: int, b: int) -> int:\n+    # verified\n     return a + b\n"
        )

        with patch.object(ContainerRunner, "run") as mock_run:
            mock_run.return_value = ExecutionResult(
                exit_code=0,
                stdout="test_calc.py::test_add PASSED [100%]\n==== 1 passed in 0.01s ====\n",
                duration_seconds=0.01,
            )
            with patch("sys.stdout", new_callable=io.StringIO):
                code = main(["verify", str(patch_file), "--repo", str(repo), "--json"])

        assert code == ExitCode.ACCEPTED

    def test_scenario_2_regression_patch(self, tmp_path: pathlib.Path) -> None:
        """Scenario 2: Regression patch - introduces failing test."""
        repo = tmp_path / "repo"
        repo.mkdir()
        (repo / "calc.py").write_text("def add(a: int, b: int) -> int:\n    return a + b\n")
        patch_file = tmp_path / "break.patch"
        patch_file.write_text(
            "--- a/calc.py\n+++ b/calc.py\n@@ -1,2 +1,2 @@\n"
            " def add(a: int, b: int) -> int:\n-    return a + b\n+    return a - b\n"
        )

        base_res = ExecutionResult(
            exit_code=0,
            stdout="test_calc.py::test_add PASSED [100%]\n==== 1 passed in 0.01s ====\n",
            duration_seconds=0.01,
        )
        patched_res = ExecutionResult(
            exit_code=1,
            stdout="test_calc.py::test_add FAILED [100%]\n==== 1 failed in 0.01s ====\n",
            duration_seconds=0.01,
        )

        with patch.object(ContainerRunner, "run", side_effect=[base_res, patched_res]):
            with patch("sys.stdout", new_callable=io.StringIO):
                code = main(["verify", str(patch_file), "--repo", str(repo), "--json"])

        assert code == ExitCode.REJECTED

    def test_scenario_3_bugfix_patch(self, tmp_path: pathlib.Path) -> None:
        """Scenario 3: Bug-fixing patch - resolves pre-existing failure."""
        repo = tmp_path / "repo"
        repo.mkdir()
        (repo / "calc.py").write_text("def div(a: int, b: int) -> int:\n    return a // b\n")
        patch_file = tmp_path / "fix.patch"
        patch_file.write_text(
            "--- a/calc.py\n+++ b/calc.py\n@@ -1,2 +1,3 @@\n"
            " def div(a: int, b: int) -> int:\n+    if b == 0: raise ValueError()\n"
            "     return a // b\n"
        )

        base_res = ExecutionResult(
            exit_code=1,
            stdout="test_calc.py::test_div FAILED [100%]\n==== 1 failed in 0.01s ====\n",
            duration_seconds=0.01,
        )
        patched_res = ExecutionResult(
            exit_code=0,
            stdout="test_calc.py::test_div PASSED [100%]\n==== 1 passed in 0.01s ====\n",
            duration_seconds=0.01,
        )

        with patch.object(ContainerRunner, "run", side_effect=[base_res, patched_res]):
            with patch("sys.stdout", new_callable=io.StringIO):
                code = main(["verify", str(patch_file), "--repo", str(repo), "--json"])

        assert code == ExitCode.ACCEPTED

    def test_scenario_4_malformed_patch(self, tmp_path: pathlib.Path) -> None:
        """Scenario 4: Malformed patch - rejected safely."""
        repo = tmp_path / "repo"
        repo.mkdir()
        patch_file = tmp_path / "invalid.patch"
        patch_file.write_text("completely invalid non-diff garbage\n")

        with patch("sys.stdout", new_callable=io.StringIO):
            code = main(["verify", str(patch_file), "--repo", str(repo), "--json"])

        assert code == ExitCode.REJECTED

    def test_scenario_5_path_traversal(self, tmp_path: pathlib.Path) -> None:
        """Scenario 5: Path traversal attack - rejected safely."""
        repo = tmp_path / "repo"
        repo.mkdir()
        patch_file = tmp_path / "traversal.patch"
        patch_file.write_text(
            "--- ../../../etc/shadow\n+++ ../../../etc/shadow\n@@ -0,0 +1,1 @@\n+root::0:0:::\n"
        )

        with patch.object(ContainerRunner, "run") as mock_run:
            mock_run.return_value = ExecutionResult(
                exit_code=0,
                stdout="test.py::t PASSED\n==== 1 passed ====\n",
                duration_seconds=0.01,
            )
            with patch("sys.stdout", new_callable=io.StringIO):
                code = main(["verify", str(patch_file), "--repo", str(repo), "--json"])

        assert code == ExitCode.REJECTED

    def test_scenario_6_external_symlink(self, tmp_path: pathlib.Path) -> None:
        """Scenario 6: External symlink attack - rejected safely."""
        repo = tmp_path / "repo"
        repo.mkdir()
        fake_symlink = repo / "evil_link"
        fake_symlink.write_text("fake link target")
        patch_file = tmp_path / "clean.patch"
        patch_file.write_text("--- a/calc.py\n+++ b/calc.py\n@@ -0,0 +1,1 @@\n+x\n")

        real_resolve = pathlib.Path.resolve

        def mock_resolve(self_path: pathlib.Path, strict: bool = False) -> pathlib.Path:
            if self_path.name == "evil_link":
                return pathlib.Path("/outside/host/secret")
            return real_resolve(self_path, strict=strict)

        def mock_is_symlink(self_path: pathlib.Path) -> bool:
            return self_path.name == "evil_link"

        with (
            patch.object(ContainerRunner, "run") as mock_run,
            patch("pathlib.Path.is_symlink", side_effect=mock_is_symlink, autospec=True),
            patch("pathlib.Path.resolve", side_effect=mock_resolve, autospec=True),
            patch("sys.stdout", new_callable=io.StringIO),
        ):
            mock_run.return_value = ExecutionResult(
                exit_code=0, stdout="test.py::t PASSED\n==== 1 passed ====\n", duration_seconds=0.01
            )
            code = main(["verify", str(patch_file), "--repo", str(repo), "--json"])

        assert code == ExitCode.REJECTED

    def test_scenario_7_test_suppression(self, tmp_path: pathlib.Path) -> None:
        """Scenario 7: Test suppression - previously passing test skipped."""
        repo = tmp_path / "repo"
        repo.mkdir()
        (repo / "calc.py").write_text("def add(a: int, b: int) -> int:\n    return a + b\n")
        patch_file = tmp_path / "suppress.patch"
        patch_file.write_text(
            "--- a/calc.py\n+++ b/calc.py\n@@ -1,2 +1,3 @@\n"
            " def add(a: int, b: int) -> int:\n+    # pass\n     return a + b\n"
        )

        base_res = ExecutionResult(
            exit_code=0,
            stdout="test_calc.py::test_add PASSED [100%]\n==== 1 passed in 0.01s ====\n",
            duration_seconds=0.01,
        )
        patched_res = ExecutionResult(
            exit_code=0,
            stdout="test_calc.py::test_add SKIPPED [100%]\n==== 1 skipped in 0.01s ====\n",
            duration_seconds=0.01,
        )

        with patch.object(ContainerRunner, "run", side_effect=[base_res, patched_res]):
            with patch("sys.stdout", new_callable=io.StringIO):
                code = main(["verify", str(patch_file), "--repo", str(repo), "--json"])

        assert code == ExitCode.SUSPICIOUS

    def test_scenario_8_docker_unavailable(self, tmp_path: pathlib.Path) -> None:
        """Scenario 8: Docker unavailable - infrastructure failure."""
        repo = tmp_path / "repo"
        repo.mkdir()
        patch_file = tmp_path / "test.patch"
        patch_file.write_text("--- a/calc.py\n+++ b/calc.py\n@@ -0,0 +1,1 @@\n+x\n")

        with patch("sys.stdout", new_callable=io.StringIO):
            code = main(["verify", str(patch_file), "--repo", str(repo), "--json"])

        assert code == ExitCode.INFRASTRUCTURE_FAILURE

    def test_scenario_9_timeout_and_oom(self) -> None:
        """Scenario 9: Timeout and OOM correctly distinguished from exit codes alone."""
        classifier = RegressionClassifier()
        base = ExecutionResult(
            exit_code=0,
            stdout="test.py::t PASSED\n==== 1 passed ====\n",
            duration_seconds=0.01,
            test_report=TestRunReport(
                test_cases=(
                    TestCaseResult(
                        name="test.py::t", outcome=TestOutcome.PASS, duration_seconds=0.01
                    ),
                ),
                exit_code=0,
                duration_seconds=0.01,
                total_collected=1,
            ),
        )

        # Case A: Timeout
        patch_timeout = ExecutionResult(
            exit_code=137,
            stdout="",
            duration_seconds=15.0,
            timeout_triggered=True,
            failure_reason=SandboxFailureReason.TIMEOUT,
        )
        res_timeout = classifier.classify(base, patch_timeout)
        assert res_timeout.verdict == PatchVerdict.REJECTED
        assert "timeout" in res_timeout.summary.lower()

        # Case B: OOM Killed
        patch_oom = ExecutionResult(
            exit_code=137,
            stdout="",
            duration_seconds=2.0,
            oom_killed=True,
            failure_reason=SandboxFailureReason.OOM_KILLED,
        )
        res_oom = classifier.classify(base, patch_oom)
        assert res_oom.verdict == PatchVerdict.REJECTED
        assert "oom" in res_oom.summary.lower()

    def test_scenario_10_incomplete_results(self) -> None:
        """Scenario 10: Incomplete / empty test results never result in ACCEPTED."""
        classifier = RegressionClassifier()
        base = ExecutionResult(
            exit_code=0,
            stdout="==== no tests ran in 0.01s ====\n",
            duration_seconds=0.01,
            test_report=TestRunReport(
                test_cases=(), exit_code=0, duration_seconds=0.01, total_collected=0
            ),
        )
        patched = ExecutionResult(
            exit_code=0,
            stdout="==== no tests ran in 0.01s ====\n",
            duration_seconds=0.01,
            test_report=TestRunReport(
                test_cases=(), exit_code=0, duration_seconds=0.01, total_collected=0
            ),
        )
        res = classifier.classify(base, patched)
        assert res.verdict != PatchVerdict.ACCEPTED
        assert res.verdict == PatchVerdict.INCONCLUSIVE

        # Collection failure
        patched_coll_fail = ExecutionResult(
            exit_code=2,
            stdout="",
            duration_seconds=0.01,
            test_report=TestRunReport(
                test_cases=(),
                exit_code=2,
                duration_seconds=0.01,
                total_collected=0,
                collection_failed=True,
                collection_error="SyntaxError",
            ),
        )
        res_coll = classifier.classify(base, patched_coll_fail)
        assert res_coll.verdict == PatchVerdict.REJECTED
