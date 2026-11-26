"""Polyglot repository stack detector.

Auto-detects project language stack (Python, Node.js, Go, Rust),
associated runner images, and default test commands.
"""

from __future__ import annotations

import pathlib
from dataclasses import dataclass
from enum import StrEnum


class ProjectKind(StrEnum):
    """Supported project language ecosystems."""

    PYTHON = "python"
    NODE = "node"
    GO = "go"
    RUST = "rust"


@dataclass(frozen=True)
class ProjectProfile:
    """Detected project configuration profile."""

    kind: ProjectKind
    default_image: str
    default_test_command: tuple[str, ...]


DEFAULT_PROFILES: dict[ProjectKind, ProjectProfile] = {
    ProjectKind.PYTHON: ProjectProfile(
        kind=ProjectKind.PYTHON,
        default_image="patchproof-runner-python:3.11",
        default_test_command=("pytest", "-q"),
    ),
    ProjectKind.NODE: ProjectProfile(
        kind=ProjectKind.NODE,
        default_image="patchproof-runner-node:20",
        default_test_command=("npm", "test"),
    ),
    ProjectKind.GO: ProjectProfile(
        kind=ProjectKind.GO,
        default_image="patchproof-runner-go:1.22",
        default_test_command=("go", "test", "./..."),
    ),
    ProjectKind.RUST: ProjectProfile(
        kind=ProjectKind.RUST,
        default_image="patchproof-runner-rust:1.78",
        default_test_command=("cargo", "test"),
    ),
}

PYTHON_MARKERS = ("pyproject.toml", "setup.py", "setup.cfg", "requirements.txt", "pytest.ini")
NODE_MARKERS = ("package.json", "tsconfig.json")
GO_MARKERS = ("go.mod", "go.sum")
RUST_MARKERS = ("Cargo.toml", "Cargo.lock")


def detect_project_stack(repo_path: pathlib.Path | str) -> ProjectProfile:
    """Inspect repository root and return the matching ProjectProfile."""
    root = pathlib.Path(repo_path).resolve()

    if any((root / m).is_file() for m in RUST_MARKERS):
        return DEFAULT_PROFILES[ProjectKind.RUST]

    if any((root / m).is_file() for m in GO_MARKERS):
        return DEFAULT_PROFILES[ProjectKind.GO]

    if any((root / m).is_file() for m in PYTHON_MARKERS):
        return DEFAULT_PROFILES[ProjectKind.PYTHON]

    if any((root / m).is_file() for m in NODE_MARKERS):
        return DEFAULT_PROFILES[ProjectKind.NODE]

    # Default fallback
    return DEFAULT_PROFILES[ProjectKind.PYTHON]
