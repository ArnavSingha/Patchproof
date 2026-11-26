"""Unit tests for polyglot project stack auto-detection."""

from __future__ import annotations

import pathlib

from patchproof.core.detector import ProjectKind, detect_project_stack


class TestProjectStackDetector:
    """Test auto-detecting repository language, runner image, and default test command."""

    def test_detects_python_project_pyproject_toml(self, tmp_path: pathlib.Path) -> None:
        (tmp_path / "pyproject.toml").write_text("[project]\nname='foo'\n")
        profile = detect_project_stack(tmp_path)

        assert profile.kind == ProjectKind.PYTHON
        assert profile.default_image == "patchproof-runner-python:3.11"
        assert profile.default_test_command == ("pytest", "-q")

    def test_detects_python_project_requirements_txt(self, tmp_path: pathlib.Path) -> None:
        (tmp_path / "requirements.txt").write_text("pytest>=8.0.0\n")
        profile = detect_project_stack(tmp_path)

        assert profile.kind == ProjectKind.PYTHON
        assert profile.default_test_command == ("pytest", "-q")

    def test_detects_node_project_package_json(self, tmp_path: pathlib.Path) -> None:
        (tmp_path / "package.json").write_text('{"name": "foo", "scripts": {"test": "jest"}}\n')
        profile = detect_project_stack(tmp_path)

        assert profile.kind == ProjectKind.NODE
        assert profile.default_image == "patchproof-runner-node:20"
        assert profile.default_test_command == ("npm", "test")

    def test_detects_go_project_go_mod(self, tmp_path: pathlib.Path) -> None:
        (tmp_path / "go.mod").write_text("module example.com/foo\ngo 1.22\n")
        profile = detect_project_stack(tmp_path)

        assert profile.kind == ProjectKind.GO
        assert profile.default_image == "patchproof-runner-go:1.22"
        assert profile.default_test_command == ("go", "test", "./...")

    def test_detects_rust_project_cargo_toml(self, tmp_path: pathlib.Path) -> None:
        (tmp_path / "Cargo.toml").write_text("[package]\nname = 'foo'\nversion = '0.1.0'\n")
        profile = detect_project_stack(tmp_path)

        assert profile.kind == ProjectKind.RUST
        assert profile.default_image == "patchproof-runner-rust:1.78"
        assert profile.default_test_command == ("cargo", "test")

    def test_fallback_to_python_default(self, tmp_path: pathlib.Path) -> None:
        # Empty directory with no recognized markers
        profile = detect_project_stack(tmp_path)

        assert profile.kind == ProjectKind.PYTHON
        assert profile.default_image == "patchproof-runner-python:3.11"
        assert profile.default_test_command == ("pytest", "-q")
