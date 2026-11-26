"""Unit tests for PatchProof built-in Model Context Protocol (MCP) server."""

from __future__ import annotations

import json
import pathlib
from unittest.mock import MagicMock, patch

from patchproof.mcp import (
    MCPServer,
    handle_doctor_tool,
    handle_verify_git_tool,
    handle_verify_patch_tool,
)
from patchproof.models.sandbox import (
    ComparativeExecutionResult,
    ExecutionResult,
    PatchVerdict,
    TestRunReport,
)


def make_mock_result(
    verdict: PatchVerdict = PatchVerdict.ACCEPTED,
    summary: str = "Verification passed",
) -> ComparativeExecutionResult:
    """Helper to create dummy execution result."""
    exec_res = ExecutionResult(
        exit_code=0,
        duration_seconds=1.0,
        test_report=TestRunReport(
            test_cases=(),
            exit_code=0,
            duration_seconds=1.0,
            total_collected=1,
        ),
    )
    return ComparativeExecutionResult(
        baseline_result=exec_res,
        patched_result=exec_res,
        verdict=verdict,
        summary=summary,
        introduced_regressions_count=0,
        resolved_defects_count=0,
        suppressed_tests_count=0,
        pre_existing_failures_count=0,
        test_comparisons=(),
    )


class TestMCPServerProtocol:
    """Test JSON-RPC 2.0 lifecycle and message handling."""

    def test_initialize_handshake(self) -> None:
        server = MCPServer()
        req = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {"protocolVersion": "2024-11-05"},
        }
        res = server.process_request(req)
        assert res is not None
        assert res["id"] == 1
        assert "result" in res
        assert res["result"]["serverInfo"]["name"] == "patchproof"
        assert "tools" in res["result"]["capabilities"]

    def test_tools_list(self) -> None:
        server = MCPServer()
        req = {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/list",
            "params": {},
        }
        res = server.process_request(req)
        assert res is not None
        assert res["id"] == 2
        tools = res["result"]["tools"]
        tool_names = [t["name"] for t in tools]
        assert "patchproof_verify_patch" in tool_names
        assert "patchproof_verify_git" in tool_names
        assert "patchproof_doctor" in tool_names

    def test_unknown_method_returns_error(self) -> None:
        server = MCPServer()
        req = {
            "jsonrpc": "2.0",
            "id": 99,
            "method": "nonexistent/method",
            "params": {},
        }
        res = server.process_request(req)
        assert res is not None
        assert res["id"] == 99
        assert "error" in res
        assert res["error"]["code"] == -32601


class TestMCPTools:
    """Test individual MCP tool handlers."""

    @patch("patchproof.mcp.ComparativeRunner")
    def test_verify_patch_tool_success(
        self, mock_runner_cls: MagicMock, tmp_path: pathlib.Path
    ) -> None:
        mock_runner = MagicMock()
        mock_runner.run.return_value = make_mock_result(verdict=PatchVerdict.ACCEPTED)
        mock_runner_cls.return_value = mock_runner

        args = {
            "patch": "--- a/x.py\n+++ b/x.py\n@@ -1 +1 @@\n-1\n+2\n",
            "repo": str(tmp_path),
            "test_command": "pytest -q",
        }
        res = handle_verify_patch_tool(args)
        assert "content" in res
        data = json.loads(res["content"][0]["text"])
        assert data["verdict"] == "accepted"
        assert "ai_feedback" in data
        assert "proof_certificate" in data

    @patch("patchproof.mcp.get_uncommitted_diff")
    @patch("patchproof.mcp.ComparativeRunner")
    def test_verify_git_tool_uncommitted(
        self,
        mock_runner_cls: MagicMock,
        mock_get_diff: MagicMock,
        tmp_path: pathlib.Path,
    ) -> None:
        mock_get_diff.return_value = "--- a/y.py\n+++ b/y.py\n@@ -1 +1 @@\n-a\n+b\n"
        mock_runner = MagicMock()
        mock_runner.run.return_value = make_mock_result(verdict=PatchVerdict.ACCEPTED)
        mock_runner_cls.return_value = mock_runner

        args = {"repo": str(tmp_path), "target": "uncommitted"}
        res = handle_verify_git_tool(args)
        assert "content" in res
        data = json.loads(res["content"][0]["text"])
        assert data["verdict"] == "accepted"
        assert "ai_feedback" in data
        assert "proof_certificate" in data

    @patch("patchproof.mcp.SystemDoctor")
    def test_doctor_tool(self, mock_doctor_cls: MagicMock) -> None:
        mock_doc = MagicMock()
        mock_check = MagicMock()
        mock_check.passed = True
        mock_check.name = "Docker CLI"
        mock_check.details = "Available"
        mock_check.remediation = ""
        mock_doc.run_all_checks.return_value = [mock_check]
        mock_doctor_cls.return_value = mock_doc

        res = handle_doctor_tool({})
        assert "content" in res
        data = json.loads(res["content"][0]["text"])
        assert data["all_passed"] is True
