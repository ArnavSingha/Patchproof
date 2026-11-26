"""Standalone deterministic verification script for the 10 PatchProof M3 Scenarios.

Usage:
    python scripts/verify_scenarios.py
"""

from __future__ import annotations

import io
import json
import pathlib
import sys
import tempfile
from typing import Any
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


def run_scenario_1_clean_patch() -> dict[str, Any]:
    """Scenario 1: Clean patch - preserves passing tests, zero regressions."""
    with tempfile.TemporaryDirectory() as td:
        repo = pathlib.Path(td) / "repo"
        repo.mkdir()
        (repo / "calc.py").write_text("def add(a: int, b: int) -> int:\n    return a + b\n")
        patch_file = pathlib.Path(td) / "clean.patch"
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
    return {
        "scenario": 1,
        "name": "Clean patch (no regressions)",
        "expected_verdict": "ACCEPTED (0)",
        "actual_code": int(code),
        "status": "PASS",
    }


def run_scenario_2_regression_patch() -> dict[str, Any]:
    """Scenario 2: Regression patch - introduces failing test."""
    with tempfile.TemporaryDirectory() as td:
        repo = pathlib.Path(td) / "repo"
        repo.mkdir()
        (repo / "calc.py").write_text("def add(a: int, b: int) -> int:\n    return a + b\n")
        patch_file = pathlib.Path(td) / "break.patch"
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
    return {
        "scenario": 2,
        "name": "Regression patch (introduces failure)",
        "expected_verdict": "REJECTED (1)",
        "actual_code": int(code),
        "status": "PASS",
    }


def run_scenario_3_bugfix_patch() -> dict[str, Any]:
    """Scenario 3: Bug-fixing patch - resolves pre-existing failure."""
    with tempfile.TemporaryDirectory() as td:
        repo = pathlib.Path(td) / "repo"
        repo.mkdir()
        (repo / "calc.py").write_text("def div(a: int, b: int) -> int:\n    return a // b\n")
        patch_file = pathlib.Path(td) / "fix.patch"
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
    return {
        "scenario": 3,
        "name": "Bugfix patch (resolves defect)",
        "expected_verdict": "ACCEPTED (0)",
        "actual_code": int(code),
        "status": "PASS",
    }


def run_scenario_4_malformed_patch() -> dict[str, Any]:
    """Scenario 4: Malformed patch - rejected safely."""
    with tempfile.TemporaryDirectory() as td:
        repo = pathlib.Path(td) / "repo"
        repo.mkdir()
        patch_file = pathlib.Path(td) / "invalid.patch"
        patch_file.write_text("completely invalid non-diff garbage\n")

        with patch("sys.stdout", new_callable=io.StringIO):
            code = main(["verify", str(patch_file), "--repo", str(repo), "--json"])

    assert code == ExitCode.REJECTED
    return {
        "scenario": 4,
        "name": "Malformed patch syntax",
        "expected_verdict": "REJECTED (1)",
        "actual_code": int(code),
        "status": "PASS",
    }


def run_scenario_5_path_traversal() -> dict[str, Any]:
    """Scenario 5: Path traversal attack - rejected safely."""
    with tempfile.TemporaryDirectory() as td:
        repo = pathlib.Path(td) / "repo"
        repo.mkdir()
        patch_file = pathlib.Path(td) / "traversal.patch"
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
    return {
        "scenario": 5,
        "name": "Path traversal attack",
        "expected_verdict": "REJECTED (1)",
        "actual_code": int(code),
        "status": "PASS",
    }


def run_scenario_6_external_symlink() -> dict[str, Any]:
    """Scenario 6: External symlink attack - rejected safely."""
    with tempfile.TemporaryDirectory() as td:
        repo = pathlib.Path(td) / "repo"
        repo.mkdir()
        fake_symlink = repo / "evil_link"
        fake_symlink.write_text("fake link target")
        patch_file = pathlib.Path(td) / "clean.patch"
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
    return {
        "scenario": 6,
        "name": "External symlink attack",
        "expected_verdict": "REJECTED (1)",
        "actual_code": int(code),
        "status": "PASS",
    }


def run_scenario_7_test_suppression() -> dict[str, Any]:
    """Scenario 7: Test suppression - previously passing test skipped."""
    with tempfile.TemporaryDirectory() as td:
        repo = pathlib.Path(td) / "repo"
        repo.mkdir()
        (repo / "calc.py").write_text("def add(a: int, b: int) -> int:\n    return a + b\n")
        patch_file = pathlib.Path(td) / "suppress.patch"
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
    return {
        "scenario": 7,
        "name": "Test suppression",
        "expected_verdict": "SUSPICIOUS (2)",
        "actual_code": int(code),
        "status": "PASS",
    }


def run_scenario_8_docker_unavailable() -> dict[str, Any]:
    """Scenario 8: Docker unavailable - infrastructure failure."""
    with tempfile.TemporaryDirectory() as td:
        repo = pathlib.Path(td) / "repo"
        repo.mkdir()
        patch_file = pathlib.Path(td) / "test.patch"
        patch_file.write_text("--- a/calc.py\n+++ b/calc.py\n@@ -0,0 +1,1 @@\n+x\n")

        with patch("sys.stdout", new_callable=io.StringIO):
            code = main(["verify", str(patch_file), "--repo", str(repo), "--json"])

    assert code == ExitCode.INFRASTRUCTURE_FAILURE
    return {
        "scenario": 8,
        "name": "Docker unavailable",
        "expected_verdict": "INFRASTRUCTURE_FAILURE (4)",
        "actual_code": int(code),
        "status": "PASS",
    }


def run_scenario_9_timeout_and_oom() -> dict[str, Any]:
    """Scenario 9: Timeout and OOM correctly distinguished from exit codes alone."""
    classifier = RegressionClassifier()
    base = ExecutionResult(
        exit_code=0,
        stdout="test.py::t PASSED\n==== 1 passed ====\n",
        duration_seconds=0.01,
        test_report=TestRunReport(
            test_cases=(
                TestCaseResult(name="test.py::t", outcome=TestOutcome.PASS, duration_seconds=0.01),
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

    return {
        "scenario": 9,
        "name": "Timeout and OOM distinguished",
        "expected_verdict": "REJECTED (explicit reasons)",
        "status": "PASS",
    }


def run_scenario_10_incomplete_results() -> dict[str, Any]:
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

    return {
        "scenario": 10,
        "name": "Incomplete test results never accepted",
        "expected_verdict": "INCONCLUSIVE / REJECTED",
        "status": "PASS",
    }


def main_scenarios() -> None:
    """Run all 10 scenarios and print formatted JSON summary."""
    scenarios = [
        run_scenario_1_clean_patch(),
        run_scenario_2_regression_patch(),
        run_scenario_3_bugfix_patch(),
        run_scenario_4_malformed_patch(),
        run_scenario_5_path_traversal(),
        run_scenario_6_external_symlink(),
        run_scenario_7_test_suppression(),
        run_scenario_8_docker_unavailable(),
        run_scenario_9_timeout_and_oom(),
        run_scenario_10_incomplete_results(),
    ]
    print(json.dumps(scenarios, indent=2))
    all_passed = all(s["status"] == "PASS" for s in scenarios)
    print(f"\nALL 10 SCENARIOS PASSED: {all_passed}")
    sys.exit(0 if all_passed else 1)


if __name__ == "__main__":
    main_scenarios()
