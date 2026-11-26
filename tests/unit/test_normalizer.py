"""Unit tests for CanonicalPatch normalizer and models."""

import pytest
from pydantic import ValidationError

from patchproof.core.normalizer import parse_patch
from patchproof.exceptions import InvalidPatchFormatError
from patchproof.models.patch import ChangeType, PatchType

SAMPLE_SINGLE_FILE_DIFF = """\
diff --git a/src/math.py b/src/math.py
index e69de29..d95f3ad 100644
--- a/src/math.py
+++ b/src/math.py
@@ -1,2 +1,3 @@
 def add(a, b):
-    return a - b
+    # Fixed addition logic
+    return a + b
"""

SAMPLE_MULTI_FILE_DIFF = """\
diff --git a/src/math.py b/src/math.py
index e69de29..d95f3ad 100644
--- a/src/math.py
+++ b/src/math.py
@@ -1,2 +1,2 @@
 def add(a, b):
-    return a - b
+    return a + b
diff --git a/tests/test_math.py b/tests/test_math.py
index 4567890..1234567 100644
--- a/tests/test_math.py
+++ b/tests/test_math.py
@@ -1,2 +1,2 @@
 def test_add():
-    assert add(1, 1) == 0
+    assert add(1, 1) == 2
"""

SAMPLE_NEW_FILE_DIFF = """\
diff --git a/src/utils.py b/src/utils.py
new file mode 100644
index 0000000..83db48f
--- /dev/null
+++ b/src/utils.py
@@ -0,0 +1,2 @@
+def greet(name: str) -> str:
+    return f"Hello, {name}!"
"""

SAMPLE_DELETE_FILE_DIFF = """\
diff --git a/legacy.py b/legacy.py
deleted file mode 100644
index 83db48f..0000000
--- a/legacy.py
+++ /dev/null
@@ -1,2 +0,0 @@
-def deprecated_func():
-    pass
"""


def test_single_file_modification():
    """Verify single file modification parses correct hunks, lines, and metadata."""
    patch = parse_patch(SAMPLE_SINGLE_FILE_DIFF)

    assert patch.patch_type == PatchType.UNIFIED_DIFF
    assert patch.total_files == 1
    assert patch.modified_paths == ("src/math.py",)
    assert patch.total_additions == 2
    assert patch.total_deletions == 1

    delta = patch.file_deltas[0]
    assert delta.path == "src/math.py"
    assert delta.change_type == ChangeType.MODIFY
    assert not delta.is_new_file
    assert not delta.is_deleted_file
    assert len(delta.hunks) == 1

    hunk = delta.hunks[0]
    assert hunk.old_start == 1
    assert hunk.old_lines == 2
    assert hunk.new_start == 1
    assert hunk.new_lines == 3
    assert hunk.added_lines_count == 2
    assert hunk.deleted_lines_count == 1


def test_multi_file_modification():
    """Verify multiple file deltas are extracted and indexed accurately."""
    patch = parse_patch(SAMPLE_MULTI_FILE_DIFF)

    assert patch.total_files == 2
    assert patch.modified_paths == ("src/math.py", "tests/test_math.py")
    assert patch.total_additions == 2
    assert patch.total_deletions == 2

    delta1 = patch.file_deltas[0]
    assert delta1.path == "src/math.py"
    assert delta1.change_type == ChangeType.MODIFY

    delta2 = patch.file_deltas[1]
    assert delta2.path == "tests/test_math.py"
    assert delta2.change_type == ChangeType.MODIFY


def test_new_file_creation():
    """Verify newly created files are detected with ChangeType.ADD and correct line counts."""
    patch = parse_patch(SAMPLE_NEW_FILE_DIFF)

    assert patch.total_files == 1
    delta = patch.file_deltas[0]
    assert delta.path == "src/utils.py"
    assert delta.change_type == ChangeType.ADD
    assert delta.is_new_file
    assert not delta.is_deleted_file
    assert delta.total_added_lines == 2
    assert delta.total_deleted_lines == 0


