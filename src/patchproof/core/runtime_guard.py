"""Dynamic Runtime Assertion Auditor and Pytest Injected Guard subsystem.

Provides deterministic runtime tracing of assertions and test executions
to catch metaprogramming, dynamic test suppression, and assertion stripping.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

logger = logging.getLogger(__name__)

MARKER_PREFIX = "__PATCHPROOF_RUNTIME_PROFILE__="


class RuntimeProfile(BaseModel):
    """Immutable telemetry profile capturing dynamic runtime test execution metrics."""

    model_config = ConfigDict(frozen=True)

    collected_count: int = Field(ge=0, description="Total number of test items discovered")
    executed_count: int = Field(ge=0, description="Total number of test items run to completion")
    executed_assertions: int = Field(
        ge=0, description="Total dynamic assertions executed across test suite"
    )
    assertions_by_test: dict[str, int] = Field(
        default_factory=dict, description="Mapping of test nodeid to executed assertion count"
    )


def generate_runtime_guard_plugin() -> str:
    """Generate self-contained Python source code for the injected pytest runtime guard plugin."""
    return f'''"""Auto-injected PatchProof Runtime Assertion Guard plugin.

Collects empirical assertion counts and execution metrics during test execution.
"""
from __future__ import annotations

import json
import linecache
import pathlib
import sys

MARKER_PREFIX = "{MARKER_PREFIX}"

class RuntimeAssertionTracker:
    def __init__(self) -> None:
        self.assertions: int = 0
        self.by_test: dict[str, int] = {{}}
        self.current_test: str | None = None
        self.current_file: str | None = None

    def trace(self, frame, event, arg):
        if event == "line":
            filename = frame.f_code.co_filename
            # Restrict tracing to the active test module to exclude pytest/stdlib internals
            is_target = self.current_file and (
                filename == self.current_file or filename.endswith(self.current_file)
            )
            if is_target:
                line = linecache.getline(filename, frame.f_lineno).strip()
                if line.startswith("assert ") or line.startswith("assert(") or ".assert" in line:
                    self.assertions += 1
                    if self.current_test:
                        self.by_test[self.current_test] = self.by_test.get(self.current_test, 0) + 1
        return self.trace

tracker = RuntimeAssertionTracker()

def pytest_runtest_call(item) -> None:
    tracker.current_test = item.nodeid
    file_path = getattr(item, "path", getattr(item, "fspath", None))
    tracker.current_file = str(file_path) if file_path else None
    sys.settrace(tracker.trace)

def pytest_runtest_teardown(item, nextitem) -> None:
    sys.settrace(None)

def pytest_sessionfinish(session, exitstatus) -> None:
    collected = len(session.items)
    executed = getattr(session, "testscount", collected)
    data = {{
        "collected_count": collected,
        "executed_count": executed,
        "executed_assertions": tracker.assertions,
        "assertions_by_test": tracker.by_test,
    }}
    payload = json.dumps(data)
    # 1. Output marked payload to stdout for StreamCapture
    sys.stdout.write(f"\\n{{MARKER_PREFIX}}{{payload}}\\n")
    sys.stdout.flush()

    # 2. Persist to ephemeral container tmpfs as backup
    try:
        tmp_target = pathlib.Path("/tmp/patchproof_runtime_profile.json")
        tmp_target.write_text(payload, encoding="utf-8")
    except OSError:
        pass
'''


def extract_runtime_profile(stdout: str) -> RuntimeProfile | None:
    """Extract and parse RuntimeProfile from captured container stdout.

    Returns None if marker is not found or payload is invalid JSON.
    """
    if MARKER_PREFIX not in stdout:
        return None

    # Find the last marker occurrence in case of multiple runs
    idx = stdout.rfind(MARKER_PREFIX)
    remainder = stdout[idx + len(MARKER_PREFIX) :]
    first_line = remainder.splitlines()[0].strip()

    try:
        raw_data: Any = json.loads(first_line)
        if not isinstance(raw_data, dict):
            return None
        return RuntimeProfile(
            collected_count=int(raw_data.get("collected_count", 0)),
            executed_count=int(raw_data.get("executed_count", 0)),
            executed_assertions=int(raw_data.get("executed_assertions", 0)),
            assertions_by_test={
                str(k): int(v) for k, v in raw_data.get("assertions_by_test", {}).items()
            },
        )
    except (json.JSONDecodeError, ValueError, TypeError) as err:
        logger.warning("Failed to decode runtime profile payload: %s", err)
        return None


def reconcile_runtime_profiles(
    baseline_profile: RuntimeProfile | None,
    patched_profile: RuntimeProfile | None,
) -> tuple[bool, str | None]:
    """Compare runtime execution profiles between baseline and patched containers.

    Returns:
        tuple[bool, str | None]: (tampering_detected, reason)
    """
    if baseline_profile is None:
        # Baseline didn't produce profile (e.g. non-pytest suite); cannot deduce tampering
        return False, None

    if patched_profile is None:
        # Baseline had guard active, but patched suite dropped or bypassed it
        return (
            True,
            "Dynamic test tampering detected: Patched test suite suppressed the "
            "PatchProof runtime guard",
        )

    # Invariant 1: Total executed assertions must not drop
    if patched_profile.executed_assertions < baseline_profile.executed_assertions:
        drop = baseline_profile.executed_assertions - patched_profile.executed_assertions
        return (
            True,
            f"Dynamic test tampering detected: {drop} fewer assertion(s) executed at runtime "
            f"(baseline: {baseline_profile.executed_assertions}, "
            f"patched: {patched_profile.executed_assertions})",
        )

    # Invariant 2: Per-test assertion counts must not drop for pre-existing tests
    for test_name, b_count in baseline_profile.assertions_by_test.items():
        if test_name in patched_profile.assertions_by_test:
            p_count = patched_profile.assertions_by_test[test_name]
            if p_count < b_count:
                return (
                    True,
                    f"Dynamic test tampering detected in '{test_name}': assertion count dropped "
                    f"from {b_count} to {p_count}",
                )

    # Invariant 3: Total executed test count must not drop
    if patched_profile.executed_count < baseline_profile.executed_count:
        drop = baseline_profile.executed_count - patched_profile.executed_count
        return (
            True,
            f"Dynamic test tampering detected: {drop} fewer test(s) executed at runtime "
            f"(baseline: {baseline_profile.executed_count}, "
            f"patched: {patched_profile.executed_count})",
        )

    return False, None
