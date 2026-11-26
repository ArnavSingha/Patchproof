"""TDD tests for patchproof_heal MCP tool."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from patchproof.mcp import TOOLS_DEFINITIONS, handle_heal_tool


class TestMCPHealTool:
    """Verify MCP registration and invocation of autonomous patch healer."""

    def test_heal_tool_registered_in_tool_definitions(self) -> None:
        names = [t["name"] for t in TOOLS_DEFINITIONS]
        assert "patchproof_heal" in names

    @patch("patchproof.mcp.AutonomousHealer")
    def test_handle_heal_tool_invokes_autonomous_healer(self, mock_healer_cls: MagicMock) -> None:
        mock_instance = MagicMock()
        mock_healer_cls.return_value = mock_instance

        mock_instance.heal.return_value = MagicMock(
            success=True,
            iterations=1,
            final_patch="fixed_patch_content",
            summary="Autonomous healing succeeded: patch accepted.",
        )

        res = handle_heal_tool({
            "patch": "diff --git a/a.py b/a.py\n",
            "repo": ".",
            "max_iterations": 2,
        })

        assert res.get("isError") is not True
        content = res["content"][0]["text"]
        assert "Autonomous healing succeeded" in content
