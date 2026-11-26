"""Patch normalizer and analyzer core module."""

from patchproof.core.applier import apply_patch
from patchproof.core.ast_analyzer import (
    analyze_patch_ast,
    analyze_python_source,
    extract_delta_line_ranges,
)
from patchproof.core.detector import ProjectKind, ProjectProfile, detect_project_stack
from patchproof.core.git_provider import (
    get_head_diff,
    get_uncommitted_diff,
    install_pre_commit_hook,
    is_git_repository,
)
from patchproof.core.normalizer import parse_patch
from patchproof.core.search_replace import parse_search_replace

__all__ = [
    "ProjectKind",
    "ProjectProfile",
    "analyze_patch_ast",
    "analyze_python_source",
    "apply_patch",
    "detect_project_stack",
    "extract_delta_line_ranges",
    "get_head_diff",
    "get_uncommitted_diff",
    "install_pre_commit_hook",
    "is_git_repository",
    "parse_patch",
    "parse_search_replace",
]
