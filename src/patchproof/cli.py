"""Unified Command-Line Interface for PatchProof."""

from __future__ import annotations

import argparse
import json
import logging
import pathlib
import sys
from collections.abc import Sequence
from enum import IntEnum
from typing import NoReturn

from patchproof.core.agent_init import (
    init_aider_config,
    init_all_agents,
    init_claude_config,
    init_cursor_config,
)
from patchproof.core.detector import detect_project_stack
from patchproof.core.git_provider import (
    get_head_diff,
    get_uncommitted_diff,
    install_pre_commit_hook,
)
from patchproof.core.healer import AutonomousHealer
from patchproof.core.normalizer import parse_patch
from patchproof.core.slicer import slice_test_suite
from patchproof.exceptions import (
    DockerUnavailableError,
    GitRepositoryError,
    NoChangesDetectedError,
    SandboxSecurityError,
    UnsupportedDockerEngineError,
)
from patchproof.factory.blast_radius import BlastRadiusClassifier
from patchproof.factory.engine import FactoryEngine, GateResult
from patchproof.factory.models import FactoryLineage, HybridLoopState
from patchproof.mcp import MCPServer
from patchproof.models.patch import CanonicalPatch
from patchproof.models.sandbox import (
    ComparativeExecutionResult,
    ContainerConfig,
    PatchVerdict,
    RegressionClassification,
    ResourceLimits,
)
from patchproof.reporting.badge import generate_svg_badge
from patchproof.reporting.certificate import (
    ProofCertificate,
    export_certificate,
    generate_certificate,
    verify_certificate,
)
from patchproof.reporting.diagnostic import AISelfHealingReport, generate_ai_feedback
from patchproof.reporting.html_report import generate_html_report
from patchproof.reporting.markdown import generate_markdown_summary
from patchproof.sandbox.comparative import ComparativeRunner
from patchproof.sandbox.docker_cli import parse_test_command
from patchproof.sandbox.doctor import SystemDoctor
from patchproof.sandbox.setup_runner import (
    DEFAULT_RUNNER_TAG,
    build_runner_from_repo,
    build_runner_image,
)

logger = logging.getLogger(__name__)


class ExitCode(IntEnum):
    """Exit codes returned by PatchProof CLI."""

    ACCEPTED = 0
    REJECTED = 1
    SUSPICIOUS = 2
    INCONCLUSIVE = 3
    INFRASTRUCTURE_FAILURE = 4
    CLI_ERROR = 5


VERDICT_EXIT_CODES: dict[PatchVerdict, int] = {
    PatchVerdict.ACCEPTED: ExitCode.ACCEPTED,
    PatchVerdict.REJECTED: ExitCode.REJECTED,
    PatchVerdict.SUSPICIOUS: ExitCode.SUSPICIOUS,
    PatchVerdict.INCONCLUSIVE: ExitCode.INCONCLUSIVE,
    PatchVerdict.INFRASTRUCTURE_FAILURE: ExitCode.INFRASTRUCTURE_FAILURE,
}


class PatchProofArgumentParser(argparse.ArgumentParser):
    """Custom ArgumentParser exiting with ExitCode.CLI_ERROR on argument errors."""

    def error(self, message: str) -> NoReturn:
        sys.stderr.write(f"patchproof: error: {message}\n")
        sys.exit(ExitCode.CLI_ERROR)


