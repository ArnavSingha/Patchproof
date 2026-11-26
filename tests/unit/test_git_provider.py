"""Unit tests for Git provider and diff extraction."""

from __future__ import annotations

import pathlib
import subprocess
from unittest.mock import MagicMock, patch

import pytest

from patchproof.core.git_provider import (
    get_head_diff,
    get_uncommitted_diff,
    install_pre_commit_hook,
    is_git_repository,
)
from patchproof.exceptions import GitRepositoryError, NoChangesDetectedError


class TestIsGitRepository:
    """Test git repository detection."""

    @patch("subprocess.run")
    def test_returns_true_for_git_repo(self, mock_run: MagicMock, tmp_path: pathlib.Path) -> None:
        mock_run.return_value = subprocess.CompletedProcess(
            args=["git", "rev-parse", "--is-inside-work-tree"],
            returncode=0,
            stdout="true\n",
            stderr="",
        )
        assert is_git_repository(tmp_path) is True
        mock_run.assert_called_once()
        assert mock_run.call_args[1]["shell"] is False
        assert mock_run.call_args[1]["close_fds"] is True

    @patch("subprocess.run")
    def test_returns_false_for_non_git_repo(
        self, mock_run: MagicMock, tmp_path: pathlib.Path
    ) -> None:
        mock_run.return_value = subprocess.CompletedProcess(
            args=["git", "rev-parse", "--is-inside-work-tree"],
            returncode=128,
            stdout="",
            stderr="fatal: not a git repository",
        )
        assert is_git_repository(tmp_path) is False

    @patch("subprocess.run", side_effect=FileNotFoundError("git binary not found"))
    def test_returns_false_when_git_missing(
        self, mock_run: MagicMock, tmp_path: pathlib.Path
    ) -> None:
        assert is_git_repository(tmp_path) is False


class TestGetUncommittedDiff:
    """Test extracting uncommitted working tree changes."""

    @patch("subprocess.run")
    def test_returns_diff_when_uncommitted_changes_exist(
        self, mock_run: MagicMock, tmp_path: pathlib.Path
    ) -> None:
        sample_diff = (
            "diff --git a/foo.py b/foo.py\n--- a/foo.py\n+++ b/foo.py\n@@ -1 +1 @@\n-a\n+b\n"
        )
        mock_run.side_effect = [
            # 1. is_git_repository check
            subprocess.CompletedProcess(args=[], returncode=0, stdout="true\n", stderr=""),
            # 2. git diff HEAD
            subprocess.CompletedProcess(args=[], returncode=0, stdout=sample_diff, stderr=""),
        ]

        diff = get_uncommitted_diff(tmp_path)
        assert diff == sample_diff

    @patch("subprocess.run")
    def test_raises_no_changes_when_diff_empty(
        self, mock_run: MagicMock, tmp_path: pathlib.Path
    ) -> None:
        mock_run.side_effect = [
            subprocess.CompletedProcess(args=[], returncode=0, stdout="true\n", stderr=""),
            # Empty diff
            subprocess.CompletedProcess(args=[], returncode=0, stdout="  \n", stderr=""),
        ]

        with pytest.raises(NoChangesDetectedError, match="No uncommitted changes detected"):
            get_uncommitted_diff(tmp_path)

    @patch("subprocess.run")
    def test_raises_git_error_when_not_git_repo(
        self, mock_run: MagicMock, tmp_path: pathlib.Path
    ) -> None:
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=128, stdout="", stderr="fatal: not a git repo"
        )
        with pytest.raises(GitRepositoryError, match="not a Git repository"):
            get_uncommitted_diff(tmp_path)


class TestGetHeadDiff:
    """Test extracting changes from latest HEAD commit."""

    @patch("subprocess.run")
    def test_returns_head_diff_when_commits_exist(
        self, mock_run: MagicMock, tmp_path: pathlib.Path
    ) -> None:
        sample_diff = (
            "diff --git a/bar.py b/bar.py\n--- a/bar.py\n+++ b/bar.py\n@@ -1 +1 @@\n-x\n+y\n"
        )
        mock_run.side_effect = [
            # 1. is_git_repository
            subprocess.CompletedProcess(args=[], returncode=0, stdout="true\n", stderr=""),
            # 2. git show --format= HEAD
            subprocess.CompletedProcess(args=[], returncode=0, stdout=sample_diff, stderr=""),
        ]

        diff = get_head_diff(tmp_path)
        assert diff == sample_diff

    @patch("subprocess.run")
    def test_raises_git_error_when_no_commits(
        self, mock_run: MagicMock, tmp_path: pathlib.Path
    ) -> None:
        mock_run.side_effect = [
            subprocess.CompletedProcess(args=[], returncode=0, stdout="true\n", stderr=""),
            subprocess.CompletedProcess(
                args=[], returncode=128, stdout="", stderr="fatal: bad default revision 'HEAD'"
            ),
        ]
        with pytest.raises(GitRepositoryError, match="Failed to retrieve HEAD diff"):
            get_head_diff(tmp_path)


class TestInstallPreCommitHook:
    """Test installing Git pre-commit verification hook."""

    def test_installs_pre_commit_hook(self, tmp_path: pathlib.Path) -> None:
        git_dir = tmp_path / ".git"
        git_dir.mkdir()

        hook_path = install_pre_commit_hook(tmp_path)
        assert hook_path.is_file()
        assert hook_path.name == "pre-commit"

        content = hook_path.read_text(encoding="utf-8")
        assert "patchproof verify --git" in content

    def test_raises_error_if_not_git_repo(self, tmp_path: pathlib.Path) -> None:
        with pytest.raises(GitRepositoryError, match=r"Not a Git repository"):
            install_pre_commit_hook(tmp_path)
