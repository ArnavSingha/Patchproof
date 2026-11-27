"""Unit tests for Dynamic Runtime Assertion Auditor and Pytest Injected Guard."""

from __future__ import annotations

import ast
import json
import pathlib
import subprocess
import sys
import tempfile

import pytest
from pydantic import ValidationError

from patchproof.core.runtime_guard import (
    MARKER_PREFIX,
    RuntimeProfile,
    extract_runtime_profile,
    generate_runtime_guard_plugin,
    reconcile_runtime_profiles,
)


def test_generate_runtime_guard_plugin_syntax() -> None:
    """Generated plugin source code must be syntactically valid Python."""
    code = generate_runtime_guard_plugin()
    assert isinstance(code, str)
    assert len(code) > 0
    # Must parse without syntax errors
    parsed = ast.parse(code)
    assert isinstance(parsed, ast.Module)
    assert MARKER_PREFIX in code


def test_runtime_profile_model() -> None:
    """RuntimeProfile model enforces types and bounds."""
    profile = RuntimeProfile(
        collected_count=10,
        executed_count=8,
        executed_assertions=25,
        assertions_by_test={"test_a": 15, "test_b": 10},
    )
    assert profile.collected_count == 10
    assert profile.executed_count == 8
    assert profile.executed_assertions == 25
    assert profile.assertions_by_test["test_a"] == 15

    # Immutable
    with pytest.raises(ValidationError):
        profile.collected_count = 12


def test_extract_runtime_profile_from_stdout() -> None:
    """extract_runtime_profile correctly extracts and parses JSON marker from stdout."""
    stdout_sample = (
        "running pytest...\n"
        "test_one.py .. [100%]\n"
        f"{MARKER_PREFIX}"
        + json.dumps(
            {
                "collected_count": 5,
                "executed_count": 5,
                "executed_assertions": 12,
                "assertions_by_test": {"test_one.py::test_a": 7, "test_one.py::test_b": 5},
            }
        )
        + "\n5 passed in 0.12s\n"
    )
    profile = extract_runtime_profile(stdout_sample)
    assert profile is not None
    assert profile.collected_count == 5
    assert profile.executed_count == 5
    assert profile.executed_assertions == 12
    assert profile.assertions_by_test["test_one.py::test_a"] == 7


def test_extract_runtime_profile_missing_or_corrupt() -> None:
    """extract_runtime_profile returns None when marker is absent or corrupt."""
    assert extract_runtime_profile("clean stdout without marker") is None
    assert extract_runtime_profile(f"{MARKER_PREFIX}{{invalid json}}") is None


def test_reconcile_runtime_profiles_identical() -> None:
    """No tampering when baseline and patched execution profiles match."""
    p1 = RuntimeProfile(
        collected_count=5,
        executed_count=5,
        executed_assertions=10,
        assertions_by_test={"t1": 5, "t2": 5},
    )
    p2 = RuntimeProfile(
        collected_count=5,
        executed_count=5,
        executed_assertions=10,
        assertions_by_test={"t1": 5, "t2": 5},
    )
    tampered, msg = reconcile_runtime_profiles(p1, p2)
    assert tampered is False
    assert msg is None


def test_reconcile_runtime_profiles_assertion_drop() -> None:
    """Detects when total executed assertions drop in patched run."""
    p_base = RuntimeProfile(
        collected_count=5,
        executed_count=5,
        executed_assertions=10,
        assertions_by_test={"t1": 5, "t2": 5},
    )
    p_patch = RuntimeProfile(
        collected_count=5,
        executed_count=5,
        executed_assertions=7,
        assertions_by_test={"t1": 5, "t2": 2},
    )
    tampered, msg = reconcile_runtime_profiles(p_base, p_patch)
    assert tampered is True
    assert msg is not None
    assert "3 fewer assertion(s)" in msg


def test_reconcile_runtime_profiles_per_test_drop() -> None:
    """Detects when assertions drop in an existing test even if total matches."""
    p_base = RuntimeProfile(
        collected_count=5,
        executed_count=5,
        executed_assertions=10,
        assertions_by_test={"t1": 5, "t2": 5},
    )
    p_patch = RuntimeProfile(
        collected_count=5,
        executed_count=5,
        executed_assertions=10,
        assertions_by_test={"t1": 2, "t2": 8},  # t1 dropped from 5 to 2
    )
    tampered, msg = reconcile_runtime_profiles(p_base, p_patch)
    assert tampered is True
    assert msg is not None
    assert "assertion count dropped" in msg
    assert "t1" in msg


def test_reconcile_runtime_profiles_executed_count_drop() -> None:
    """Detects when executed tests drop in patched run (e.g. dynamic skip)."""
    p_base = RuntimeProfile(
        collected_count=5,
        executed_count=5,
        executed_assertions=10,
        assertions_by_test={"t1": 5, "t2": 5},
    )
    p_patch = RuntimeProfile(
        collected_count=5,
        executed_count=3,
        executed_assertions=10,
        assertions_by_test={"t1": 5, "t2": 5},
    )
    tampered, msg = reconcile_runtime_profiles(p_base, p_patch)
    assert tampered is True
    assert msg is not None
    assert "2 fewer test(s) executed" in msg


def test_reconcile_runtime_profiles_missing_in_patched() -> None:
    """Detects when baseline emitted profile but patched suppressed or failed to emit profile."""
    p_base = RuntimeProfile(
        collected_count=5,
        executed_count=5,
        executed_assertions=10,
        assertions_by_test={"t1": 5, "t2": 5},
    )
    tampered, msg = reconcile_runtime_profiles(p_base, None)
    assert tampered is True
    assert msg is not None
    assert "suppressed the PatchProof runtime guard" in msg


def test_runtime_guard_live_execution() -> None:
    """End-to-end integration test with live pytest execution using generated plugin."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = pathlib.Path(tmpdir)
        plugin_code = generate_runtime_guard_plugin()
        (tmp_path / "patchproof_runtime_guard.py").write_text(plugin_code, encoding="utf-8")

        test_file = tmp_path / "test_sample.py"
        test_file.write_text(
            (
                "def test_foo():\n"
                "    val = 42\n"
                "    assert val == 42\n"
                "    assert val > 0\n"
                "\n"
                "def test_bar():\n"
                "    assert True\n"
            ),
            encoding="utf-8",
        )

        res = subprocess.run(
            [
                sys.executable,
                "-m",
                "pytest",
                "-q",
                "-p",
                "patchproof_runtime_guard",
                str(test_file),
            ],
            capture_output=True,
            text=True,
            cwd=str(tmp_path),
            check=False,
        )

        assert res.returncode == 0
        profile = extract_runtime_profile(res.stdout)
        assert profile is not None
        assert profile.collected_count == 2
        assert profile.executed_count == 2
        assert profile.executed_assertions == 3
        # Ensure test_foo has 2 and test_bar has 1
        foo_key = next(k for k in profile.assertions_by_test if "test_foo" in k)
        bar_key = next(k for k in profile.assertions_by_test if "test_bar" in k)
        assert profile.assertions_by_test[foo_key] == 2
        assert profile.assertions_by_test[bar_key] == 1
