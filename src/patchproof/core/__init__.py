"""Patch normalizer and analyzer core module."""

from patchproof.core.ast_analyzer import (
    analyze_patch_ast,
    analyze_python_source,
    extract_delta_line_ranges,
)
from patchproof.core.normalizer import parse_patch
from patchproof.core.search_replace import parse_search_replace

__all__ = [
    "analyze_patch_ast",
    "analyze_python_source",
    "extract_delta_line_ranges",
    "parse_patch",
    "parse_search_replace",
]
