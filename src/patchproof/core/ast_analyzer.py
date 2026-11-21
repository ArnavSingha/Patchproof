"""Deterministic Python AST analysis for modified entities, imports, and calls."""

from __future__ import annotations

import ast
import textwrap
from collections.abc import Callable, Iterable

from patchproof.models.ast import (
    ASTAnalysisReport,
    ASTEntity,
    EntityType,
    FileASTReport,
)
from patchproof.models.patch import CanonicalPatch, FileDelta


class _EntityVisitor(ast.NodeVisitor):
    """AST visitor extracting class and function boundaries with line numbers."""

    def __init__(self) -> None:
        self.entities: list[ASTEntity] = []
        self.current_class: str | None = None

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        end_line = getattr(node, "end_lineno", node.lineno)
        entity = ASTEntity(
            name=node.name,
            entity_type=EntityType.CLASS,
            parent_class=self.current_class,
            start_line=node.lineno,
            end_line=end_line,
        )
        self.entities.append(entity)

        prev_class = self.current_class
        self.current_class = node.name
        self.generic_visit(node)
        self.current_class = prev_class

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        end_line = getattr(node, "end_lineno", node.lineno)
        ent_type = EntityType.METHOD if self.current_class else EntityType.FUNCTION
        entity = ASTEntity(
            name=node.name,
            entity_type=ent_type,
            parent_class=self.current_class,
            start_line=node.lineno,
            end_line=end_line,
        )
        self.entities.append(entity)
        self.generic_visit(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        end_line = getattr(node, "end_lineno", node.lineno)
        ent_type = EntityType.METHOD if self.current_class else EntityType.FUNCTION
        entity = ASTEntity(
            name=node.name,
            entity_type=ent_type,
            parent_class=self.current_class,
            start_line=node.lineno,
            end_line=end_line,
        )
        self.entities.append(entity)
        self.generic_visit(node)


def _extract_imports(tree: ast.AST) -> tuple[str, ...]:
    """Extract all static imported module names from an AST."""
    imported_modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imported_modules.add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                imported_modules.add(node.module)
    return tuple(sorted(imported_modules))


def _extract_call_name(node: ast.Call) -> str | None:
    """Extract string identifier from a Call node."""
    func = node.func
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return None


def _extract_calls_in_ranges(tree: ast.AST, ranges: Iterable[tuple[int, int]]) -> tuple[str, ...]:
    """Extract syntactic function/method calls whose line numbers fall within modified spans."""
    calls: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            lineno = getattr(node, "lineno", None)
            if lineno is None:
                continue
            for start, end in ranges:
                if start <= lineno <= end:
                    name = _extract_call_name(node)
                    if name:
                        calls.add(name)
                    break
    return tuple(sorted(calls))


def _extract_calls_from_text(code_fragment: str) -> set[str]:
    """Extract calls from an arbitrary snippet of code."""
    calls: set[str] = set()
    try:
        tree = ast.parse(code_fragment)
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                name = _extract_call_name(node)
                if name:
                    calls.add(name)
    except SyntaxError:
        pass
    return calls


def _spans_overlap(s1: int, e1: int, s2: int, e2: int) -> bool:
    """Return True if span [s1, e1] overlaps with [s2, e2]."""
    return not (e1 < s2 or s1 > e2)


def analyze_python_source(
    source_code: str,
    modified_ranges: list[tuple[int, int]],
    file_path: str = "",
) -> FileASTReport:
    """Deterministically analyze Python source code against a set of modified line spans.

    Args:
        source_code: Raw Python source code string.
        modified_ranges: List of (start_line, end_line) spans affected by changes.
        file_path: Optional repository-relative path of the analyzed file.

    Returns:
        FileASTReport: Report detailing modified entities, static imports, and calls.
    """
    try:
        tree = ast.parse(source_code, filename=file_path or "<string>")
    except SyntaxError:
        return FileASTReport(
            path=file_path,
            modified_entities=(),
            imported_modules=(),
            syntactic_calls=(),
        )

    visitor = _EntityVisitor()
    visitor.visit(tree)

    intersected_entities: list[ASTEntity] = []
    for entity in visitor.entities:
        for m_start, m_end in modified_ranges:
            if _spans_overlap(entity.start_line, entity.end_line, m_start, m_end):
                intersected_entities.append(entity)
                break

    imports = _extract_imports(tree)
    calls = _extract_calls_in_ranges(tree, modified_ranges)

    return FileASTReport(
        path=file_path,
        modified_entities=tuple(intersected_entities),
        imported_modules=imports,
        syntactic_calls=calls,
    )


def extract_delta_line_ranges(delta: FileDelta) -> list[tuple[int, int]]:
    """Calculate the target line ranges affected by a FileDelta."""
    ranges: list[tuple[int, int]] = []
    for hunk in delta.hunks:
        start = hunk.new_start if hunk.new_start > 0 else 1
        length = hunk.new_lines if hunk.new_lines > 0 else 1
        ranges.append((start, start + length - 1))
    return ranges


def analyze_patch_ast(
    patch: CanonicalPatch,
    source_resolver: Callable[[str], str | None] | None = None,
) -> ASTAnalysisReport:
    """Analyze all Python files in a CanonicalPatch against available source code.

    Args:
        patch: Canonical patch to inspect.
        source_resolver: Optional callable `(path: str) -> source_code | None`.

    Returns:
        ASTAnalysisReport: Consolidated AST report across all patch files.
    """
    file_reports: list[FileASTReport] = []

    for delta in patch.file_deltas:
        if not delta.path.endswith(".py"):
            continue

        ranges = extract_delta_line_ranges(delta)
        source = source_resolver(delta.path) if source_resolver else None

        # Extract calls introduced in added hunk lines (dedented for valid AST parsing)
        added_snippet = textwrap.dedent(
            "\n".join(
                line[1:]
                for hunk in delta.hunks
                for line in hunk.lines
                if line.startswith("+") and not line.startswith("+++")
            )
        )
        patch_introduced_calls = _extract_calls_from_text(added_snippet)

        if source is not None:
            rep = analyze_python_source(source, ranges, file_path=delta.path)
            all_file_calls = tuple(sorted(set(rep.syntactic_calls) | patch_introduced_calls))
            merged_rep = FileASTReport(
                path=rep.path,
                modified_entities=rep.modified_entities,
                imported_modules=rep.imported_modules,
                syntactic_calls=all_file_calls,
            )
            file_reports.append(merged_rep)
        else:
            file_reports.append(
                FileASTReport(
                    path=delta.path,
                    modified_entities=(),
                    imported_modules=(),
                    syntactic_calls=tuple(sorted(patch_introduced_calls)),
                )
            )

    return ASTAnalysisReport(file_reports=tuple(file_reports))
