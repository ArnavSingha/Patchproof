"""Unit tests for AI Agent configuration initializers (Aider, Cursor, Claude Code)."""

from __future__ import annotations

import json
import pathlib

from patchproof.core.agent_init import (
    init_aider_config,
    init_all_agents,
    init_claude_config,
    init_cursor_config,
)


def test_init_aider_config(tmp_path: pathlib.Path) -> None:
    """Test generating .aider.conf.yml in target repository."""
    created = init_aider_config(tmp_path)
    assert len(created) == 1
    config_file = tmp_path / ".aider.conf.yml"
    assert config_file.is_file()
    content = config_file.read_text(encoding="utf-8")
    assert 'test-cmd: "patchproof verify --git"' in content

    # Calling without force should not overwrite existing
    created_again = init_aider_config(tmp_path, force=False)
    assert len(created_again) == 0

    # Calling with force should succeed
    created_forced = init_aider_config(tmp_path, force=True)
    assert len(created_forced) == 1


def test_init_cursor_config(tmp_path: pathlib.Path) -> None:
    """Test generating .cursor/mcp.json and .cursorrules."""
    created = init_cursor_config(tmp_path)
    assert len(created) == 2

    mcp_file = tmp_path / ".cursor" / "mcp.json"
    assert mcp_file.is_file()
    mcp_data = json.loads(mcp_file.read_text(encoding="utf-8"))
    assert "patchproof" in mcp_data["mcpServers"]
    assert "patchproof" in mcp_data["mcpServers"]["patchproof"]["args"]

    rules_file = tmp_path / ".cursorrules"
    assert rules_file.is_file()
    assert "PatchProof" in rules_file.read_text(encoding="utf-8")


def test_init_claude_config(tmp_path: pathlib.Path) -> None:
    """Test generating Claude Code configuration and instructions."""
    created = init_claude_config(tmp_path)
    assert len(created) == 2

    claude_mcp = tmp_path / ".mcp.json"
    assert claude_mcp.is_file()
    mcp_data = json.loads(claude_mcp.read_text(encoding="utf-8"))
    assert "patchproof" in mcp_data["mcpServers"]

    claude_doc = tmp_path / "CLAUDE.md"
    assert claude_doc.is_file()
    assert "patchproof verify" in claude_doc.read_text(encoding="utf-8")


def test_init_all_agents(tmp_path: pathlib.Path) -> None:
    """Test initializing all AI agent configurations at once."""
    created = init_all_agents(tmp_path)
    assert len(created) >= 5
    assert (tmp_path / ".aider.conf.yml").is_file()
    assert (tmp_path / ".cursor" / "mcp.json").is_file()
    assert (tmp_path / ".mcp.json").is_file()
