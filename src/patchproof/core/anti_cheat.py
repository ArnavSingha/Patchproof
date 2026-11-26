"""Anti-Cheat test tampering guard and integrity verification engine.

Detects unauthorized modifications, assertion count reductions, test function deletions,
and skip/suppression injections executed by autonomous coding agents.
"""

from __future__ import annotations

import ast
import pathlib
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from patchproof.core.applier import apply_patch
from patchproof.models.patch import CanonicalPatch, ChangeType, FileDelta

TEST_DIR_PARTS = {"test", "tests", "__tests__"}
TEST_EXTENSIONS = {".py"}


def is_test_file(path: str | pathlib.Path) -> bool:
    """Determine whether a canonical relative repository path represents a test file."""
    p = pathlib.PurePosixPath(str(path).replace("\\", "/"))
    parts_lower = {part.lower() for part in p.parts[:-1]}
    filename_lower = p.name.lower()

    if any(part in TEST_DIR_PARTS for part in parts_lower):
        if p.suffix in TEST_EXTENSIONS:
            return True

    if filename_lower == "conftest.py":
        return True

    if filename_lower.startswith("test_") and p.suffix == ".py":
        return True

    if filename_lower.endswith("_test.py"):
        return True

    return False


class TamperingViolationType(StrEnum):
    """Categorization of test tampering violations."""

    TEST_FILE_DELETED = "test_file_deleted"
    TEST_FUNCTION_DELETED = "test_function_deleted"
    ASSERTION_COUNT_REDUCED = "assertion_count_reduced"
    TEST_SUPPRESSION_INJECTED = "test_suppression_injected"
    TEST_SYNTAX_ERROR = "test_syntax_error"


class TamperingViolation(BaseModel):
    """Specific recorded violation of test integrity."""

    model_config = ConfigDict(frozen=True)

    violation_type: TamperingViolationType
    file_path: str
    target_name: str | None = None
    details: str


class AntiCheatAuditReport(BaseModel):
    """Comprehensive integrity report resulting from patch tampering audit."""

    model_config = ConfigDict(frozen=True)

    tampering_detected: bool
    violations: tuple[TamperingViolation, ...] = Field(default=())
    summary: str
    assertion_delta: int = 0
    tests_deleted_count: int = 0
    tests_suppressed_count: int = 0