def test_file_deletion():
    """Verify deleted files are detected with ChangeType.DELETE."""
    patch = parse_patch(SAMPLE_DELETE_FILE_DIFF)

    assert patch.total_files == 1
    delta = patch.file_deltas[0]
    assert delta.path == "legacy.py"
    assert delta.change_type == ChangeType.DELETE
    assert delta.is_deleted_file
    assert not delta.is_new_file
    assert delta.total_deleted_lines == 2
    assert delta.total_added_lines == 0


def test_crlf_normalization():
    """Verify Windows CRLF line endings are normalized to LF without changing delta output."""
    crlf_diff = SAMPLE_SINGLE_FILE_DIFF.replace("\n", "\r\n")
    patch_crlf = parse_patch(crlf_diff)
    patch_lf = parse_patch(SAMPLE_SINGLE_FILE_DIFF)

    assert patch_crlf.total_files == patch_lf.total_files
    assert patch_crlf.total_additions == patch_lf.total_additions
    assert patch_crlf.total_deletions == patch_lf.total_deletions
    assert "\r" not in patch_crlf.raw_diff


def test_multi_hunk_file():
    """Verify multiple non-contiguous hunks in a single file."""
    diff = """\
diff --git a/module.py b/module.py
--- a/module.py
+++ b/module.py
@@ -10,2 +10,2 @@
 def first():
-    return 1
+    return 10
@@ -50,2 +50,2 @@
 def second():
-    return 2
+    return 20
"""
    patch = parse_patch(diff)
    assert patch.total_files == 1
    delta = patch.file_deltas[0]
    assert len(delta.hunks) == 2
    assert delta.hunks[0].old_start == 10
    assert delta.hunks[1].old_start == 50
    assert delta.total_added_lines == 2
    assert delta.total_deleted_lines == 2


def test_hunk_single_line_syntax():
    """Verify unified diff hunks without explicit line counts (e.g. @@ -1 +1 @@)."""
    diff = """\
--- a/file.txt
+++ b/file.txt
@@ -1 +1 @@
-old
+new
"""
    patch = parse_patch(diff)
    delta = patch.file_deltas[0]
    assert len(delta.hunks) == 1
    hunk = delta.hunks[0]
    assert hunk.old_start == 1
    assert hunk.old_lines == 1
    assert hunk.new_start == 1
    assert hunk.new_lines == 1
    assert hunk.added_lines_count == 1
    assert hunk.deleted_lines_count == 1


def test_hunk_no_newline_marker():
    """Verify hunks containing '\\ No newline at end of file' are parsed correctly."""
    diff = """\
--- a/file.txt
+++ b/file.txt
@@ -1,2 +1,2 @@
 line1
-line2
\\ No newline at end of file
+line2_modified
\\ No newline at end of file
"""
    patch = parse_patch(diff)
    delta = patch.file_deltas[0]
    hunk = delta.hunks[0]
    assert hunk.old_lines == 2
    assert hunk.new_lines == 2
    assert any("\\" in line for line in hunk.lines)


def test_empty_diff_raises_error():
    """Verify empty or whitespace string raises InvalidPatchFormatError."""
    with pytest.raises(InvalidPatchFormatError, match="empty or contains only whitespace"):
        parse_patch("")

    with pytest.raises(InvalidPatchFormatError, match="empty or contains only whitespace"):
        parse_patch("   \n\n  \t  ")


def test_no_headers_raises_error():
    """Verify text without unified diff headers raises InvalidPatchFormatError."""
    with pytest.raises(InvalidPatchFormatError, match="No valid unified diff file headers found"):
        parse_patch("Hello world! This is just random prose without diff markers.")


def test_malformed_hunk_header_raises_error():
    """Verify malformed hunk header raises InvalidPatchFormatError."""
    diff = """\
--- a/file.txt
+++ b/file.txt
@@ bad header @@
+hello
"""
    with pytest.raises(InvalidPatchFormatError, match="Malformed hunk header"):
        parse_patch(diff)


