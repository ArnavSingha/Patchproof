"""Git provider for extracting uncommitted changes, HEAD diffs, and installing hooks."""

from __future__ import annotations

import io
import pathlib
import subprocess
import tarfile

from patchproof.exceptions import GitRepositoryError, NoChangesDetectedError, SandboxSecurityError

GIT_COMMAND_TIMEOUT_SECONDS = 15.0
MAX_UNTRACKED_FILE_BYTES = 5 * 1024 * 1024  # 5MB safety limit per untracked file


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


def get_untracked_files(repo_path: pathlib.Path | str) -> tuple[str, ...]:
    """Capture list of untracked files not ignored by .gitignore in working tree.

    Returns tuple of relative POSIX file paths.
    """
    resolved = pathlib.Path(repo_path).resolve()
    try:
        res = subprocess.run(
            ["git", "ls-files", "--others", "--exclude-standard"],
            cwd=str(resolved),
            capture_output=True,
            text=True,
            shell=False,
            close_fds=True,
            timeout=GIT_COMMAND_TIMEOUT_SECONDS,
        )
    except (subprocess.SubprocessError, OSError):
        return ()

    if res.returncode != 0:
        return ()

    files: list[str] = []
    for line in res.stdout.splitlines():
        cleaned = line.strip()
        if cleaned:
            files.append(cleaned.replace("\\", "/"))
    return tuple(files)


def export_git_tree(
    repo_path: pathlib.Path | str,
    ref: str,
    dest_dir: pathlib.Path | str,
) -> bool:
    """Export pristine git tree at ref into dest_dir without touching working copy.

    Guarantees baseline purity by extracting committed files directly from Git archive.
    Rejects and fails closed on any path traversal attacks inside tar archive members.

    Raises:
        SandboxSecurityError: If tar archive contains path traversal sequences.
    """
    resolved_repo = pathlib.Path(repo_path).resolve()
    dest_root = pathlib.Path(dest_dir).resolve()
    dest_root.mkdir(parents=True, exist_ok=True)

    try:
        res = subprocess.run(
            ["git", "archive", ref],
            cwd=str(resolved_repo),
            capture_output=True,
            shell=False,
            close_fds=True,
            timeout=GIT_COMMAND_TIMEOUT_SECONDS,
        )
    except (subprocess.SubprocessError, OSError):
        return False

    if res.returncode != 0 or not res.stdout:
        return False

    tar_bytes = res.stdout
    try:
        with tarfile.open(fileobj=io.BytesIO(tar_bytes), mode="r:*") as tar:
            for member in tar.getmembers():
                target_path = (dest_root / member.name).resolve()
                try:
                    target_path.relative_to(dest_root)
                except ValueError as err:
                    raise SandboxSecurityError(
                        f"Tar traversal detected in git archive member: '{member.name}' "
                        f"points outside destination root to '{target_path}'"
                    ) from err
            if hasattr(tarfile, "data_filter"):
                tar.extractall(dest_root, filter="data")
            else:
                tar.extractall(dest_root)
        return True
    except SandboxSecurityError:
        raise
    except Exception:
        return False


def get_uncommitted_diff(
    repo_path: pathlib.Path | str,
    include_untracked: bool = True,
) -> str:
    """Capture uncommitted changes (both staged and unstaged) in working tree.

    Optionally includes newly created untracked files as unified diff additions against /dev/null.

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

    if include_untracked:
        untracked = get_untracked_files(resolved)
        for rel_file in untracked:
            file_path = resolved / rel_file
            if file_path.is_file():
                try:
                    stat_res = file_path.stat()
                    if stat_res.st_size <= MAX_UNTRACKED_FILE_BYTES:
                        content = file_path.read_text(encoding="utf-8", errors="replace")
                        lines = content.splitlines(keepends=True)
                        line_count = len(lines)
                        diff_parts = [
                            f"diff --git a/{rel_file} b/{rel_file}",
                            "new file mode 100644",
                            "--- /dev/null",
                            f"+++ b/{rel_file}",
                            f"@@ -0,0 +1,{line_count} @@",
                        ]
                        for l_str in lines:
                            clean_line = l_str.rstrip("\r\n")
                            diff_parts.append(f"+{clean_line}")
                        diff_text += "\n" + "\n".join(diff_parts) + "\n"
                except (OSError, UnicodeDecodeError):
                    continue

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
