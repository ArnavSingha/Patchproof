"""Built-in Model Context Protocol (MCP) server for PatchProof.

Exposes deterministic code patch verification to AI agents (Antigravity, Cursor, Claude Code).
Runs over standard I/O using JSON-RPC 2.0.
"""

from __future__ import annotations

import json
import logging
import pathlib
import sys
from typing import Any

from patchproof.core.git_provider import get_head_diff, get_uncommitted_diff
from patchproof.core.normalizer import parse_patch
from patchproof.core.slicer import slice_test_suite
from patchproof.exceptions import (
    GitRepositoryError,
    NoChangesDetectedError,
    SandboxSecurityError,
)
from patchproof.models.patch import CanonicalPatch
from patchproof.models.sandbox import (
    ComparativeExecutionResult,
    ContainerConfig,
    PatchVerdict,
    ResourceLimits,
)
from patchproof.reporting.certificate import generate_certificate
from patchproof.reporting.diagnostic import generate_ai_feedback
from patchproof.sandbox.comparative import ComparativeRunner
from patchproof.sandbox.docker_cli import parse_test_command
from patchproof.sandbox.doctor import SystemDoctor
from patchproof.sandbox.setup_runner import DEFAULT_RUNNER_TAG

logger = logging.getLogger(__name__)

PROTOCOL_VERSION = "2024-11-05"

TOOLS_DEFINITIONS: list[dict[str, Any]] = [
    {
        "name": "patchproof_verify_patch",
        "description": (
            "Verify a code patch (unified diff or SEARCH/REPLACE format) against the test suite "
            "in an isolated, zero-network Docker sandbox. Returns baseline vs patched test results "
            "and regression classification (ACCEPTED / REJECTED)."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "patch": {
                    "type": "string",
                    "description": "Unified diff or SEARCH/REPLACE patch content to verify.",
                },
                "repo": {
                    "type": "string",
                    "description": "Path to target repository on host (default: .).",
                    "default": ".",
                },
                "test_command": {
                    "type": "string",
                    "description": "Custom test execution command (e.g. 'pytest tests/unit/ -q').",
                },
                "timeout": {
                    "type": "number",
                    "description": "Per-run execution timeout in seconds (default: 15.0).",
                    "default": 15.0,
                },
                "image": {
                    "type": "string",
                    "description": f"Runner Docker image (default: {DEFAULT_RUNNER_TAG}).",
                    "default": DEFAULT_RUNNER_TAG,
                },
            },
            "required": ["patch"],
        },
    },
    {
        "name": "patchproof_verify_git",
        "description": (
            "Verify uncommitted working tree changes or latest HEAD commit in a Git repository "
            "in an isolated Docker sandbox. Returns comparative regression analysis."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "repo": {
                    "type": "string",
                    "description": "Path to target Git repository (default: current directory).",
                    "default": ".",
                },
                "target": {
                    "type": "string",
                    "enum": ["uncommitted", "head"],
                    "description": (
                        "What to verify: 'uncommitted' (working tree changes) or 'head' "
                        "(latest commit). Default: 'uncommitted'."
                    ),
                    "default": "uncommitted",
                },
                "test_command": {
                    "type": "string",
                    "description": "Custom test execution command (e.g. 'pytest tests/unit/ -q').",
                },
                "timeout": {
                    "type": "number",
                    "description": "Per-run execution timeout in seconds (default: 15.0).",
                    "default": 15.0,
                },
            },
        },
    },
    {
        "name": "patchproof_doctor",
        "description": (
            "Diagnose host system prerequisites (Python version, Docker daemon availability, "
            "Linux container mode, runner image readiness) and return actionable recovery guidance."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {},
        },
    },
]