def test_hunk_line_count_mismatch_raises_error():
    """Verify mismatch between declared lines and actual hunk body lines raises error."""
    diff = """\
--- a/file.txt
+++ b/file.txt
@@ -1,3 +1,1 @@
-only_one_line
"""
    with pytest.raises(InvalidPatchFormatError, match="Hunk line count mismatch"):
        parse_patch(diff)


def test_invalid_line_marker_raises_error():
    """Verify invalid character in hunk body raises InvalidPatchFormatError."""
    diff = """\
--- a/file.txt
+++ b/file.txt
@@ -1,2 +1,2 @@
 context line
? invalid line marker
"""
    with pytest.raises(InvalidPatchFormatError, match="Invalid line marker in hunk body"):
        parse_patch(diff)


def test_model_immutability():
    """Verify CanonicalPatch and FileDelta are frozen and reject mutation."""
    patch = parse_patch(SAMPLE_SINGLE_FILE_DIFF)

    with pytest.raises(ValidationError):
        patch.raw_diff = "tampered diff"

    with pytest.raises(ValidationError):
        patch.file_deltas[0].path = "tampered/path.py"


def test_diff_with_quoted_paths_containing_spaces():
    """Verify git diff headers with quoted paths containing spaces parse correctly."""
    diff = """\
diff --git "a/path with space/file name.py" "b/path with space/file name.py"
--- "a/path with space/file name.py"
+++ "b/path with space/file name.py"
@@ -1,2 +1,2 @@
 old
-line 1
+line 2
"""
    patch = parse_patch(diff)
    assert patch.total_files == 1
    assert patch.file_deltas[0].path == "path with space/file name.py"
    assert patch.file_deltas[0].total_added_lines == 1


def test_mode_only_change():
    """Verify git diff with mode change only and no content hunks."""
    diff = """\
diff --git a/script.sh b/script.sh
old mode 100644
new mode 100755
"""
    patch = parse_patch(diff)
    assert patch.total_files == 1
    delta = patch.file_deltas[0]
    assert delta.path == "script.sh"
    assert delta.change_type == ChangeType.MODIFY
    assert len(delta.hunks) == 0


def test_pure_rename_diff():
    """Verify 100% similarity pure rename without hunks."""
    diff = """\
diff --git a/old_name.py b/new_name.py
similarity index 100%
rename from old_name.py
rename to new_name.py
"""
    patch = parse_patch(diff)
    assert patch.total_files == 1
    delta = patch.file_deltas[0]
    assert delta.path == "new_name.py"
    assert delta.change_type == ChangeType.MODIFY
    assert delta.old_path == "old_name.py"
    assert delta.new_path == "new_name.py"
    assert len(delta.hunks) == 0


def test_standalone_binary_diff():
    """Verify binary file diff line."""
    diff = """\
diff --git a/logo.png b/logo.png
Binary files a/logo.png and b/logo.png differ
"""
    patch = parse_patch(diff)
    assert patch.total_files == 1
    delta = patch.file_deltas[0]
    assert delta.path == "logo.png"
    assert delta.is_binary is True
    assert delta.change_type == ChangeType.MODIFY


def test_index_header_diff():
    """Verify parsing diffs containing SVN/CVS Index: headers."""
    diff = """\
Index: src/main.py
===================================================================
--- src/main.py	(revision 123)
+++ src/main.py	(working copy)
@@ -1,2 +1,2 @@
-old_val = 1
+old_val = 2
 unchanged = True
"""
    patch = parse_patch(diff)
    assert patch.total_files == 1
    assert patch.file_deltas[0].path == "src/main.py"
    assert len(patch.file_deltas[0].hunks) == 1


def test_folder_named_a_not_double_stripped():
    """Verify single-pass prefix strip prevents path /a/b/c becoming b/c."""
    diff = """\
diff --git a/a/foo.py b/a/foo.py
--- a/a/foo.py
+++ b/a/foo.py
@@ -1,1 +1,1 @@
-x = 1
+x = 2
"""
    patch = parse_patch(diff)
    assert patch.file_deltas[0].path == "a/foo.py"
