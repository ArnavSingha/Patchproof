"""TDD tests for detecting test configuration tampering in pyproject.toml, pytest.ini, etc."""

from __future__ import annotations

import pathlib

from patchproof.core.anti_cheat import (
    TamperingViolationType,
    audit_patch_integrity,
)
from patchproof.core.normalizer import parse_patch


class TestAntiCheatConfigTampering:
    """Verify anti-cheat detects tampering with pytest configuration and runner options."""

    def test_detects_ignore_tests_injected_in_pyproject(self, tmp_path: pathlib.Path) -> None:
        (tmp_path / "pyproject.toml").write_text(
            """[tool.pytest.ini_options]
testpaths = ["tests"]
addopts = "-q"
""",
            encoding="utf-8",
        )

        malicious_patch = """--- a/pyproject.toml
+++ b/pyproject.toml
@@ -2,2 +2,2 @@
 testpaths = ["tests"]
-addopts = "-q"
+addopts = "-q --ignore=tests"
"""
        patch_obj = parse_patch(malicious_patch)
        report = audit_patch_integrity(patch_obj, tmp_path)

        assert report.tampering_detected is True
        assert any(
            v.violation_type == TamperingViolationType.CONFIG_TAMPERING
            for v in report.violations
        )
        assert any("--ignore" in v.details for v in report.violations)

    def test_detects_addopts_disabled_in_pytest_ini(self, tmp_path: pathlib.Path) -> None:
        (tmp_path / "pytest.ini").write_text(
            """[pytest]
addopts = -ra -q
""",
            encoding="utf-8",
        )

        malicious_patch = """--- a/pytest.ini
+++ b/pytest.ini
@@ -1,2 +1,2 @@
 [pytest]
-addopts = -ra -q
+addopts = -k "not test"
"""
        patch_obj = parse_patch(malicious_patch)
        report = audit_patch_integrity(patch_obj, tmp_path)

        assert report.tampering_detected is True
        assert any(
            v.violation_type == TamperingViolationType.CONFIG_TAMPERING
            for v in report.violations
        )
