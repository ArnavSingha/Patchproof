"""Configuration initializers for AI coding agents (Aider, Cursor, Claude Code).

Enables one-command setup for agents to automatically use PatchProof.
"""

from __future__ import annotations

import json
import logging
import pathlib

logger = logging.getLogger(__name__)

AIDER_CONFIG_CONTENT = """# Aider configuration with PatchProof deterministic verification
test-cmd: "patchproof verify --git"
auto-test: true
"""

CURSORRULES_CONTENT = """# PatchProof Invariant Rules for AI Assistance
Whenever modifying code or fixing bugs:
1. Always run `patchproof verify --git` or call the MCP tool `patchproof_verify_git`.
2. If verification returns REJECTED, inspect the `ai_feedback` diagnostics.
3. Fix the regression without modifying existing tests or weakening assertions.
"""

CLAUDE_MD_CONTENT = """# AI Assistant Engineering Rules
- **Patch Verification**: Before finalizing any code changes or bug fixes, always verify:
  `patchproof verify --git`
- If verification fails with regressions, review diagnostic output and fix the root cause.
- Never weaken, comment out, or delete existing tests to force a pass.
"""


def _safe_write_file(path: pathlib.Path, content: str, force: bool = False) -> bool:
    """Safely write content to file, creating parent directories. Returns True if written."""
    if path.is_file() and not force:
        logger.info("File already exists, skipping: %s", path)
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return True


def init_aider_config(repo_path: pathlib.Path, force: bool = False) -> list[pathlib.Path]:
    """Generate .aider.conf.yml configured with PatchProof test runner."""
    created: list[pathlib.Path] = []
    config_file = repo_path / ".aider.conf.yml"
    if _safe_write_file(config_file, AIDER_CONFIG_CONTENT, force=force):
        created.append(config_file)
    return created


def init_cursor_config(repo_path: pathlib.Path, force: bool = False) -> list[pathlib.Path]:
    """Generate .cursor/mcp.json and .cursorrules for Cursor IDE."""
    created: list[pathlib.Path] = []
    mcp_file = repo_path / ".cursor" / "mcp.json"
    mcp_data = {
        "mcpServers": {
            "patchproof": {
                "command": "python",
                "args": ["-m", "patchproof", "mcp"],
            }
        }
    }
    if _safe_write_file(mcp_file, json.dumps(mcp_data, indent=2) + "\n", force=force):
        created.append(mcp_file)

    rules_file = repo_path / ".cursorrules"
    if _safe_write_file(rules_file, CURSORRULES_CONTENT, force=force):
        created.append(rules_file)

    return created


def init_claude_config(repo_path: pathlib.Path, force: bool = False) -> list[pathlib.Path]:
    """Generate .mcp.json and CLAUDE.md for Claude Code."""
    created: list[pathlib.Path] = []
    mcp_file = repo_path / ".mcp.json"
    mcp_data = {
        "mcpServers": {
            "patchproof": {
                "command": "python",
                "args": ["-m", "patchproof", "mcp"],
            }
        }
    }
    if _safe_write_file(mcp_file, json.dumps(mcp_data, indent=2) + "\n", force=force):
        created.append(mcp_file)

    claude_file = repo_path / "CLAUDE.md"
    if _safe_write_file(claude_file, CLAUDE_MD_CONTENT, force=force):
        created.append(claude_file)

    return created


def init_all_agents(repo_path: pathlib.Path, force: bool = False) -> list[pathlib.Path]:
    """Generate configuration for all supported AI agents (Aider, Cursor, Claude Code)."""
    results: list[pathlib.Path] = []
    results.extend(init_aider_config(repo_path, force=force))
    results.extend(init_cursor_config(repo_path, force=force))
    results.extend(init_claude_config(repo_path, force=force))
    return results