def _format_result_json(result: ComparativeExecutionResult, patch_content: str = "") -> str:
    """Format comparative result as machine-readable JSON with AI feedback diagnostics."""
    canonical_patch: CanonicalPatch | None = None
    if patch_content:
        try:
            canonical_patch = parse_patch(patch_content)
        except Exception:
            pass

    ai_report = generate_ai_feedback(result, patch=canonical_patch)

    report = {
        "verdict": result.verdict.value,
        "summary": result.summary,
        "ai_feedback": ai_report.to_dict(),
        "counts": {
            "introduced_regressions": result.introduced_regressions_count,
            "flaky_tests": result.flaky_tests_count,
            "resolved_defects": result.resolved_defects_count,
            "suppressed_tests": result.suppressed_tests_count,
            "pre_existing_failures": result.pre_existing_failures_count,
        },
        "baseline": {
            "exit_code": result.baseline_result.exit_code,
            "duration_seconds": result.baseline_result.duration_seconds,
            "failure_reason": result.baseline_result.failure_reason.value,
        },
        "patched": {
            "exit_code": result.patched_result.exit_code,
            "duration_seconds": result.patched_result.duration_seconds,
            "failure_reason": result.patched_result.failure_reason.value,
        },
        "comparisons": [
            {
                "test_name": c.test_name,
                "baseline_outcome": (c.baseline_outcome.value if c.baseline_outcome else None),
                "patched_outcome": (c.patched_outcome.value if c.patched_outcome else None),
                "classification": c.classification.value,
            }
            for c in result.test_comparisons
        ],
    }
    if result.verdict == PatchVerdict.ACCEPTED:
        try:
            cert = generate_certificate(result, patch_content=patch_content)
            report["proof_certificate"] = cert.to_dict()
        except Exception:
            pass
    return json.dumps(report, indent=2)


def handle_verify_patch_tool(arguments: dict[str, Any]) -> dict[str, Any]:
    """Execute patchproof_verify_patch tool."""
    patch_content = arguments.get("patch", "")
    repo = arguments.get("repo", ".")
    image = arguments.get("image", DEFAULT_RUNNER_TAG)
    timeout = float(arguments.get("timeout", 15.0))
    test_cmd_str = arguments.get("test_command")
    slice_tests = bool(arguments.get("slice", False))
    disambiguate_flakes = bool(arguments.get("disambiguate_flakes", False))

    entrypoint_cmd: tuple[str, ...] = ("pytest", "-q")
    if test_cmd_str:
        try:
            entrypoint_cmd = parse_test_command(test_cmd_str)
        except SandboxSecurityError as err:
            return {
                "isError": True,
                "content": [{"type": "text", "text": f"Invalid test command: {err}"}],
            }
    elif slice_tests:
        try:
            can_p = parse_patch(patch_content)
            slice_res = slice_test_suite(pathlib.Path(repo).resolve(), can_p)
            if not slice_res.is_full_suite_fallback and slice_res.selected_test_paths:
                entrypoint_cmd = ("pytest", "-q", *slice_res.selected_test_paths)
        except Exception:
            pass

    repo_path = pathlib.Path(repo).resolve()
    if not repo_path.is_dir():
        return {
            "isError": True,
            "content": [{"type": "text", "text": f"Repository directory not found: {repo}"}],
        }

    try:
        config = ContainerConfig(
            host_repo_path=str(repo_path),
            image=image,
            resources=ResourceLimits(timeout_seconds=timeout),
            entrypoint_command=entrypoint_cmd,
        )
    except Exception as err:
        return {
            "isError": True,
            "content": [{"type": "text", "text": f"Configuration error: {err}"}],
        }

    runner = ComparativeRunner()
    try:
        res = runner.run(
            config,
            patch_content,
            disambiguate_flakes=disambiguate_flakes,
        )
        return {
            "content": [
                {
                    "type": "text",
                    "text": _format_result_json(res, patch_content=patch_content),
                }
            ],
        }
    except Exception as err:
        return {
            "isError": True,
            "content": [{"type": "text", "text": f"Execution error: {err}"}],
        }