def build_parser() -> argparse.ArgumentParser:
    """Build the command-line argument parser."""
    parser = PatchProofArgumentParser(
        prog="patchproof",
        description="PatchProof: Deterministic Code Patch Verification Engine",
    )
    subparsers = parser.add_subparsers(
        dest="command",
        required=True,
        help="Subcommand to execute",
    )

    verify_parser = subparsers.add_parser(
        "verify",
        help="Verify a code patch against a test suite in an isolated sandbox",
    )
    verify_parser.add_argument(
        "patch",
        nargs="?",
        default=None,
        help="Path to patch file, or '-' for stdin. Optional if --git or --head is used",
    )
    verify_parser.add_argument(
        "--git",
        action="store_true",
        help="Extract and verify uncommitted working tree changes in target repository",
    )
    verify_parser.add_argument(
        "--head",
        action="store_true",
        help="Extract and verify changes from the latest HEAD commit in target repository",
    )
    verify_parser.add_argument(
        "--repo",
        default=".",
        help="Path to target repository on host (default: current directory)",
    )
    verify_parser.add_argument(
        "--image",
        default=None,
        help="Docker runner image (default: auto-detected from project)",
    )
    verify_parser.add_argument(
        "--timeout",
        type=float,
        default=15.0,
        help="Per-run container execution timeout in seconds (default: 15.0)",
    )
    verify_parser.add_argument(
        "--test-command",
        "-c",
        default=None,
        help="Custom test execution command (default: auto-detected from project)",
    )
    verify_parser.add_argument(
        "--json",
        action="store_true",
        help="Output machine-readable JSON report",
    )
    verify_parser.add_argument(
        "--html",
        default=None,
        metavar="PATH",
        help="Write self-contained visual HTML evidence report to specified path",
    )
    verify_parser.add_argument(
        "--verbose",
        action="store_true",
        help="Include full test execution details and individual test comparisons",
    )
    verify_parser.add_argument(
        "--ai-feedback",
        action="store_true",
        help="Display actionable AI self-healing diagnostic prompt on regression",
    )
    verify_parser.add_argument(
        "--slice",
        "--fast",
        dest="slice_tests",
        action="store_true",
        help="Predictively slice test suite to run only impacted tests",
    )
    verify_parser.add_argument(
        "--disambiguate-flakes",
        action="store_true",
        help="Re-run introduced regressions 3x in sandbox to filter out non-deterministic flakes",
    )
    verify_parser.add_argument(
        "--allow-test-edits",
        action="store_true",
        help="Allow test file modifications without triggering anti-cheat alerts",
    )
    verify_parser.add_argument(
        "--cert",
        default=None,
        metavar="PATH",
        help="Write cryptographic Proof-of-Patch certificate to specified path",
    )
    verify_parser.add_argument(
        "--secret-key",
        default=None,
        help="Custom secret key for HMAC certificate signing",
    )
    verify_parser.add_argument(
        "--private-key",
        default=None,
        help="Path to Ed25519 private key PEM file for asymmetric certificate signing",
    )
    verify_parser.add_argument(
        "--signer-identity",
        default=None,
        help="Signer identity string (e.g. CI workflow or actor)",
    )
    verify_parser.add_argument(
        "--markdown",
        "--summary-file",
        default=None,
        metavar="PATH",
        help="Write GitHub-Flavored Markdown summary proof card to specified path",
    )
    verify_parser.add_argument(
        "--badge",
        default=None,
        metavar="PATH",
        help="Write standalone SVG verification badge to specified path",
    )

    setup_runner_parser = subparsers.add_parser(
        "setup-runner",
        help="Build standard PatchProof test runner container image locally",
    )
    setup_runner_parser.add_argument(
        "--from-repo",
        default=None,
        metavar="PATH",
        help="Inspect repository dependencies (requirements, pyproject) and build runner",
    )
    setup_runner_parser.add_argument(
        "--tag",
        default=DEFAULT_RUNNER_TAG,
        help=f"Tag for runner image (default: {DEFAULT_RUNNER_TAG})",
    )
    setup_runner_parser.add_argument(
        "--dockerfile",
        default=None,
        help="Path to runner Dockerfile (default: auto-detected)",
    )
    setup_runner_parser.add_argument(
        "--no-cache",
        action="store_true",
        help="Do not use cache when building image",
    )
    setup_runner_parser.add_argument(
        "--quiet",
        action="store_true",
        help="Suppress non-error build progress output",
    )

    doctor_parser = subparsers.add_parser(
        "doctor",
        help="Inspect host environment prerequisites and report actionable diagnostics",
    )
    doctor_parser.add_argument(
        "--image",
        default=DEFAULT_RUNNER_TAG,
        help=f"Runner image to inspect (default: {DEFAULT_RUNNER_TAG})",
    )
    doctor_parser.add_argument(
        "--json",
        action="store_true",
        help="Output diagnostics in structured JSON format",
    )
    doctor_parser.add_argument(
        "--timeout",
        type=float,
        default=5.0,
        help="Timeout in seconds for individual diagnostic checks (default: 5.0)",
    )
    doctor_parser.add_argument(
        "--fix",
        action="store_true",
        help="Automatically attempt remediation for repairable environment issues",
    )

    install_hooks_parser = subparsers.add_parser(
        "install-hooks",
        help="Install Git pre-commit verification hook in target repository",
    )
    install_hooks_parser.add_argument(
        "--repo",
        default=".",
        help="Path to target Git repository (default: current directory)",
    )

    init_agent_parser = subparsers.add_parser(
        "init-agent",
        help="Initialize configuration and rules for AI coding agents (Claude, Cursor, Aider)",
    )
    init_agent_parser.add_argument(
        "agent",
        choices=["all", "aider", "cursor", "claude"],
        default="all",
        nargs="?",
        help="Target AI agent configuration to generate (default: all)",
    )
    init_agent_parser.add_argument(
        "--repo",
        default=".",
        help="Path to target repository (default: current directory)",
    )
    init_agent_parser.add_argument(
        "--force",
        action="store_true",
        help="Overwrite existing configuration files if they exist",
    )

    subparsers.add_parser(
        "mcp",
        help="Start built-in Model Context Protocol (MCP) server over standard I/O",
    )

    keygen_parser = subparsers.add_parser(
        "keygen",
        help="Generate Ed25519 cryptographic keypair for Proof-of-Patch attestation",
    )
    keygen_parser.add_argument(
        "--out-dir",
        default=".",
        help="Directory to save generated keypair (default: current directory)",
    )
    keygen_parser.add_argument(
        "--name",
        default="patchproof_key",
        help="Base name for key files (default: patchproof_key)",
    )

    verify_cert_parser = subparsers.add_parser(
        "verify-cert",
        help="Validate cryptographic signature and integrity of a Proof-of-Patch certificate",
    )
    verify_cert_parser.add_argument(
        "certificate",
        help="Path to Proof-of-Patch certificate JSON file",
    )
    verify_cert_parser.add_argument(
        "--secret-key",
        default=None,
        help="Custom HMAC secret key used for validation",
    )
    verify_cert_parser.add_argument(
        "--public-key",
        default=None,
        help="Path to Ed25519 public key file (.pub / PEM)",
    )

    heal_parser = subparsers.add_parser(
        "heal",
        help="Autonomous self-healing loop: verify and repair patch regressions",
    )
    heal_parser.add_argument(
        "patch",
        nargs="?",
        default=None,
        help="Path to patch file, or '-' for stdin. Optional if --git is used",
    )
    heal_parser.add_argument(
        "--git",
        action="store_true",
        help="Extract and heal uncommitted working tree changes",
    )
    heal_parser.add_argument(
        "--max-iterations",
        type=int,
        default=3,
        help="Maximum self-healing convergence iterations (default: 3)",
    )
    heal_parser.add_argument(
        "--repo",
        default=".",
        help="Path to target repository on host (default: current directory)",
    )

    factory_parser = subparsers.add_parser(
        "factory",
        help="AI Software Factory: assess blast radius and run autonomous hybrid loop",
    )
    factory_sub = factory_parser.add_subparsers(
        dest="factory_action",
        metavar="ACTION",
        required=True,
    )

    eval_parser = factory_sub.add_parser(
        "evaluate",
        help="Assess blast radius and assign Lineage A or Lineage B",
    )
    eval_parser.add_argument(
        "patch", nargs="?", default=None, help="Path to patch file, or '-' for stdin"
    )
    eval_parser.add_argument(
        "--git",
        action="store_true",
        help="Evaluate uncommitted changes in git working tree",
    )
    eval_parser.add_argument(
        "--head", action="store_true", help="Evaluate git HEAD commit diff"
    )
    eval_parser.add_argument(
        "--json", action="store_true", help="Output blast radius report as JSON"
    )
    eval_parser.add_argument("--repo", default=".", help="Repository root path")

    run_parser = factory_sub.add_parser(
        "run",
        help="Execute autonomous hybrid loop on a trigger signal",
    )
    run_parser.add_argument(
        "--trigger", required=True, help="Trigger signal identifier or description"
    )
    run_parser.add_argument("--patch", default=None, help="Path to candidate patch file")
    run_parser.add_argument("--repo", default=".", help="Repository root path")
    run_parser.add_argument(
        "--max-retries", type=int, default=3, help="Max retry loops (default: 3)"
    )
    run_parser.add_argument(
        "--json", action="store_true", help="Output final result as JSON"
    )

    return parser


