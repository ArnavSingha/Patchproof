"""Tests for concurrent staging and sparse tree integration in comparative execution."""

import pathlib
from unittest.mock import MagicMock, patch

import pytest

from patchproof.models.sandbox import (
    ContainerConfig,
    ExecutionResult,
    PatchVerdict,
)
from patchproof.sandbox.comparative import ComparativeRunner


@pytest.fixture
def dummy_config(tmp_path: pathlib.Path) -> ContainerConfig:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "test_dummy.py").write_text("def test_ok(): assert True\n", encoding="utf-8")
    return ContainerConfig(
        image="python:3.11-slim",
        host_repo_path=str(repo),
        entrypoint_command=("pytest", "-q"),
    )


def test_concurrent_staging_git_repo(
    dummy_config: ContainerConfig, tmp_path: pathlib.Path
) -> None:
    """Verify that export_git_tree can run concurrently for baseline and patched workspaces."""
    mock_runner = MagicMock()
    mock_runner.run.return_value = ExecutionResult(
        exit_code=0,
        stdout="1 passed in 0.05s",
        stderr="",
        duration_seconds=0.1,
    )

    engine = ComparativeRunner(runner=mock_runner)

    patch_str = (
        "diff --git a/test_dummy.py b/test_dummy.py\n"
        "--- a/test_dummy.py\n"
        "+++ b/test_dummy.py\n"
        "@@ -1 +1 @@\n"
        "-def test_ok(): assert True\n"
        "+def test_ok(): assert 1 == 1\n"
    )

    with (
        patch("patchproof.sandbox.comparative.is_git_repository", return_value=True),
        patch("patchproof.sandbox.comparative.export_git_tree", return_value=True) as mock_export,
    ):
        result = engine.run(
            patch=patch_str,
            config=dummy_config,
        )

        assert mock_export.call_count == 2
        assert result.verdict in (PatchVerdict.ACCEPTED, PatchVerdict.REJECTED)


def test_sparse_staging_when_common_ancestor_detected(dummy_config: ContainerConfig) -> None:
    """Verify that when patch modified files share a common ancestor, subtree is passed."""
    mock_runner = MagicMock()
    mock_runner.run.return_value = ExecutionResult(
        exit_code=0,
        stdout="1 passed in 0.05s",
        stderr="",
        duration_seconds=0.1,
    )

    engine = ComparativeRunner(runner=mock_runner)

    patch_str = (
        "diff --git a/services/auth/models.py b/services/auth/models.py\n"
        "--- a/services/auth/models.py\n"
        "+++ b/services/auth/models.py\n"
        "@@ -1 +1 @@\n"
        "-x = 1\n"
        "+x = 2\n"
    )

    with patch("patchproof.sandbox.comparative.is_git_repository", return_value=True), \
         patch("patchproof.sandbox.comparative.export_git_tree", return_value=True) as mock_export:

        engine.run(
            patch=patch_str,
            config=dummy_config,
        )

        assert mock_export.call_count == 2
        # Check that subtree was passed as 'services/auth'
        for call_args in mock_export.call_args_list:
            assert call_args.kwargs.get("subtree") == "services/auth"
