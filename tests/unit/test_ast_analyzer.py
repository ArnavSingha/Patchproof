"""Unit tests for Python AST analyzer."""

from patchproof.core.ast_analyzer import (
    analyze_patch_ast,
    analyze_python_source,
    extract_delta_line_ranges,
)
from patchproof.core.normalizer import parse_patch
from patchproof.models.ast import EntityType

SAMPLE_PYTHON_SOURCE = """\
import os
import sys
from typing import List, Optional

class UserManager:
    def __init__(self, db_url: str) -> None:
        self.db = connect(db_url)

    def get_user(self, user_id: int) -> Optional[dict]:
        user = self.db.find(user_id)
        validate(user)
        return user

    def delete_user(self, user_id: int) -> None:
        self.db.remove(user_id)

def helper_function(val: str) -> str:
    cleaned = sanitize(val)
    return cleaned

async def async_fetch_data(url: str) -> str:
    res = await http_get(url)
    return res
"""


def test_analyze_python_source_function_modification():
    """Verify top-level function intersection is correctly mapped."""
    report = analyze_python_source(SAMPLE_PYTHON_SOURCE, [(18, 19)], file_path="src/users.py")

    assert report.path == "src/users.py"
    assert len(report.modified_entities) == 1
    entity = report.modified_entities[0]
    assert entity.name == "helper_function"
    assert entity.entity_type == EntityType.FUNCTION
    assert entity.parent_class is None
    assert "sanitize" in report.syntactic_calls


def test_analyze_python_source_class_and_method():
    """Verify class and enclosed method modifications are both identified."""
    report = analyze_python_source(SAMPLE_PYTHON_SOURCE, [(11, 12)], file_path="src/users.py")

    entity_names = [e.name for e in report.modified_entities]
    assert "UserManager" in entity_names
    assert "get_user" in entity_names

    method_ent = next(e for e in report.modified_entities if e.name == "get_user")
    assert method_ent.entity_type == EntityType.METHOD
    assert method_ent.parent_class == "UserManager"
    assert "validate" in report.syntactic_calls


def test_analyze_python_source_async_function():
    """Verify async functions are parsed and recognized."""
    report = analyze_python_source(SAMPLE_PYTHON_SOURCE, [(23, 23)], file_path="src/api.py")

    assert len(report.modified_entities) == 1
    assert report.modified_entities[0].name == "async_fetch_data"
    assert report.modified_entities[0].entity_type == EntityType.FUNCTION


def test_analyze_python_source_imports_extraction():
    """Verify all static imports are extracted from source file."""
    report = analyze_python_source(SAMPLE_PYTHON_SOURCE, [(1, 1)], file_path="src/users.py")

    assert "os" in report.imported_modules
    assert "sys" in report.imported_modules
    assert "typing" in report.imported_modules


def test_analyze_python_source_syntax_error():
    """Verify unparseable Python syntax degrades gracefully to an empty report."""
    bad_code = "def broken_syntax(:\n    pass"
    report = analyze_python_source(bad_code, [(1, 2)], file_path="bad.py")

    assert report.path == "bad.py"
    assert report.modified_entities == ()
    assert report.imported_modules == ()
    assert report.syntactic_calls == ()


def test_extract_delta_line_ranges():
    """Verify line ranges are correctly calculated from FileDelta hunks."""
    diff = """\
--- a/test.py
+++ b/test.py
@@ -10,1 +10,4 @@
 a
+b
+c
+d
"""
    patch = parse_patch(diff)
    delta = patch.file_deltas[0]
    ranges = extract_delta_line_ranges(delta)

    assert ranges == [(10, 13)]


def test_analyze_patch_ast_integration():
    """Verify end-to-end integration between CanonicalPatch and AST analysis."""
    diff = """\
diff --git a/src/users.py b/src/users.py
--- a/src/users.py
+++ b/src/users.py
@@ -11,2 +11,3 @@
     def get_user(self, user_id: int) -> Optional[dict]:
+        log_access(user_id)
         user = self.db.find(user_id)
"""
    patch = parse_patch(diff)

    def resolver(path: str) -> str | None:
        if path == "src/users.py":
            return SAMPLE_PYTHON_SOURCE
        return None

    report = analyze_patch_ast(patch, source_resolver=resolver)

    assert len(report.file_reports) == 1
    file_rep = report.file_reports[0]
    assert file_rep.path == "src/users.py"
    assert any(e.name == "get_user" for e in file_rep.modified_entities)
    assert "log_access" in file_rep.syntactic_calls


def test_nested_helper_function_classification():
    """Verify closures inside methods are classified as FUNCTION, not METHOD."""
    source = """\
class Worker:
    def process(self):
        def helper():
            return 42
        return helper()
"""
    report = analyze_python_source(source, [(3, 4)], "worker.py")
    funcs = [e for e in report.modified_entities if e.name == "helper"]
    assert len(funcs) == 1
    assert funcs[0].entity_type == EntityType.FUNCTION
    assert funcs[0].parent_class is None


def test_relative_imports():
    """Verify static extraction of relative imports."""
    source = """\
from . import sibling
from ..parent import grandparent
import os
"""
    report = analyze_python_source(source, [(1, 1)], "module.py")
    expected_matches = {".", ".sibling", "..parent"}
    assert any(m in report.imported_modules for m in expected_matches)
    assert "os" in report.imported_modules


def test_pre_patch_coordinate_ranges():
    """Verify pre_patch flag extracts old hunk line coordinates."""
    diff = """\
diff --git a/src/app.py b/src/app.py
--- a/src/app.py
+++ b/src/app.py
@@ -20,5 +30,1 @@
-old_1
-old_2
-old_3
-old_4
-old_5
+new_1
"""
    patch = parse_patch(diff)
    delta = patch.file_deltas[0]
    post_ranges = extract_delta_line_ranges(delta, pre_patch=False)
    pre_ranges = extract_delta_line_ranges(delta, pre_patch=True)
    assert post_ranges == [(30, 30)]
    assert pre_ranges == [(20, 24)]
