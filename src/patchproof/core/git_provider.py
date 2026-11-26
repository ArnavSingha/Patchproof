"""Git provider for extracting uncommitted changes, HEAD diffs, and installing hooks."""

from __future__ import annotations

import pathlib
import subprocess

from patchproof.exceptions import GitRepositoryError, NoChangesDetectedError

GIT_COMMAND_TIMEOUT_SECONDS = 15.0


def is_git_repository(repo_path: pathlib.Path | str) -> bool:
    """Check if target directory is inside an active Git worktree.

    Never executes shell=True; handles missing git binary gracefully.
    """
    resolved = pathlib.Path(repo_path).resolve()
    try:
        res = subprocess.run(
            ["git", "rev-parse", "--is-inside-work-tree"],
            cwd=str(resolved),
            capture_output=True,
            text=True,
            shell=False,
            close_fds=True,
            timeout=GIT_COMMAND_TIMEOUT_SECONDS,
        )
        return res.returncode == 0 and res.stdout.strip() == "true"
    except (subprocess.SubprocessError, OSError):
        return False


def get_uncommitted_diff(repo_path: pathlib.Path | str) -> str:
    """Capture uncommitted changes (both staged and unstaged) in working tree.

    Raises:
        GitRepositoryError: If path is not a git repository.
        NoChangesDetectedError: If working tree is clean.
    """
    resolved = pathlib.Path(repo_path).resolve()
    if not is_git_repository(resolved):
        raise GitRepositoryError(f"Target path is not a Git repository: {resolved}")

    # First attempt: git diff HEAD (captures both staged and unstaged vs last commit)
    res = subprocess.run(
        ["git", "diff", "HEAD"],
        cwd=str(resolved),
        capture_output=True,
        text=True,
        shell=False,
        close_fds=True,
        timeout=GIT_COMMAND_TIMEOUT_SECONDS,
    )

    # Fallback to plain git diff if HEAD does not exist (new repository before first commit)
    if res.returncode != 0:
        res = subprocess.run(
            ["git", "diff"],
            cwd=str(resolved),
            capture_output=True,
            text=True,
            shell=False,
            close_fds=True,
            timeout=GIT_COMMAND_TIMEOUT_SECONDS,
        )

    if res.returncode != 0:
        err_msg = res.stderr.strip() or f"exit code {res.returncode}"
        raise GitRepositoryError(f"Failed to capture git diff: {err_msg}")

    diff_text = res.stdout
    if not diff_text or not diff_text.strip():
        raise NoChangesDetectedError(f"No uncommitted changes detected in repository: {resolved}")

    return diff_text


def get_head_diff(repo_path: pathlib.Path | str) -> str:
    """Capture changes introduced by the most recent HEAD commit.

    Raises:
        GitRepositoryError: If path is not a git repository or no commits exist.
        NoChangesDetectedError: If HEAD commit has no diff.
    """
    resolved = pathlib.Path(repo_path).resolve()
    if not is_git_repository(resolved):
        raise GitRepositoryError(f"Target path is not a Git repository: {resolved}")

    res = subprocess.run(
        ["git", "show", "--format=", "HEAD"],
        cwd=str(resolved),
        capture_output=True,
        text=True,
        shell=False,
        close_fds=True,
        timeout=GIT_COMMAND_TIMEOUT_SECONDS,
    )

    if res.returncode != 0:
        err_msg = res.stderr.strip() or f"exit code {res.returncode}"
        raise GitRepositoryError(f"Failed to retrieve HEAD diff: {err_msg}")

    diff_text = res.stdout
    if not diff_text or not diff_text.strip():
        raise NoChangesDetectedError(
            f"No changes detected in HEAD commit for repository: {resolved}"
        )

    return diff_text


def install_pre_commit_hook(repo_path: pathlib.Path | str) -> pathlib.Path:
    """Install .git/hooks/pre-commit verification hook in target repository.

    Supports standard Git repositories, Git worktrees, and submodules.

    Raises:
        GitRepositoryError: If repo_path is not a valid Git repository.
    """
    resolved = pathlib.Path(repo_path).resolve()
    git_entry = resolved / ".git"
    if not git_entry.exists() and not is_git_repository(resolved):
        raise GitRepositoryError(f"Not a Git repository: {resolved}")

    # Use git rev-parse to accurately resolve hooks path
    # across standard repos, worktrees, and submodules
    hooks_rel_path: str | None = None
    try:
        res = subprocess.run(
            ["git", "rev-parse", "--git-path", "hooks"],
            cwd=resolved,
            capture_output=True,
            text=True,
            shell=False,
            close_fds=True,
            timeout=10,
        )
        if res.returncode == 0 and res.stdout.strip():
            hooks_rel_path = res.stdout.strip()
    except Exception:
        pass

    if hooks_rel_path:
        hooks_dir = (resolved / hooks_rel_path).resolve()
    else:
        # Fallback to standard .git/hooks directory
        base_git = git_entry if git_entry.is_dir() else (resolved / ".git")
        hooks_dir = base_git / "hooks"

    hooks_dir.mkdir(parents=True, exist_ok=True)

    hook_file = hooks_dir / "pre-commit"
    hook_script = (
        "#!/bin/sh\n"
        "# PatchProof Pre-Commit Verification Hook\n"
        "# Automatically verifies uncommitted changes in isolated sandbox\n"
        "set -e\n"
        "patchproof verify --git\n"
    )

    hook_file.write_text(hook_script, encoding="utf-8")

    # On POSIX systems, ensure executable permission
    try:
        current_mode = hook_file.stat().st_mode
        hook_file.chmod(current_mode | 0o755)
    except OSError:
        pass

    return hook_file
