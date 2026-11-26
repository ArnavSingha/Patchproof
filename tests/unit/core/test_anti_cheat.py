"""Unit tests for the Anti-Cheat Test Tampering Guard."""

from __future__ import annotations

import pathlib

from patchproof.core.anti_cheat import (
    AntiCheatAuditReport,
    TamperingViolationType,
    audit_patch_integrity,
    is_test_file,
)
from patchproof.core.normalizer import parse_patch


class TestAntiCheatGuard:
    """Test verification and auditing of patch integrity against test tampering."""

    def test_is_test_file_identification(self) -> None:
        """Verify robust identification of test files across naming conventions."""
        assert is_test_file("tests/test_api.py") is True
        assert is_test_file("test/unit/test_core.py") is True
        assert is_test_file("src/mypackage/test_utils.py") is True
        assert is_test_file("tests/calc_test.py") is True
        assert is_test_file("tests/conftest.py") is True
        assert is_test_file("conftest.py") is True

        # Non-test files
        assert is_test_file("src/mypackage/api.py") is False
        assert is_test_file("src/testing_framework.py") is False
        assert is_test_file("README.md") is False

    def test_clean_patch_modifying_only_source_code(self, tmp_path: pathlib.Path) -> None:
        """A patch modifying only source files should produce 0 violations."""
        src_file = tmp_path / "src" / "calc.py"
        src_file.parent.mkdir(parents=True)
        src_file.write_text("def add(a, b):\n    return a - b\n", encoding="utf-8")

        diff = (
            "--- a/src/calc.py\n"
            "+++ b/src/calc.py\n"
            "@@ -1,2 +1,2 @@\n"
            " def add(a, b):\n"
            "-    return a - b\n"
            "+    return a + b\n"
        )
        patch = parse_patch(diff)
        report: AntiCheatAuditReport = audit_patch_integrity(patch, repo_root=tmp_path)

        assert report.tampering_detected is False
        assert len(report.violations) == 0
        assert "clean" in report.summary.lower()

    def test_detect_test_file_deletion(self, tmp_path: pathlib.Path) -> None:
        """Deleting a test file must be flagged as a critical tampering violation."""
        test_file = tmp_path / "tests" / "test_auth.py"
        test_file.parent.mkdir(parents=True)
        test_file.write_text("def test_login(): assert False\n", encoding="utf-8")

        diff = (
            "--- a/tests/test_auth.py\n"
            "+++ /dev/null\n"
            "@@ -1,1 +0,0 @@\n"
            "-def test_login(): assert False\n"
        )
        patch = parse_patch(diff)
        report = audit_patch_integrity(patch, repo_root=tmp_path)

        assert report.tampering_detected is True
        assert len(report.violations) == 1
        v = report.violations[0]
        assert v.violation_type == TamperingViolationType.TEST_FILE_DELETED
        assert v.file_path == "tests/test_auth.py"

    def test_detect_test_function_deletion(self, tmp_path: pathlib.Path) -> None:
        """Deleting a test function in an existing test file must trigger tampering violation."""
        test_file = tmp_path / "tests" / "test_math.py"
        test_file.parent.mkdir(parents=True)
        orig_content = (
            "def test_add():\n"
            "    assert 1 + 1 == 2\n"
            "\n"
            "def test_failing_case():\n"
            "    assert 10 / 0 == 0\n"
        )
        test_file.write_text(orig_content, encoding="utf-8")

        diff = (
            "--- a/tests/test_math.py\n"
            "+++ b/tests/test_math.py\n"
            "@@ -3,3 +3,0 @@\n"
            "-\n"
            "-def test_failing_case():\n"
            "-    assert 10 / 0 == 0\n"
        )
        patch = parse_patch(diff)
        report = audit_patch_integrity(patch, repo_root=tmp_path)

        assert report.tampering_detected is True
        deleted_funcs = [
            v for v in report.violations
            if v.violation_type == TamperingViolationType.TEST_FUNCTION_DELETED
        ]
        assert len(deleted_funcs) == 1
        assert deleted_funcs[0].target_name == "test_failing_case"

    def test_detect_assertion_count_reduced(self, tmp_path: pathlib.Path) -> None:
        """Removing assertions from a test must be flagged."""
        test_file = tmp_path / "tests" / "test_user.py"
        test_file.parent.mkdir(parents=True)
        orig_content = (
            "def test_user_creation():\n"
            "    u = {'id': 1, 'active': True}\n"
            "    assert u is not None\n"
            "    assert u['id'] == 1\n"
            "    assert u['active'] is True\n"
        )
        test_file.write_text(orig_content, encoding="utf-8")

        # Patch removes two asserts and replaces with pass
        diff = (
            "--- a/tests/test_user.py\n"
            "+++ b/tests/test_user.py\n"
            "@@ -3,3 +3,1 @@\n"
            "     assert u is not None\n"
            "-    assert u['id'] == 1\n"
            "-    assert u['active'] is True\n"
        )
        patch = parse_patch(diff)
        report = audit_patch_integrity(patch, repo_root=tmp_path)

        assert report.tampering_detected is True
        assert report.assertion_delta < 0
        violations = [
            v for v in report.violations
            if v.violation_type == TamperingViolationType.ASSERTION_COUNT_REDUCED
        ]
        assert len(violations) == 1
        assert "assertion count decreased" in violations[0].details.lower()

    def test_detect_suppression_injection(self, tmp_path: pathlib.Path) -> None:
        """Injecting @pytest.mark.skip or pytest.skip() must be detected."""
        test_file = tmp_path / "tests" / "test_edge.py"
        test_file.parent.mkdir(parents=True)
        orig_content = (
            "import pytest\n"
            "\n"
            "def test_difficult_case():\n"
            "    assert False\n"
        )
        test_file.write_text(orig_content, encoding="utf-8")

        diff = (
            "--- a/tests/test_edge.py\n"
            "+++ b/tests/test_edge.py\n"
            "@@ -1,4 +1,5 @@\n"
            " import pytest\n"
            " \n"
            "+@pytest.mark.skip(reason='cheating')\n"
            " def test_difficult_case():\n"
            "     assert False\n"
        )
        patch = parse_patch(diff)
        report = audit_patch_integrity(patch, repo_root=tmp_path)

        assert report.tampering_detected is True
        suppression_violations = [
            v for v in report.violations
            if v.violation_type == TamperingViolationType.TEST_SUPPRESSION_INJECTED
        ]
        assert len(suppression_violations) >= 1

    def test_allow_test_edits_flag(self, tmp_path: pathlib.Path) -> None:
        """When allow_test_edits=True, adding assertions and tests should be accepted."""
        test_file = tmp_path / "tests" / "test_new.py"
        test_file.parent.mkdir(parents=True)
        test_file.write_text("def test_one(): assert 1 == 1\n", encoding="utf-8")

        diff = (
            "--- a/tests/test_new.py\n"
            "+++ b/tests/test_new.py\n"
            "@@ -1,1 +1,2 @@\n"
            " def test_one(): assert 1 == 1\n"
            "+def test_two(): assert 2 == 2\n"
        )
        patch = parse_patch(diff)
        # With allow_test_edits=True, no violation
        report = audit_patch_integrity(patch, repo_root=tmp_path, allow_test_edits=True)
        assert report.tampering_detected is False