def format_json_report(result: ComparativeExecutionResult, patch_content: str = "") -> str:
    """Format comparative execution result as structured JSON."""
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
        "metrics": {
            "introduced_regressions": result.introduced_regressions_count,
            "flaky_tests": result.flaky_tests_count,
            "resolved_defects": result.resolved_defects_count,
            "pre_existing_failures": result.pre_existing_failures_count,
            "suppressed_tests": result.suppressed_tests_count,
            "removed_tests": result.removed_tests_count,
            "test_tampering_detected": result.test_tampering_detected,
            "tampering_details": list(result.tampering_details),
            "collection_failure": result.collection_failure_occurred,
        },
        "execution": {
            "baseline": {
                "exit_code": result.baseline_result.exit_code,
                "duration_seconds": result.baseline_result.duration_seconds,
                "failure_reason": result.baseline_result.failure_reason.value,
                "oom_killed": result.baseline_result.oom_killed,
                "timeout_triggered": result.baseline_result.timeout_triggered,
            },
            "patched": {
                "exit_code": result.patched_result.exit_code,
                "duration_seconds": result.patched_result.duration_seconds,
                "failure_reason": result.patched_result.failure_reason.value,
                "oom_killed": result.patched_result.oom_killed,
                "timeout_triggered": result.patched_result.timeout_triggered,
            },
        },
        "tests": [
            {
                "name": tc.test_name,
                "baseline_outcome": tc.baseline_outcome.value if tc.baseline_outcome else None,
                "patched_outcome": tc.patched_outcome.value if tc.patched_outcome else None,
                "classification": tc.classification.value,
                "message": tc.message,
            }
            for tc in result.test_comparisons
        ],
    }
    return json.dumps(report, indent=2)


