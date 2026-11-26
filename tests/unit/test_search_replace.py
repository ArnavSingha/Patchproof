"""Unit tests for Aider-style SEARCH/REPLACE block normalizer."""

import pytest

from patchproof.core.search_replace import parse_search_replace
from patchproof.exceptions import InvalidPatchFormatError
from patchproof.models.patch import ChangeType, PatchType

SAMPLE_SINGLE_SEARCH_REPLACE = """\
src/calculator.py
<<<<<<< SEARCH
def add(a, b):
    return a - b
=======
def add(a, b):
    return a + b
>>>>>>> REPLACE
"""

SAMPLE_MULTI_BLOCK_SINGLE_FILE = """\
src/math_ops.py
<<<<<<< SEARCH
def foo():
    return 1
=======
def foo():
    return 10
>>>>>>> REPLACE

<<<<<<< SEARCH
def bar():
    return 2
=======
def bar():
    return 20
>>>>>>> REPLACE
"""

SAMPLE_MULTI_FILE_SEARCH_REPLACE = """\
src/app.py
<<<<<<< SEARCH
DEBUG = False
=======
DEBUG = True
>>>>>>> REPLACE

tests/test_app.py
<<<<<<< SEARCH
def test_debug():
    assert DEBUG is False
=======
def test_debug():
    assert DEBUG is True
>>>>>>> REPLACE
"""

SAMPLE_ADDITION_BLOCK = """\
src/new_feature.py
<<<<<<< SEARCH
=======
def brand_new_feature():
    return "ok"
>>>>>>> REPLACE
"""

SAMPLE_DELETION_BLOCK = """\
src/deprecated.py
<<<<<<< SEARCH
def obsolete_code():
    pass
=======
>>>>>>> REPLACE
"""


def test_single_search_replace():
    """Verify single search/replace block parses correctly into CanonicalPatch."""
    patch = parse_search_replace(SAMPLE_SINGLE_SEARCH_REPLACE)

    assert patch.patch_type == PatchType.SEARCH_REPLACE
    assert patch.total_files == 1
    assert patch.modified_paths == ("src/calculator.py",)

    delta = patch.file_deltas[0]
    assert delta.path == "src/calculator.py"
    assert delta.change_type == ChangeType.MODIFY
    assert len(delta.hunks) == 1

    hunk = delta.hunks[0]
    assert hunk.old_lines == 2
    assert hunk.new_lines == 2
    assert hunk.added_lines_count == 2
    assert hunk.deleted_lines_count == 2


def test_multi_block_single_file():
    """Verify multiple search/replace blocks within one file create multiple hunks."""
    patch = parse_search_replace(SAMPLE_MULTI_BLOCK_SINGLE_FILE)

    assert patch.total_files == 1
    delta = patch.file_deltas[0]
    assert delta.path == "src/math_ops.py"
    assert len(delta.hunks) == 2
    assert patch.total_additions == 4
    assert patch.total_deletions == 4


def test_multi_file_search_replace():
    """Verify multiple files with distinct search/replace blocks are parsed."""
    patch = parse_search_replace(SAMPLE_MULTI_FILE_SEARCH_REPLACE)

    assert patch.total_files == 2
    assert patch.modified_paths == ("src/app.py", "tests/test_app.py")
    assert patch.file_deltas[0].total_added_lines == 1
    assert patch.file_deltas[1].total_added_lines == 2


def test_addition_empty_search():
    """Verify empty SEARCH block is treated as an addition (ChangeType.ADD)."""
    patch = parse_search_replace(SAMPLE_ADDITION_BLOCK)

    assert patch.total_files == 1
    delta = patch.file_deltas[0]
    assert delta.path == "src/new_feature.py"
    assert delta.change_type == ChangeType.ADD
    assert delta.total_added_lines == 2
    assert delta.total_deleted_lines == 0


def test_deletion_empty_replace():
    """Verify empty REPLACE block is treated as a deletion (ChangeType.DELETE)."""
    patch = parse_search_replace(SAMPLE_DELETION_BLOCK)

    assert patch.total_files == 1
    delta = patch.file_deltas[0]
    assert delta.path == "src/deprecated.py"
    assert delta.change_type == ChangeType.DELETE
    assert delta.total_deleted_lines == 2
    assert delta.total_added_lines == 0


def test_filename_formatting_variations():
    """Verify support for backtick-enclosed and prefix-labeled filenames."""
    formatted_diff = """\
`src/backticked.py`
<<<<<<< SEARCH
old
=======
new
>>>>>>> REPLACE

File: src/prefixed.py
<<<<<<< SEARCH
foo
=======
bar
>>>>>>> REPLACE
"""
    patch = parse_search_replace(formatted_diff)
    assert patch.total_files == 2
    assert patch.modified_paths == ("src/backticked.py", "src/prefixed.py")


def test_missing_search_start():
    """Verify error when <<<<<<< SEARCH delimiter is missing."""
    with pytest.raises(InvalidPatchFormatError, match="Missing required delimiter"):
        parse_search_replace("some text without delimiters")


def test_missing_divider():
    """Verify error when ======= divider is omitted."""
    bad_diff = """\
src/file.py
<<<<<<< SEARCH
code
>>>>>>> REPLACE
"""
    with pytest.raises(InvalidPatchFormatError, match="======="):
        parse_search_replace(bad_diff)


def test_missing_replace_end():
    """Verify error when >>>>>>> REPLACE end delimiter is omitted."""
    bad_diff = """\
src/file.py
<<<<<<< SEARCH
code
=======
new_code
"""
    with pytest.raises(InvalidPatchFormatError, match="missing '>>>>>>> REPLACE'"):
        parse_search_replace(bad_diff)


def test_missing_filename():
    """Verify error when SEARCH block begins without a preceding filename."""
    bad_diff = """\
<<<<<<< SEARCH
code
=======
new_code
>>>>>>> REPLACE
"""
    with pytest.raises(InvalidPatchFormatError, match="without a target filename"):
        parse_search_replace(bad_diff)


def test_both_empty_search_and_replace():
    """Verify error when both search and replace sections are completely empty."""
    bad_diff = """\
src/file.py
<<<<<<< SEARCH
=======
>>>>>>> REPLACE
"""
    with pytest.raises(InvalidPatchFormatError, match="both empty SEARCH and empty REPLACE"):
        parse_search_replace(bad_diff)


def test_unexpected_delimiter_outside_block():
    """Verify error when delimiter appears out of order."""
    bad_diff = """\
src/file.py
=======
"""
    with pytest.raises(InvalidPatchFormatError, match="Unexpected delimiter"):
        parse_search_replace(bad_diff)


def test_extensionless_root_files():
    """Verify root extensionless files like Makefile and Dockerfile parse correctly."""
    diff = """\
Makefile
<<<<<<< SEARCH
all: test
=======
all: lint test
>>>>>>> REPLACE
"""
    patch = parse_search_replace(diff)
    assert patch.total_files == 1
    delta = patch.file_deltas[0]
    assert delta.path == "Makefile"
    assert len(delta.hunks) == 1
