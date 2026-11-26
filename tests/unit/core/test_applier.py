"""Unit tests for isolated patch application to staging workspaces."""

from __future__ import annotations

import pathlib

import pytest

from patchproof.core.applier import apply_patch
from patchproof.exceptions import (
    PatchApplicationError,
    SandboxSecurityError,
)
from patchproof.models.patch import (
    CanonicalPatch,
    ChangeType,
    FileDelta,
    PatchHunk,
    PatchType,
)


@pytest.fixture
def workspace_dir(tmp_path: pathlib.Path) -> pathlib.Path:
    ws = tmp_path / "staging_ws"
    ws.mkdir()
    (ws / "main.py").write_text("def hello():\n    return 'hello'\n", encoding="utf-8")
    (ws / "math_ops.py").write_text(
        "def add(a, b):\n    return a - b\n\ndef sub(a, b):\n    return a - b\n",
        encoding="utf-8",
    )
    return ws


class TestPatchApplier:
    """Test safe and deterministic patch application on staging workspaces."""

    def test_apply_unified_diff_modification(self, workspace_dir: pathlib.Path) -> None:
        diff_str = (
            "--- a/math_ops.py\n"
            "+++ b/math_ops.py\n"
            "@@ -1,4 +1,4 @@\n"
            " def add(a, b):\n"
            "-    return a - b\n"
            "+    return a + b\n"
            " \n"
            " def sub(a, b):\n"
        )
        modified = apply_patch(diff_str, workspace_dir)

        assert "math_ops.py" in modified
        content = (workspace_dir / "math_ops.py").read_text(encoding="utf-8")
        assert "return a + b" in content
        assert "return a - b" in content  # sub still has it

    def test_apply_new_file_creation(self, workspace_dir: pathlib.Path) -> None:
        diff_str = (
            "--- /dev/null\n"
            "+++ b/utils/helpers.py\n"
            "@@ -0,0 +1,2 @@\n"
            "+def get_val():\n"
            "+    return 42\n"
        )
        modified = apply_patch(diff_str, workspace_dir)

        assert "utils/helpers.py" in modified
        new_file = workspace_dir / "utils" / "helpers.py"
        assert new_file.exists()
        assert "return 42" in new_file.read_text(encoding="utf-8")

    def test_apply_file_deletion(self, workspace_dir: pathlib.Path) -> None:
        diff_str = (
            "--- a/main.py\n+++ /dev/null\n@@ -1,2 +0,0 @@\n-def hello():\n-    return 'hello'\n"
        )
        modified = apply_patch(diff_str, workspace_dir)

        assert "main.py" in modified
        assert not (workspace_dir / "main.py").exists()

    def test_apply_search_replace_block(self, workspace_dir: pathlib.Path) -> None:
        sr_block = (
            "math_ops.py\n"
            "<<<<<<< SEARCH\n"
            "def add(a, b):\n"
            "    return a - b\n"
            "=======\n"
            "def add(a, b):\n"
            "    return a + b\n"
            ">>>>>>> REPLACE\n"
        )
        modified = apply_patch(sr_block, workspace_dir)

        assert "math_ops.py" in modified
        content = (workspace_dir / "math_ops.py").read_text(encoding="utf-8")
        assert "return a + b" in content

    def test_reject_path_traversal_escaping_workspace(self, workspace_dir: pathlib.Path) -> None:
        malicious_patch = CanonicalPatch(
            patch_type=PatchType.UNIFIED_DIFF,
            raw_diff="fake",
            file_deltas=(
                FileDelta(
                    path="../../escaped.py",
                    change_type=ChangeType.ADD,
                    hunks=(
                        PatchHunk(
                            old_start=0,
                            old_lines=0,
                            new_start=1,
                            new_lines=1,
                            header="@@ -0,0 +1,1 @@",
                            lines=("+malicious",),
                        ),
                    ),
                ),
            ),
        )
        with pytest.raises(SandboxSecurityError, match="traversal"):
            apply_patch(malicious_patch, workspace_dir)

    def test_fail_on_hunk_context_mismatch(self, workspace_dir: pathlib.Path) -> None:
        diff_str = (
            "--- a/main.py\n"
            "+++ b/main.py\n"
            "@@ -1,2 +1,2 @@\n"
            " def wrong_function():\n"
            "-    return 'hello'\n"
            "+    return 'world'\n"
        )
        with pytest.raises(PatchApplicationError, match="Context mismatch"):
            apply_patch(diff_str, workspace_dir)

    def test_fail_on_modifying_nonexistent_file(self, workspace_dir: pathlib.Path) -> None:
        diff_str = "--- a/missing.py\n+++ b/missing.py\n@@ -1,1 +1,1 @@\n-x\n+y\n"
        with pytest.raises(PatchApplicationError, match="does not exist"):
            apply_patch(diff_str, workspace_dir)

    def test_fail_on_creating_existing_file(self, workspace_dir: pathlib.Path) -> None:
        diff_str = "--- /dev/null\n+++ b/main.py\n@@ -0,0 +1,1 @@\n+new line\n"
        with pytest.raises(PatchApplicationError, match="already exists"):
            apply_patch(diff_str, workspace_dir)

    def test_apply_modification_clearing_all_lines_produces_empty_file(
        self, workspace_dir: pathlib.Path
    ) -> None:
        diff_str = (
            "--- a/main.py\n+++ b/main.py\n@@ -1,2 +0,0 @@\n-def hello():\n-    return 'hello'\n"
        )
        apply_patch(diff_str, workspace_dir)
        content = (workspace_dir / "main.py").read_bytes()
        assert content == b""

    def test_reject_workspace_root_as_target(self, workspace_dir: pathlib.Path) -> None:
        root_patch = CanonicalPatch(
            patch_type=PatchType.UNIFIED_DIFF,
            raw_diff="fake",
            file_deltas=(
                FileDelta(
                    path=".",
                    change_type=ChangeType.DELETE,
                ),
            ),
        )
        with pytest.raises(SandboxSecurityError, match="workspace root"):
            apply_patch(root_patch, workspace_dir)

    def test_apply_out_of_order_hunks(self, workspace_dir: pathlib.Path) -> None:
        (workspace_dir / "order.py").write_text("line1\nline2\nline3\nline4\n", encoding="utf-8")
        hunk1 = PatchHunk(
            old_start=1,
            old_lines=1,
            new_start=1,
            new_lines=1,
            header="@@ -1,1 +1,1 @@",
            lines=("-line1", "+LINE_ONE"),
        )
        hunk2 = PatchHunk(
            old_start=3,
            old_lines=1,
            new_start=3,
            new_lines=1,
            header="@@ -3,1 +3,1 @@",
            lines=("-line3", "+LINE_THREE"),
        )
        # Pass hunks in reverse order (hunk2 before hunk1)
        patch = CanonicalPatch(
            patch_type=PatchType.UNIFIED_DIFF,
            raw_diff="fake",
            file_deltas=(
                FileDelta(
                    path="order.py",
                    change_type=ChangeType.MODIFY,
                    hunks=(hunk2, hunk1),
                ),
            ),
        )
        apply_patch(patch, workspace_dir)
        content = (workspace_dir / "order.py").read_text(encoding="utf-8")
        assert "LINE_ONE" in content
        assert "line2" in content
        assert "LINE_THREE" in content
        assert "line4" in content
