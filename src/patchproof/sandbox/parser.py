"""Structured test output parser for Pytest container execution results."""

from __future__ import annotations

import re

from patchproof.models.sandbox import (
    TestCaseResult,
    TestOutcome,
    TestRunReport,
)

VERBOSE_TEST_LINE_RE = re.compile(
    r"^(?P<name>\S+?::.*?)\s+(?P<outcome>PASSED|FAILED|SKIPPED|XFAIL|XPASS|ERROR)"
    r"(?:\s+\(.*?\))?(?:\s+\[\s*\d+%\])?\s*$"
)

SHORT_SUMMARY_LINE_RE = re.compile(
    r"^(?P<outcome>PASSED|FAILED|SKIPPED|XFAIL|XPASS|ERROR)\s+(?P<name>\S+?::.*?)(?:\s+-\s+(?P<msg>.*))?$"
)

COLLECTION_ERROR_SIGNATURES = (
    "error collecting ",
    "error during collection",
    "errors during collection",
    "interrupted: 1 error during collection",
)

OUTCOME_MAP: dict[str, TestOutcome] = {
    "PASSED": TestOutcome.PASS,
    "FAILED": TestOutcome.FAIL,
    "ERROR": TestOutcome.ERROR,
    "SKIPPED": TestOutcome.SKIPPED,
    "XFAIL": TestOutcome.XFAIL,
    "XPASS": TestOutcome.XPASS,
}


class PytestOutputParser:
    """Parses raw container stdout/stderr streams into structured TestRunReport models."""

    def parse(
        self,
        stdout: str,
        stderr: str = "",
        exit_code: int = 0,
        duration_seconds: float = 0.0,
    ) -> TestRunReport:
        """Parse pytest execution streams into an immutable TestRunReport."""
        combined_text = f"{stdout}\n{stderr}"
        collection_failed, collection_error = self._detect_collection_failure(combined_text)

        test_cases_map: dict[str, TestCaseResult] = {}
        failure_messages: dict[str, str] = self._extract_failure_messages(stdout)

        # 1. Parse individual test lines (verbose output)
        for line in stdout.splitlines():
            line_clean = line.strip()
            match = VERBOSE_TEST_LINE_RE.match(line_clean)
            if match:
                name = match.group("name")
                outcome_str = match.group("outcome")
                outcome = OUTCOME_MAP.get(outcome_str, TestOutcome.ERROR)
                msg = failure_messages.get(name)

                test_cases_map[name] = TestCaseResult(
                    name=name,
                    outcome=outcome,
                    duration_seconds=0.0,
                    failure_message=msg,
                )

        # 2. Parse short test summary info if present
        in_short_summary = False
        for line in stdout.splitlines():
            line_clean = line.strip()
            if "short test summary info" in line_clean:
                in_short_summary = True
                continue
            if in_short_summary and line_clean.startswith("==="):
                in_short_summary = False
                continue

            if in_short_summary:
                match = SHORT_SUMMARY_LINE_RE.match(line_clean)
                if match:
                    name = match.group("name")
                    outcome_str = match.group("outcome")
                    outcome = OUTCOME_MAP.get(outcome_str, TestOutcome.ERROR)
                    msg = match.group("msg") or failure_messages.get(name)

                    if name not in test_cases_map:
                        test_cases_map[name] = TestCaseResult(
                            name=name,
                            outcome=outcome,
                            duration_seconds=0.0,
                            failure_message=msg,
                        )
                    elif msg and not test_cases_map[name].failure_message:
                        test_cases_map[name] = TestCaseResult(
                            name=name,
                            outcome=test_cases_map[name].outcome,
                            duration_seconds=test_cases_map[name].duration_seconds,
                            failure_message=msg,
                        )

        test_cases_tuple = tuple(test_cases_map.values())

        return TestRunReport(
            test_cases=test_cases_tuple,
            exit_code=exit_code,
            duration_seconds=round(duration_seconds, 3),
            total_collected=len(test_cases_tuple),
            collection_failed=collection_failed,
            collection_error=collection_error,
        )

    def _detect_collection_failure(self, text: str) -> tuple[bool, str | None]:
        """Detect whether test discovery failed during the run."""
        text_lower = text.lower()
        for sig in COLLECTION_ERROR_SIGNATURES:
            if sig in text_lower:
                # Extract collection error lines
                err_lines: list[str] = []
                capturing = False
                for line in text.splitlines():
                    if "error collecting" in line.lower() or "=== errors ===" in line.lower():
                        capturing = True
                    if capturing:
                        err_lines.append(line)
                        if len(err_lines) >= 30:
                            break
                error_snippet = "\n".join(err_lines) if err_lines else "Collection error detected"
                return True, error_snippet.strip()
        return False, None

    def _extract_failure_messages(self, stdout: str) -> dict[str, str]:
        """Extract failure traceback messages associated with test identifiers."""
        messages: dict[str, str] = {}
        lines = stdout.splitlines()
        current_test: str | None = None
        current_msg_lines: list[str] = []

        in_failures = False
        for line in lines:
            if "=== FAILURES ===" in line or "=== ERRORS ===" in line:
                in_failures = True
                continue
            if (
                in_failures
                and line.startswith("===")
                and "FAILURES" not in line
                and "ERRORS" not in line
            ):
                in_failures = False
                if current_test:
                    messages[current_test] = "\n".join(current_msg_lines).strip()
                break

            if in_failures:
                if line.startswith("___") and line.endswith("___"):
                    if current_test:
                        messages[current_test] = "\n".join(current_msg_lines).strip()
                        current_msg_lines = []
                    current_test = line.strip("_ ")
                elif current_test:
                    current_msg_lines.append(line)

        if current_test and current_msg_lines:
            messages[current_test] = "\n".join(current_msg_lines).strip()

        return messages
