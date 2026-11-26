"""TDD tests for pristine Git tree isolation, untracked files detection, and export."""

from __future__ import annotations

import io
import pathlib
import subprocess
import tarfile

import pytest

from patchproof.core.git_provider import (
    export_git_tree,
    get_uncommitted_diff,
    get_untracked_files,
)
from patchproof.exceptions import NoChangesDetectedError, SandboxSecurityError


@pytest.fixture
def git_repo_with_commits(tmp_path: pathlib.Path) -> pathlib.Path:
    """Create a minimal git repo with initial commit."""
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=str(repo), check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.name", "Tester"], cwd=str(repo), check=True, capture_output=True
    )
    subprocess.run(
        ["git", "config", "user.email", "tester@example.com"],
        cwd=str(repo),
        check=True,
        capture_output=True,
    )

    (repo / "calc.py").write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
    (repo / "test_calc.py").write_text(
        "from calc import add\ndef test_add():\n    assert add(1, 2) == 3\n", encoding="utf-8"
    )
    subprocess.run(["git", "add", "."], cwd=str(repo), check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "Initial commit"], cwd=str(repo), check=True, capture_output=True
    )
    return repo


class TestGitUntrackedFiles:
    """Verify detection and unified diff inclusion of untracked files."""

    def test_get_untracked_files_detects_new_file(
        self, git_repo_with_commits: pathlib.Path
    ) -> None:
        repo = git_repo_with_commits
        (repo / "new_helper.py").write_text("def helper(): pass\n", encoding="utf-8")

        untracked = get_untracked_files(repo)
        assert "new_helper.py" in untracked

    def test_get_uncommitted_diff_includes_untracked_files(
        self, git_repo_with_commits: pathlib.Path
    ) -> None:
        repo = git_repo_with_commits
        (repo / "untracked_feature.py").write_text("x = 42\n", encoding="utf-8")

        diff = get_uncommitted_diff(repo, include_untracked=True)
        assert "untracked_feature.py" in diff
        assert "--- /dev/null" in diff
        assert "+x = 42" in diff

    def test_get_uncommitted_diff_excludes_untracked_when_flag_false(
        self, git_repo_with_commits: pathlib.Path
    ) -> None:
        repo = git_repo_with_commits
        (repo / "untracked_feature.py").write_text("x = 42\n", encoding="utf-8")

        with pytest.raises(NoChangesDetectedError):
            get_uncommitted_diff(repo, include_untracked=False)


class TestExportGitTree:
    """Verify exporting pristine tree without host working directory contamination."""

    def test_export_git_tree_extracts_clean_head(
        self, git_repo_with_commits: pathlib.Path, tmp_path: pathlib.Path
    ) -> None:
        repo = git_repo_with_commits
        dest = tmp_path / "pristine_export"

        # Make dirty changes on host
        (repo / "calc.py").write_text("DIRTY CONTENT", encoding="utf-8")
        (repo / "untracked.py").write_text("UNTRACKED", encoding="utf-8")

        success = export_git_tree(repo, "HEAD", dest)
        assert success is True
        assert (dest / "calc.py").is_file()
        # Pristine export must have committed content, NOT dirty host content
        assert "def add(a, b):" in (dest / "calc.py").read_text(encoding="utf-8")
        # Untracked files must NOT be in pristine export
        assert not (dest / "untracked.py").exists()

    def test_export_git_tree_rejects_path_traversal(
        self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Simulate malicious tar archive with ../ traversal."""
        repo = tmp_path / "dummy_repo"
        repo.mkdir()
        dest = tmp_path / "dest"
        dest.mkdir()

        # Create malicious tar in memory
        tar_buf = io.BytesIO()
        with tarfile.open(fileobj=tar_buf, mode="w") as tar:
            ti = tarfile.TarInfo(name="../escape.txt")
            data = b"malicious"
            ti.size = len(data)
            tar.addfile(ti, io.BytesIO(data))

        malicious_bytes = tar_buf.getvalue()

        def fake_run(*args: object, **kwargs: object) -> subprocess.CompletedProcess[bytes]:
            return subprocess.CompletedProcess(args=[], returncode=0, stdout=malicious_bytes)

        monkeypatch.setattr(subprocess, "run", fake_run)

        with pytest.raises(SandboxSecurityError, match="Tar traversal detected"):
            export_git_tree(repo, "HEAD", dest)