def format_terminal_report(result: ComparativeExecutionResult, verbose: bool = False) -> str:
    """Format comparative execution result as human-readable terminal output."""
    clean_count = sum(
        1
        for t in result.test_comparisons
        if t.classification == RegressionClassification.CLEAN_PRESERVATION
    )
    b_res = result.baseline_result
    p_res = result.patched_result

    lines: list[str] = [
        "=" * 78,
        "                      PATCHPROOF VERIFICATION REPORT",
        "=" * 78,
        f"Verdict:    {result.verdict.value.upper()}",
        f"Summary:    {result.summary}",
        "-" * 78,
        "Metrics:",
        f"  - Preserved Passing Tests:  {clean_count}",
        f"  - Resolved Defects:         {result.resolved_defects_count}",
        f"  - Introduced Regressions:   {result.introduced_regressions_count}",
        f"  - Suppressed Tests:         {result.suppressed_tests_count}",
        f"  - Removed Tests:            {result.removed_tests_count}",
        f"  - Test Tampering Alert:     {'DETECTED' if result.test_tampering_detected else 'None'}",
        f"  - Pre-existing Failures:    {result.pre_existing_failures_count}",
        f"  - Collection Failure:       {'YES' if result.collection_failure_occurred else 'No'}",
        "-" * 78,
        "Execution Details:",
        f"  - Baseline: Exit code {b_res.exit_code} ({b_res.duration_seconds:.2f}s)",
        f"  - Patched:  Exit code {p_res.exit_code} ({p_res.duration_seconds:.2f}s)",
    ]

    # Show critical test transitions or full breakdown if verbose
    critical_cases = [
        tc
        for tc in result.test_comparisons
        if verbose
        or tc.classification
        in (
            RegressionClassification.INTRODUCED_REGRESSION,
            RegressionClassification.RESOLVED_DEFECT,
            RegressionClassification.SUPPRESSION_ALERT,
        )
    ]

    if critical_cases:
        lines.append("-" * 78)
        lines.append("Test Transitions:")
        for tc in critical_cases:
            tag = f"[{tc.classification.value.upper()}]"
            b_out = tc.baseline_outcome.value.upper() if tc.baseline_outcome else "NONE"
            p_out = tc.patched_outcome.value.upper() if tc.patched_outcome else "NONE"
            lines.append(f"  {tag:<26} {tc.test_name} ({b_out} -> {p_out})")

    if result.test_tampering_detected and result.tampering_details:
        lines.append("-" * 78)
        lines.append("Anti-Cheat Tampering Violations:")
        for detail in result.tampering_details:
            lines.append(f"  [TAMPERING] {detail}")

    # Display diagnostic output if run failed or had collection/setup errors
    err_text = result.patched_result.stderr.strip()
    if err_text and result.verdict in (
        PatchVerdict.REJECTED,
        PatchVerdict.INFRASTRUCTURE_FAILURE,
    ):
        lines.append("-" * 78)
        lines.append("Diagnostic Output (Stderr):")
        for err_line in err_text.splitlines()[:5]:
            lines.append(f"  {err_line}")
        if len(err_text.splitlines()) > 5:
            lines.append("  [... truncated, use --verbose or --json for full output]")

    lines.append("=" * 78)
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    """Execute PatchProof CLI entrypoint."""
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "setup-runner":
        if args.from_repo:
            target_repo = pathlib.Path(args.from_repo).resolve()
            res_code, _assigned_tag = build_runner_from_repo(
                repo_path=target_repo,
                image_tag=args.tag if args.tag != DEFAULT_RUNNER_TAG else None,
                no_cache=args.no_cache,
                quiet=args.quiet,
            )
            return ExitCode.ACCEPTED if res_code == 0 else ExitCode.CLI_ERROR

        dockerfile = pathlib.Path(args.dockerfile) if args.dockerfile else None
        res_code = build_runner_image(
            image_tag=args.tag,
            dockerfile_path=dockerfile,
            no_cache=args.no_cache,
            quiet=args.quiet,
        )
        return ExitCode.ACCEPTED if res_code == 0 else ExitCode.CLI_ERROR

    if args.command == "doctor":
        doctor = SystemDoctor(timeout_seconds=args.timeout)
        if getattr(args, "fix", False):
            sys.stdout.write("patchproof: attempting automatic remediation...\n")
            fix_actions = doctor.auto_remediate(image_tag=args.image)
            for act in fix_actions:
                sys.stdout.write(f"  * {act}\n")
            sys.stdout.write("\n")
        results = doctor.run_all_checks(image_tag=args.image)
        sys.stdout.write(doctor.format_report(results, json_output=args.json) + "\n")
        return ExitCode.ACCEPTED if all(r.passed for r in results) else ExitCode.REJECTED

    if args.command == "install-hooks":
        repo_path = pathlib.Path(args.repo).resolve()
        try:
            hook_file = install_pre_commit_hook(repo_path)
            sys.stdout.write(f"patchproof: pre-commit hook installed successfully at {hook_file}\n")
            return ExitCode.ACCEPTED
        except GitRepositoryError as err:
            sys.stderr.write(f"patchproof: error: {err}\n")
            return ExitCode.CLI_ERROR

    if args.command == "mcp":
        server = MCPServer()
        server.run_stdio()
        return ExitCode.ACCEPTED

    if args.command == "init-agent":
        target_repo = pathlib.Path(args.repo).resolve()
        if not target_repo.is_dir():
            sys.stderr.write(f"patchproof: error: target directory not found: {args.repo}\n")
            return ExitCode.CLI_ERROR

        created_files: list[pathlib.Path] = []
        if args.agent == "aider":
            created_files = init_aider_config(target_repo, force=args.force)
        elif args.agent == "cursor":
            created_files = init_cursor_config(target_repo, force=args.force)
        elif args.agent == "claude":
            created_files = init_claude_config(target_repo, force=args.force)
        else:
            created_files = init_all_agents(target_repo, force=args.force)

        if created_files:
            sys.stdout.write("patchproof: successfully initialized AI agent configuration:\n")
            for f in created_files:
                try:
                    rel_f = f.relative_to(target_repo)
                except ValueError:
                    rel_f = f
                sys.stdout.write(f"  + {rel_f}\n")
        else:
            sys.stdout.write(
                "patchproof: configuration files already exist (use --force to overwrite)\n"
            )
        return ExitCode.ACCEPTED

    if args.command == "keygen":
        out_dir = pathlib.Path(args.out_dir).resolve()
        out_dir.mkdir(parents=True, exist_ok=True)
        priv_path = out_dir / f"{args.name}.pem"
        pub_path = out_dir / f"{args.name}.pub"
        from patchproof.reporting.asymmetric import generate_ed25519_keypair

        priv_pem, pub_pem = generate_ed25519_keypair()
        priv_path.write_text(priv_pem, encoding="utf-8")
        pub_path.write_text(pub_pem, encoding="utf-8")
        sys.stdout.write(
            f"patchproof: Generated Ed25519 keypair:\n"
            f"  Private key: {priv_path}\n"
            f"  Public key:  {pub_path}\n"
        )
        return ExitCode.ACCEPTED

    if args.command == "verify-cert":
        cert_path = pathlib.Path(args.certificate)
        if not cert_path.is_file():
            sys.stderr.write(f"patchproof: error: certificate file not found: {args.certificate}\n")
            return ExitCode.CLI_ERROR
        try:
            cert_data = json.loads(cert_path.read_text(encoding="utf-8"))
            cert = ProofCertificate(**cert_data)
        except Exception as err:
            sys.stderr.write(f"patchproof: error: invalid certificate format: {err}\n")
            return ExitCode.CLI_ERROR

        pub_key_pem = None
        if args.public_key:
            pub_path = pathlib.Path(args.public_key)
            if not pub_path.is_file():
                sys.stderr.write(
                    f"patchproof: error: public key file not found: {args.public_key}\n"
                )
                return ExitCode.CLI_ERROR
            pub_key_pem = pub_path.read_text(encoding="utf-8")

        if verify_certificate(cert, secret_key=args.secret_key, public_key_pem=pub_key_pem):
            sys.stdout.write(
                "patchproof: Proof-of-Patch certificate verified valid and untampered.\n"
            )
            return ExitCode.ACCEPTED
        else:
            sys.stderr.write(
                "patchproof: error: certificate validation failed "
                "(invalid signature or tampered content)\n"
            )
            return ExitCode.REJECTED

    if args.command == "heal":
        target_repo = pathlib.Path(args.repo).resolve()
        if not target_repo.is_dir():
            sys.stderr.write(f"patchproof: error: target directory not found: {args.repo}\n")
            return ExitCode.CLI_ERROR

        if args.git:
            try:
                heal_patch_content = get_uncommitted_diff(target_repo)
            except NoChangesDetectedError:
                sys.stdout.write("patchproof: working tree is clean; nothing to heal.\n")
                return ExitCode.ACCEPTED
            except GitRepositoryError as err:
                sys.stderr.write(f"patchproof: error: {err}\n")
                return ExitCode.CLI_ERROR
        elif args.patch:
            p_file = pathlib.Path(args.patch)
            if not p_file.is_file():
                sys.stderr.write(f"patchproof: error: patch file not found: {args.patch}\n")
                return ExitCode.CLI_ERROR
            heal_patch_content = p_file.read_text(encoding="utf-8")
        else:
            sys.stderr.write("patchproof: error: specify patch file or --git for heal\n")
            return ExitCode.CLI_ERROR

        project_profile = detect_project_stack(target_repo)
        cfg = ContainerConfig(
            host_repo_path=str(target_repo),
            image=project_profile.default_image,
            entrypoint_command=project_profile.default_test_command,
        )
        healer = AutonomousHealer()

        def default_callback(report: AISelfHealingReport) -> str | None:
            sys.stderr.write(report.prompt_feedback + "\n")
            return None

        heal_res = healer.heal(
            cfg,
            heal_patch_content,
            repair_agent_fn=default_callback,
            max_iterations=args.max_iterations,
        )
        if heal_res.success:
            sys.stdout.write(f"patchproof: {heal_res.summary}\n")
            return ExitCode.ACCEPTED
        else:
            sys.stderr.write(f"patchproof: {heal_res.summary}\n")
            return ExitCode.REJECTED

    if args.command == "factory":
        if args.factory_action == "evaluate":
            target_repo = pathlib.Path(args.repo).resolve()
            eval_patch: str
            if args.git:
                try:
                    eval_patch = get_uncommitted_diff(target_repo)
                except NoChangesDetectedError:
                    sys.stdout.write("patchproof: working tree is clean; no changes to evaluate.\n")
                    return ExitCode.ACCEPTED
                except GitRepositoryError as err:
                    sys.stderr.write(f"patchproof: error: {err}\n")
                    return ExitCode.CLI_ERROR
            elif args.head:
                try:
                    eval_patch = get_head_diff(target_repo)
                except NoChangesDetectedError:
                    sys.stdout.write("patchproof: HEAD commit introduces no diff changes.\n")
                    return ExitCode.ACCEPTED
                except GitRepositoryError as err:
                    sys.stderr.write(f"patchproof: error: {err}\n")
                    return ExitCode.CLI_ERROR
            elif args.patch == "-":
                eval_patch = sys.stdin.read()
            elif args.patch:
                p_file = pathlib.Path(args.patch)
                if not p_file.is_file():
                    sys.stderr.write(f"patchproof: error: patch file not found: {args.patch}\n")
                    return ExitCode.CLI_ERROR
                eval_patch = p_file.read_text(encoding="utf-8")
            else:
                sys.stderr.write("patchproof: error: must specify patch file, --git, or --head\n")
                return ExitCode.CLI_ERROR

            classifier = BlastRadiusClassifier()
            report = classifier.assess_patch(eval_patch)

            if args.json:
                sys.stdout.write(report.model_dump_json(indent=2) + "\n")
            else:
                lineage_label = (
                    "Lineage A (Contained / Level 4)"
                    if report.lineage == FactoryLineage.LINEAGE_A
                    else "Lineage B (Breaking / Level 3)"
                )
                sys.stdout.write(
                    f"patchproof factory: Blast Radius Assessment\n"
                    f"  Risk Level: {report.risk_level.value.upper()}\n"
                    f"  Routing:    {lineage_label}\n"
                    f"  Contained:  {report.is_contained}\n"
                    f"  Rationale:  {report.rationale}\n"
                )
            return ExitCode.ACCEPTED

        if args.factory_action == "run":
            target_repo = pathlib.Path(args.repo).resolve()
            engine = FactoryEngine(max_retries=args.max_retries)

            candidate_patch = ""
            if args.patch:
                p_path = pathlib.Path(args.patch)
                if p_path.is_file():
                    candidate_patch = p_path.read_text(encoding="utf-8")

            def generator(_: str | None) -> str:
                if candidate_patch:
                    return candidate_patch
                try:
                    return get_uncommitted_diff(target_repo)
                except Exception:
                    return ""

            def gate(patch_str: str) -> GateResult:
                if not patch_str.strip():
                    return GateResult(passed=False, diagnostics="Empty patch content")
                return GateResult(passed=True, diagnostics="")

            res = engine.run(
                run_id=f"run-{abs(hash(args.trigger)) % 100000}",
                generator_fn=generator,
                gate_fn=gate,
            )

            if args.json:
                sys.stdout.write(res.model_dump_json(indent=2) + "\n")
            else:
                sys.stdout.write(
                    f"patchproof factory: Run Completed\n"
                    f"  Run ID:    {res.run_id}\n"
                    f"  Status:    {res.status.value}\n"
                    f"  Lineage:   {res.lineage.value}\n"
                    f"  Shipped:   {res.shipped_to_production}\n"
                    f"  Needs Operator Approval: {res.operator_approval_required}\n"
                )

            return (
                ExitCode.ACCEPTED
                if res.status == HybridLoopState.PASSED
                else ExitCode.REJECTED
            )

    if args.command != "verify":
        sys.stderr.write(f"patchproof: unknown command: {args.command}\n")
        return ExitCode.CLI_ERROR

    # 1. Validate host repo directory
    repo_path = pathlib.Path(args.repo).resolve()
    if not repo_path.is_dir():
        sys.stderr.write(f"patchproof: error: target repo directory not found: {args.repo}\n")
        return ExitCode.CLI_ERROR

    # 2. Validate input source (mutually exclusive)
    inputs_count = sum([bool(args.patch), bool(args.git), bool(args.head)])
    if inputs_count == 0:
        sys.stderr.write("patchproof: error: must specify patch file, --git, or --head\n")
        return ExitCode.CLI_ERROR
    if inputs_count > 1:
        sys.stderr.write("patchproof: error: specify only one of patch file, --git, or --head\n")
        return ExitCode.CLI_ERROR

    # 3. Read patch content
    patch_content: str
    if args.git:
        try:
            patch_content = get_uncommitted_diff(repo_path)
        except NoChangesDetectedError:
            sys.stdout.write("patchproof: working tree clean; no uncommitted changes to verify.\n")
            return ExitCode.ACCEPTED
        except GitRepositoryError as err:
            sys.stderr.write(f"patchproof: error: {err}\n")
            return ExitCode.CLI_ERROR
    elif args.head:
        try:
            patch_content = get_head_diff(repo_path)
        except NoChangesDetectedError:
            sys.stdout.write("patchproof: HEAD commit introduces no diff changes.\n")
            return ExitCode.ACCEPTED
        except GitRepositoryError as err:
            sys.stderr.write(f"patchproof: error: {err}\n")
            return ExitCode.CLI_ERROR
    elif args.patch == "-":
        if sys.stdin.isatty():
            sys.stderr.write(
                "patchproof: reading patch from stdin (press Ctrl+D / Ctrl+Z to finish)...\n"
            )
        patch_content = sys.stdin.read()
    else:
        patch_path = pathlib.Path(args.patch)
        if not patch_path.is_file():
            sys.stderr.write(f"patchproof: error: patch file not found: {args.patch}\n")
            return ExitCode.CLI_ERROR
        try:
            patch_content = patch_path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as err:
            sys.stderr.write(f"patchproof: error: failed to read patch file: {err}\n")
            return ExitCode.CLI_ERROR

    # 4. Validate timeout
    if args.timeout <= 0.0 or args.timeout > 300.0:
        sys.stderr.write(
            f"patchproof: error: timeout must be between 0.1 and 300.0s, got {args.timeout}\n"
        )
        return ExitCode.CLI_ERROR

    # 3.5. Project auto-detection & command construction
    project_profile = detect_project_stack(repo_path)
    resolved_image = args.image if args.image is not None else project_profile.default_image

    entrypoint_cmd: tuple[str, ...]
    if args.test_command is not None:
        try:
            entrypoint_cmd = parse_test_command(args.test_command)
        except SandboxSecurityError as err:
            sys.stderr.write(f"patchproof: error: invalid test command: {err}\n")
            return ExitCode.CLI_ERROR
    else:
        entrypoint_cmd = project_profile.default_test_command

    # 3.8. Predictive Test Slicing
    if args.slice_tests and args.test_command is None:
        try:
            can_p = parse_patch(patch_content)
            slice_res = slice_test_suite(repo_path, can_p)
            if not slice_res.is_full_suite_fallback and slice_res.selected_test_paths:
                entrypoint_cmd = (entrypoint_cmd[0], "-q", *slice_res.selected_test_paths)
                if not args.json:
                    count_tests = len(slice_res.selected_test_paths)
                    sys.stderr.write(
                        f"patchproof: predictive test slicing isolated {count_tests} "
                        f"test file(s) in {slice_res.duration_ms:.1f}ms\n"
                    )
        except Exception:
            pass

    # 4. Construct configuration
    try:
        config = ContainerConfig(
            host_repo_path=str(repo_path),
            image=resolved_image,
            resources=ResourceLimits(timeout_seconds=args.timeout),
            entrypoint_command=entrypoint_cmd,
        )
    except Exception as err:
        sys.stderr.write(f"patchproof: error: configuration error: {err}\n")
        return ExitCode.CLI_ERROR

    # 5. Execute comparative verification
    runner = ComparativeRunner()
    try:
        result = runner.run(
            config,
            patch_content,
            disambiguate_flakes=args.disambiguate_flakes,
            allow_test_edits=args.allow_test_edits,
        )
    except SandboxSecurityError as err:
        sys.stderr.write(f"patchproof: security error: {err}\n")
        return ExitCode.REJECTED
    except (DockerUnavailableError, UnsupportedDockerEngineError) as err:
        sys.stderr.write(f"patchproof: infrastructure error: {err}\n")
        return ExitCode.INFRASTRUCTURE_FAILURE
    except Exception as err:
        sys.stderr.write(f"patchproof: execution error: {err}\n")
        return ExitCode.CLI_ERROR

    # 6. Render output
    if args.html:
        html_path = pathlib.Path(args.html)
        try:
            html_content = generate_html_report(result, patch_content=patch_content)
            html_path.write_text(html_content, encoding="utf-8")
        except OSError as err:
            sys.stderr.write(f"patchproof: error: failed to write HTML report: {err}\n")

    if args.ai_feedback and result.verdict != PatchVerdict.ACCEPTED:
        canonical_p: CanonicalPatch | None = None
        if patch_content:
            try:
                canonical_p = parse_patch(patch_content)
            except Exception:
                pass
        ai_diag = generate_ai_feedback(result, patch=canonical_p)
        sys.stderr.write(ai_diag.prompt_feedback + "\n")

    cert_obj: ProofCertificate | None = None
    if args.cert:
        cert_out = pathlib.Path(args.cert)
        try:
            signing_key_pem: str | None = None
            if args.private_key:
                pk_path = pathlib.Path(args.private_key)
                if pk_path.is_file():
                    signing_key_pem = pk_path.read_text(encoding="utf-8")
                else:
                    sys.stderr.write(
                        f"patchproof: warning: private key file not found: {args.private_key}\n"
                    )
            cert_obj = generate_certificate(
                result,
                patch_content=patch_content,
                secret_key=args.secret_key,
                private_key_pem=signing_key_pem,
                signer_identity=args.signer_identity,
            )
            export_certificate(cert_obj, cert_out)
            if not args.json:
                sys.stdout.write(f"patchproof: Proof-of-Patch certificate written to {cert_out}\n")
        except Exception as err:
            sys.stderr.write(f"patchproof: error: failed to write certificate: {err}\n")

    if args.markdown:
        md_out = pathlib.Path(args.markdown)
        try:
            md_content = generate_markdown_summary(
                result, cert=cert_obj, patch_content=patch_content
            )
            md_out.parent.mkdir(parents=True, exist_ok=True)
            md_out.write_text(md_content, encoding="utf-8")
            if not args.json:
                sys.stdout.write(f"patchproof: Markdown summary card written to {md_out}\n")
        except OSError as err:
            sys.stderr.write(f"patchproof: error: failed to write Markdown summary: {err}\n")

    if args.badge:
        badge_out = pathlib.Path(args.badge)
        try:
            badge_content = generate_svg_badge(result)
            badge_out.parent.mkdir(parents=True, exist_ok=True)
            badge_out.write_text(badge_content, encoding="utf-8")
            if not args.json:
                sys.stdout.write(f"patchproof: SVG badge written to {badge_out}\n")
        except OSError as err:
            sys.stderr.write(f"patchproof: error: failed to write SVG badge: {err}\n")

    if args.json:
        sys.stdout.write(format_json_report(result, patch_content=patch_content) + "\n")
    else:
        sys.stdout.write(format_terminal_report(result, verbose=args.verbose) + "\n")

    return VERDICT_EXIT_CODES.get(result.verdict, ExitCode.CLI_ERROR)