def handle_verify_git_tool(arguments: dict[str, Any]) -> dict[str, Any]:
    """Execute patchproof_verify_git tool."""
    repo = arguments.get("repo", ".")
    target = arguments.get("target", "uncommitted")
    repo_path = pathlib.Path(repo).resolve()

    if not repo_path.is_dir():
        return {
            "isError": True,
            "content": [{"type": "text", "text": f"Repository directory not found: {repo}"}],
        }

    try:
        if target == "head":
            patch_content = get_head_diff(repo_path)
        else:
            patch_content = get_uncommitted_diff(repo_path)
    except NoChangesDetectedError:
        clean_msg = {"verdict": "accepted", "summary": "Working tree is clean; no changes."}
        return {
            "content": [
                {
                    "type": "text",
                    "text": json.dumps(clean_msg, indent=2),
                }
            ],
        }
    except GitRepositoryError as err:
        return {
            "isError": True,
            "content": [{"type": "text", "text": f"Git error: {err}"}],
        }

    # Delegate to verify_patch
    forwarded_args = dict(arguments)
    forwarded_args["patch"] = patch_content
    return handle_verify_patch_tool(forwarded_args)


def handle_doctor_tool(arguments: dict[str, Any]) -> dict[str, Any]:
    """Execute patchproof_doctor tool."""
    doctor = SystemDoctor(timeout_seconds=5.0)
    results = doctor.run_all_checks()
    all_passed = all(r.passed for r in results)

    checks_data = [
        {
            "name": r.name,
            "passed": r.passed,
            "details": r.details,
            "remediation": r.remediation,
        }
        for r in results
    ]

    report = {
        "all_passed": all_passed,
        "checks": checks_data,
    }
    return {
        "content": [{"type": "text", "text": json.dumps(report, indent=2)}],
    }


class MCPServer:
    """Zero-dependency JSON-RPC 2.0 Model Context Protocol (MCP) server."""

    def __init__(self) -> None:
        self.handlers = {
            "patchproof_verify_patch": handle_verify_patch_tool,
            "patchproof_verify_git": handle_verify_git_tool,
            "patchproof_doctor": handle_doctor_tool,
        }

    def process_request(self, req: dict[str, Any]) -> dict[str, Any] | None:
        """Process incoming JSON-RPC request and return response dict (or None)."""
        method = req.get("method")
        msg_id = req.get("id")

        if method == "notifications/initialized":
            return None

        if method == "initialize":
            return {
                "jsonrpc": "2.0",
                "id": msg_id,
                "result": {
                    "protocolVersion": PROTOCOL_VERSION,
                    "capabilities": {
                        "tools": {},
                    },
                    "serverInfo": {
                        "name": "patchproof",
                        "version": "0.1.0",
                    },
                },
            }

        if method == "ping":
            return {
                "jsonrpc": "2.0",
                "id": msg_id,
                "result": {},
            }

        if method == "tools/list":
            return {
                "jsonrpc": "2.0",
                "id": msg_id,
                "result": {
                    "tools": TOOLS_DEFINITIONS,
                },
            }

        if method == "tools/call":
            params = req.get("params", {})
            tool_name = params.get("name")
            tool_args = params.get("arguments", {})

            handler = self.handlers.get(tool_name)
            if not handler:
                return {
                    "jsonrpc": "2.0",
                    "id": msg_id,
                    "error": {
                        "code": -32601,
                        "message": f"Tool '{tool_name}' not found",
                    },
                }

            try:
                res = handler(tool_args)
                return {
                    "jsonrpc": "2.0",
                    "id": msg_id,
                    "result": res,
                }
            except Exception as err:
                return {
                    "jsonrpc": "2.0",
                    "id": msg_id,
                    "result": {
                        "isError": True,
                        "content": [{"type": "text", "text": f"Tool error: {err}"}],
                    },
                }

        # Unknown method
        return {
            "jsonrpc": "2.0",
            "id": msg_id,
            "error": {
                "code": -32601,
                "message": f"Method '{method}' not found",
            },
        }

    def run_stdio(self) -> None:
        """Run JSON-RPC loop reading line-delimited messages from sys.stdin."""
        for line in sys.stdin:
            line = line.strip()
            if not line:
                continue
            try:
                req = json.loads(line)
                res = self.process_request(req)
                if res is not None:
                    sys.stdout.write(json.dumps(res) + "\n")
                    sys.stdout.flush()
            except json.JSONDecodeError as err:
                err_res = {
                    "jsonrpc": "2.0",
                    "id": None,
                    "error": {"code": -32700, "message": f"Parse error: {err}"},
                }
                sys.stdout.write(json.dumps(err_res) + "\n")
                sys.stdout.flush()
