"""Unit tests for Pytest output parser and structured test report generation."""

from __future__ import annotations

from patchproof.models.sandbox import (
    TestOutcome,
    TestRunReport,
)
from patchproof.sandbox.parser import PytestOutputParser


class TestPytestOutputParser:
    """Test structured parsing of pytest stdout, stderr, and collection results."""

    def test_parse_verbose_passing_tests(self) -> None:
        stdout = (
            "============================= test session starts =============================\n"
            "tests/unit/test_math.py::test_add PASSED [ 50%]\n"
            "tests/unit/test_math.py::test_sub PASSED [100%]\n"
            "============================== 2 passed in 0.12s ==============================\n"
        )
        parser = PytestOutputParser()
        report: TestRunReport = parser.parse(stdout=stdout, exit_code=0, duration_seconds=0.12)

        assert report.exit_code == 0
        assert report.collection_failed is False
        assert report.total_collected == 2
        assert report.passed_count == 2
        assert report.failed_count == 0
        assert report.skipped_count == 0
        assert "tests/unit/test_math.py::test_add" in report.test_map
        assert report.test_map["tests/unit/test_math.py::test_add"].outcome == TestOutcome.PASS

    def test_parse_class_based_and_parameterized_tests(self) -> None:
        stdout = (
            "============================= test session starts =============================\n"
            "tests/unit/test_service.py::TestUserService::test_create_user PASSED [ 25%]\n"
            "tests/unit/test_service.py::TestUserService::test_find[1, 2] PASSED [ 50%]\n"
            "tests/unit/test_service.py::test_param['admin', 'guest'] FAILED [ 75%]\n"
            "tests/unit/test_service.py::test_tuple[(1, 'x')] SKIPPED [100%]\n"
            "=========================== short test summary info ===========================\n"
            "FAILED tests/unit/test_service.py::test_param['admin', 'guest'] - ValueError: err\n"
            "=================== 1 failed, 2 passed, 1 skipped in 0.30s ===================\n"
        )
        parser = PytestOutputParser()
        report = parser.parse(stdout=stdout, exit_code=1, duration_seconds=0.30)

        assert report.total_collected == 4
        assert report.passed_count == 2
        assert report.failed_count == 1
        assert report.skipped_count == 1

        assert "tests/unit/test_service.py::TestUserService::test_create_user" in report.test_map
        assert (
            report.test_map["tests/unit/test_service.py::TestUserService::test_create_user"].outcome
            == TestOutcome.PASS
        )

        assert "tests/unit/test_service.py::TestUserService::test_find[1, 2]" in report.test_map
        assert (
            report.test_map["tests/unit/test_service.py::TestUserService::test_find[1, 2]"].outcome
            == TestOutcome.PASS
        )

        failed_param = report.test_map["tests/unit/test_service.py::test_param['admin', 'guest']"]
        assert failed_param.outcome == TestOutcome.FAIL
        assert failed_param.failure_message is not None
        assert "ValueError" in failed_param.failure_message

        assert "tests/unit/test_service.py::test_tuple[(1, 'x')]" in report.test_map
        assert (
            report.test_map["tests/unit/test_service.py::test_tuple[(1, 'x')]"].outcome
            == TestOutcome.SKIPPED
        )

    def test_parse_failures_and_errors_with_traceback(self) -> None:
        stdout = (
            "============================= test session starts =============================\n"
            "tests/test_ops.py::test_divide_by_zero FAILED [ 50%]\n"
            "tests/test_ops.py::test_sqrt PASSED [100%]\n"
            "=================================== FAILURES ===================================\n"
            "_____________________________ test_divide_by_zero _____________________________\n"
            "    def test_divide_by_zero():\n"
            ">       assert 1 / 0 == 0\n"
            "E       ZeroDivisionError: division by zero\n"
            "tests/test_ops.py:10: ZeroDivisionError\n"
            "=========================== short test summary info ===========================\n"
            "FAILED tests/test_ops.py::test_divide_by_zero - ZeroDivisionError: division by zero\n"
            "========================= 1 failed, 1 passed in 0.25s =========================\n"
        )
        parser = PytestOutputParser()
        report = parser.parse(stdout=stdout, exit_code=1, duration_seconds=0.25)

        assert report.exit_code == 1
        assert report.total_collected == 2
        assert report.passed_count == 1
        assert report.failed_count == 1
        failed_test = report.test_map["tests/test_ops.py::test_divide_by_zero"]
        assert failed_test.outcome == TestOutcome.FAIL
        assert failed_test.failure_message is not None
        assert "ZeroDivisionError" in failed_test.failure_message

    def test_parse_skipped_and_xfail_tests(self) -> None:
        stdout = (
            "tests/test_features.py::test_linux_only SKIPPED (unsupported on win32) [ 33%]\n"
            "tests/test_features.py::test_legacy XFAIL [ 66%]\n"
            "tests/test_features.py::test_core PASSED [100%]\n"
            "=================== 1 passed, 1 skipped, 1 xfailed in 0.08s ===================\n"
        )
        parser = PytestOutputParser()
        report = parser.parse(stdout=stdout, exit_code=0, duration_seconds=0.08)

        assert report.total_collected == 3
        assert report.passed_count == 1
        assert report.skipped_count == 1
        linux_test = report.test_map["tests/test_features.py::test_linux_only"]
        assert linux_test.outcome == TestOutcome.SKIPPED
        assert report.test_map["tests/test_features.py::test_legacy"].outcome == TestOutcome.XFAIL

    def test_detect_collection_failure(self) -> None:
        stdout = (
            "============================= test session starts =============================\n"
            "=================================== ERRORS ====================================\n"
            "___________________ ERROR collecting tests/test_syntax.py ____________________\n"
            "tests/test_syntax.py:5: in <module>\n"
            "    def invalid syntax:\n"
            "E     SyntaxError: invalid syntax\n"
            "=========================== short test summary info ===========================\n"
            "ERROR tests/test_syntax.py\n"
            "!!!!!!!!!!!!!!!!!!! Interrupted: 1 error during collection !!!!!!!!!!!!!!!!!!!!\n"
            "============================== 1 error in 0.15s ===============================\n"
        )
        parser = PytestOutputParser()
        report = parser.parse(stdout=stdout, exit_code=2, duration_seconds=0.15)

        assert report.collection_failed is True
        assert report.collection_error is not None
        assert "SyntaxError" in report.collection_error
        assert report.failed_count == 0  # Collection failure, not test failures

    def test_empty_or_malformed_output_handled_gracefully(self) -> None:
        parser = PytestOutputParser()
        report = parser.parse(
            stdout="",
            stderr="Crash without output",
            exit_code=137,
            duration_seconds=5.0,
        )

        assert report.exit_code == 137
        assert report.total_collected == 0
        assert report.collection_failed is False
        assert len(report.test_cases) == 0