class _TestFileAuditVisitor(ast.NodeVisitor):
    """AST visitor extracting test function signatures, assertion counts, and suppressions."""

    def __init__(self) -> None:
        self.test_functions: dict[str, list[str]] = {}
        self.assert_count: int = 0
        self.class_stack: list[str] = []
        self.suppressions: set[str] = set()

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self.class_stack.append(node.name)
        self.generic_visit(node)
        self.class_stack.pop()

    def visit_Assert(self, node: ast.Assert) -> None:
        self.assert_count += 1
        self.generic_visit(node)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._record_func(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._record_func(node)

    def _record_func(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        is_test = node.name.startswith("test_")
        if self.class_stack and self.class_stack[-1].startswith("Test"):
            is_test = True

        qualname = f"{self.class_stack[-1]}.{node.name}" if self.class_stack else node.name

        # Detect decorator suppressions: @pytest.mark.skip, @pytest.mark.xfail, @unittest.skip
        decorators: list[str] = []
        for dec in node.decorator_list:
            dec_repr = ast.unparse(dec) if hasattr(ast, "unparse") else ""
            decorators.append(dec_repr)
            if any(
                s in dec_repr for s in ("pytest.mark.skip", "pytest.mark.xfail", "unittest.skip")
            ):
                self.suppressions.add(f"{qualname} ({dec_repr})")

        # Detect in-body calls to pytest.skip()
        for sub in ast.walk(node):
            if isinstance(sub, ast.Call):
                func_repr = ast.unparse(sub.func) if hasattr(ast, "unparse") else ""
                if "pytest.skip" in func_repr:
                    self.suppressions.add(f"{qualname} (pytest.skip)")

        if is_test:
            self.test_functions[qualname] = decorators

        self.generic_visit(node)


def _audit_single_test_delta(
    delta: FileDelta,
    original_text: str,
    patched_text: str,
) -> tuple[list[TamperingViolation], int, int, int]:
    """Audit AST differences between baseline and patched content of a single test file."""
    violations: list[TamperingViolation] = []
    tests_deleted = 0
    tests_suppressed = 0

    try:
        orig_tree = ast.parse(original_text, filename=delta.path)
    except SyntaxError:
        # If baseline had syntax error, we cannot cleanly compare AST
        return (violations, 0, 0, 0)

    try:
        patched_tree = ast.parse(patched_text, filename=delta.path)
    except SyntaxError as exc:
        violations.append(
            TamperingViolation(
                violation_type=TamperingViolationType.TEST_SYNTAX_ERROR,
                file_path=delta.path,
                details=f"Patched test file failed AST parsing: {exc.msg} (line {exc.lineno})",
            )
        )
        return (violations, 0, 0, 0)

    orig_visitor = _TestFileAuditVisitor()
    orig_visitor.visit(orig_tree)

    patched_visitor = _TestFileAuditVisitor()
    patched_visitor.visit(patched_tree)

    assertion_delta = patched_visitor.assert_count - orig_visitor.assert_count
    if assertion_delta < 0:
        violations.append(
            TamperingViolation(
                violation_type=TamperingViolationType.ASSERTION_COUNT_REDUCED,
                file_path=delta.path,
                details=(
                    f"Test assertion count decreased from {orig_visitor.assert_count} "
                    f"to {patched_visitor.assert_count} (delta: {assertion_delta})"
                ),
            )
        )

    # Check for deleted test functions
    for orig_func in orig_visitor.test_functions:
        if orig_func not in patched_visitor.test_functions:
            tests_deleted += 1
            violations.append(
                TamperingViolation(
                    violation_type=TamperingViolationType.TEST_FUNCTION_DELETED,
                    file_path=delta.path,
                    target_name=orig_func,
                    details=f"Existing test function '{orig_func}' was deleted or renamed in patch",
                )
            )

    # Check for newly injected suppressions
    new_suppressions = patched_visitor.suppressions - orig_visitor.suppressions
    for supp in sorted(new_suppressions):
        tests_suppressed += 1
        violations.append(
            TamperingViolation(
                violation_type=TamperingViolationType.TEST_SUPPRESSION_INJECTED,
                file_path=delta.path,
                target_name=supp.split(" ")[0],
                details=f"Test suppression or skip marker injected: {supp}",
            )
        )

    return (violations, assertion_delta, tests_deleted, tests_suppressed)


def audit_patch_integrity(
    patch: CanonicalPatch,
    repo_root: pathlib.Path | str,
    allow_test_edits: bool = False,
) -> AntiCheatAuditReport:
    """Audit patch for intentional weakening, deletion, or suppression of tests.

    Args:
        patch: Canonical patch to inspect.
        repo_root: Base repository root directory containing baseline files.
        allow_test_edits: If True, test improvements and additions are permitted, but
                          deletions/reductions still trigger audit warnings.
    """
    root_path = pathlib.Path(repo_root).resolve()
    all_violations: list[TamperingViolation] = []
    total_assertion_delta = 0
    total_tests_deleted = 0
    total_tests_suppressed = 0

    for delta in patch.file_deltas:
        if not is_test_file(delta.path):
            continue

        # Check for whole test file deletion
        if delta.change_type == ChangeType.DELETE:
            total_tests_deleted += 1
            all_violations.append(
                TamperingViolation(
                    violation_type=TamperingViolationType.TEST_FILE_DELETED,
                    file_path=delta.path,
                    details=f"Test file '{delta.path}' was completely deleted in patch",
                )
            )
            continue

        # For modifications to existing test files
        target_file = root_path / delta.path
        if not target_file.is_file():
            # Newly added test file in patch
            if not allow_test_edits:
                # Adding new test files when test edits are not allowed
                pass
            continue

        try:
            original_text = target_file.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue

        # Apply hunks in-memory using an ephemeral directory
        import tempfile
        with tempfile.TemporaryDirectory(prefix="patchproof-anticheat-") as tmp_dir:
            tmp_root = pathlib.Path(tmp_dir)
            tmp_file = tmp_root / delta.path
            tmp_file.parent.mkdir(parents=True, exist_ok=True)
            tmp_file.write_text(original_text, encoding="utf-8")

            single_file_patch = CanonicalPatch(
                patch_type=patch.patch_type,
                file_deltas=(delta,),
                raw_diff=patch.raw_diff,
            )
            try:
                apply_patch(single_file_patch, tmp_root)
                patched_text = tmp_file.read_text(encoding="utf-8", errors="replace")
            except Exception as app_exc:
                all_violations.append(
                    TamperingViolation(
                        violation_type=TamperingViolationType.TEST_SYNTAX_ERROR,
                        file_path=delta.path,
                        details=f"Failed applying test patch in-memory for audit: {app_exc}",
                    )
                )
                continue

            v_list, a_delta, t_del, t_supp = _audit_single_test_delta(
                delta, original_text, patched_text
            )
            all_violations.extend(v_list)
            total_assertion_delta += a_delta
            total_tests_deleted += t_del
            total_tests_suppressed += t_supp

    if allow_test_edits:
        # When allow_test_edits is True, only flag explicit deletions and suppressions
        all_violations = [
            v for v in all_violations
            if v.violation_type in (
                TamperingViolationType.TEST_FILE_DELETED,
                TamperingViolationType.TEST_FUNCTION_DELETED,
                TamperingViolationType.TEST_SUPPRESSION_INJECTED,
                TamperingViolationType.ASSERTION_COUNT_REDUCED,
            )
        ]

    tampering_detected = len(all_violations) > 0
    if tampering_detected:
        summary = (
            f"Test tampering detected: {len(all_violations)} violation(s) found "
            f"({total_tests_deleted} deleted, {total_tests_suppressed} suppressed, "
            f"assertion delta: {total_assertion_delta})"
        )
    else:
        summary = "Patch integrity verified clean: no test tampering detected"

    return AntiCheatAuditReport(
        tampering_detected=tampering_detected,
        violations=tuple(all_violations),
        summary=summary,
        assertion_delta=total_assertion_delta,
        tests_deleted_count=total_tests_deleted,
        tests_suppressed_count=total_tests_suppressed,
    )
