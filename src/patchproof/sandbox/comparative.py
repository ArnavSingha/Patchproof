"""Comparative execution engine orchestrating dual-run baseline-versus-patched evaluations."""

from __future__ import annotations

import logging
import os
import pathlib
import shutil
import tempfile

from patchproof.core.applier import apply_patch
from patchproof.core.git_provider import export_git_tree, is_git_repository
from patchproof.core.normalizer import parse_patch
from patchproof.core.runtime_guard import generate_runtime_guard_plugin
from patchproof.core.search_replace import SEARCH_START, parse_search_replace
from patchproof.exceptions import (
    DockerUnavailableError,
    InvalidPatchFormatError,
    PatchApplicationError,
    SandboxSecurityError,
    UnsupportedDockerEngineError,
)
from patchproof.models.patch import CanonicalPatch
from patchproof.models.sandbox import (
    ComparativeExecutionResult,
    ComparativeTestCase,
    ContainerConfig,
    ContainerEnvironment,
    ExecutionResult,
    PatchVerdict,
    RegressionClassification,
    SandboxFailureReason,
)
from patchproof.sandbox.classifier import RegressionClassifier
from patchproof.sandbox.flakiness import disambiguate_flakiness
from patchproof.sandbox.parser import PytestOutputParser
from patchproof.sandbox.runner import ContainerRunner

logger = logging.getLogger(__name__)

STAGING_IGNORE_PATTERNS = (
    ".git",
    ".venv",
    "env",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
)

BASELINE_INFRASTRUCTURE_FAILURES = (
    SandboxFailureReason.TIMEOUT,
    SandboxFailureReason.OOM_KILLED,
    SandboxFailureReason.DOCKER_UNAVAILABLE,
    SandboxFailureReason.UNSUPPORTED_ENGINE,
    SandboxFailureReason.SETUP_ERROR,
)


def safe_copy_repository(
    src_repo_path: str | pathlib.Path,
    dst_staging_path: str | pathlib.Path,
    ignore_patterns: tuple[str, ...] = STAGING_IGNORE_PATTERNS,
) -> None:
    """Safely copy repository source to an ephemeral staging directory.

    Rejects and fails closed on any symlinks pointing outside the repository root.
    Never copies git directories, virtual environments, or build caches.

    Raises:
        SandboxSecurityError: If any symlink points outside the repository root.
    """
    src_root = pathlib.Path(src_repo_path).resolve()
    dst_root = pathlib.Path(dst_staging_path).resolve()

    # Pre-scan for malicious symlinks escaping repository root
    for root_dir, dirs, files in os.walk(src_root, followlinks=False):
        current_path = pathlib.Path(root_dir)
        # Filter ignored directory names in-place
        dirs[:] = [d for d in dirs if d not in ignore_patterns]

        for item_name in dirs + files:
            item_path = current_path / item_name
            if item_path.is_symlink():
                resolved_target = item_path.resolve()
                try:
                    resolved_target.relative_to(src_root)
                except ValueError as err:
                    raise SandboxSecurityError(
                        f"Symlink traversal detected: '{item_path}' points outside "
                        f"repository root to '{resolved_target}'"
                    ) from err

    shutil.copytree(
        src_root,
        dst_root,
        ignore=shutil.ignore_patterns(*ignore_patterns),
        dirs_exist_ok=True,
    )


def safe_cleanup_staging(staging_dir: pathlib.Path) -> None:
    """Safely remove ephemeral staging directory with boundary verification."""
    try:
        resolved = staging_dir.resolve()
        temp_root = pathlib.Path(tempfile.gettempdir()).resolve()

        # Invariant 1: Must be inside system temporary directory
        resolved.relative_to(temp_root)

        # Invariant 2: Must bear patchproof-staging prefix
        if not resolved.name.startswith("patchproof-staging-"):
            logger.error("Refusing cleanup: missing patchproof-staging prefix: %s", resolved)
            return

        # Invariant 3: Must not be a root or shallow path
        if resolved == temp_root or str(resolved) == temp_root.root or len(resolved.parts) <= 2:
            logger.error("Refusing cleanup: broad path detected: %s", resolved)
            return

        shutil.rmtree(resolved, ignore_errors=True)
    except (ValueError, OSError) as err:
        logger.warning("Staging cleanup error for %s: %s", staging_dir, err)


class ComparativeRunner:
    """Orchestrates isolated baseline and patched container runs and classifies regressions."""

    def __init__(
        self,
        runner: ContainerRunner | None = None,
        parser: PytestOutputParser | None = None,
        classifier: RegressionClassifier | None = None,
    ) -> None:
        self.runner = runner or ContainerRunner()
        self.parser = parser or PytestOutputParser()
        self.classifier = classifier or RegressionClassifier()

    def run(
        self,
        config: ContainerConfig,
        patch: CanonicalPatch | str,
        disambiguate_flakes: bool = False,
        allow_test_edits: bool = False,
    ) -> ComparativeExecutionResult:
        """Execute baseline and patched container runs, isolating workspaces and verifying.

        1. Validates the patch format.
        2. Prepares ephemeral staging workspaces for baseline and patched runs.
        3. Copies repository into baseline staging workspace (auditing symlinks).
        4. Copies repository into patched staging workspace.
        5. Executes baseline tests against baseline staging workspace.
        6. Applies patch to the patched staging workspace.
        7. Executes patched tests against patched staging workspace.
        8. Reconciles test results and produces immutable ComparativeExecutionResult.
        9. Guaranteed cleanup of all staging workspaces in finally block.
        """
        # Step 1: Pre-validate patch format
        canonical_patch: CanonicalPatch
        if isinstance(patch, str):
            try:
                if SEARCH_START in patch:
                    canonical_patch = parse_search_replace(patch)
                else:
                    canonical_patch = parse_patch(patch)
            except InvalidPatchFormatError as err:
                dummy_res = ExecutionResult(
                    exit_code=1,
                    stderr=str(err),
                    duration_seconds=0.0,
                    failure_reason=SandboxFailureReason.SETUP_ERROR,
                )
                return ComparativeExecutionResult(
                    baseline_result=dummy_res,
                    patched_result=dummy_res,
                    verdict=PatchVerdict.REJECTED,
                    summary=f"Invalid patch format: {err}",
                )
        else:
            canonical_patch = patch

        # Step 1b: Anti-cheat patch integrity audit
        from patchproof.core.anti_cheat import audit_patch_integrity

        anti_cheat_report = audit_patch_integrity(
            patch=canonical_patch,
            repo_root=config.host_repo_path,
            allow_test_edits=allow_test_edits,
        )

        # Step 2: Prepare ephemeral staging workspaces
        baseline_staging = pathlib.Path(tempfile.mkdtemp(prefix="patchproof-staging-base-"))
        patched_staging = pathlib.Path(tempfile.mkdtemp(prefix="patchproof-staging-patch-"))

        try:
            # Step 2: Populate staging repositories
            # Attempt pristine Git tree export first to guarantee clean baseline
            git_isolated = False
            if is_git_repository(config.host_repo_path):
                try:
                    base_ok = export_git_tree(config.host_repo_path, "HEAD", baseline_staging)
                    patch_ok = export_git_tree(config.host_repo_path, "HEAD", patched_staging)
                    git_isolated = base_ok and patch_ok
                except SandboxSecurityError as err:
                    dummy_res = ExecutionResult(
                        exit_code=1,
                        stderr=f"Security violation during staging: {err}",
                        duration_seconds=0.0,
                        failure_reason=SandboxFailureReason.SETUP_ERROR,
                    )
                    return ComparativeExecutionResult(
                        baseline_result=dummy_res,
                        patched_result=dummy_res,
                        verdict=PatchVerdict.REJECTED,
                        summary=f"Security violation during staging: {err}",
                    )

            if not git_isolated:
                # Fallback to direct safe_copy_repository for non-git or bare environments
                try:
                    safe_copy_repository(config.host_repo_path, baseline_staging)
                except SandboxSecurityError as err:
                    dummy_res = ExecutionResult(
                        exit_code=1,
                        stderr=f"Security violation during staging: {err}",
                        duration_seconds=0.0,
                        failure_reason=SandboxFailureReason.SETUP_ERROR,
                    )
                    return ComparativeExecutionResult(
                        baseline_result=dummy_res,
                        patched_result=dummy_res,
                        verdict=PatchVerdict.REJECTED,
                        summary=f"Security violation during staging: {err}",
                    )

                try:
                    safe_copy_repository(config.host_repo_path, patched_staging)
                except SandboxSecurityError as err:
                    dummy_res = ExecutionResult(
                        exit_code=1,
                        stderr=f"Security violation during staging: {err}",
                        duration_seconds=0.0,
                        failure_reason=SandboxFailureReason.SETUP_ERROR,
                    )
                    return ComparativeExecutionResult(
                        baseline_result=dummy_res,
                        patched_result=dummy_res,
                        verdict=PatchVerdict.REJECTED,
                        summary=f"Security violation during staging: {err}",
                    )

            # Inject PatchProof dynamic runtime assertion guard into staging trees
            guard_source = generate_runtime_guard_plugin()
            (baseline_staging / "patchproof_runtime_guard.py").write_text(
                guard_source, encoding="utf-8"
            )
            (patched_staging / "patchproof_runtime_guard.py").write_text(
                guard_source, encoding="utf-8"
            )

            # Enable auto-loading of runtime guard plugin in pytest
            current_env = dict(config.environment.env_vars)
            existing_pytest_opts = current_env.get("PYTEST_ADDOPTS", "")
            combined_opts = f"{existing_pytest_opts} -p patchproof_runtime_guard".strip()
            guarded_env = ContainerEnvironment(
                env_vars=(
                    *tuple((k, v) for k, v in current_env.items() if k != "PYTEST_ADDOPTS"),
                    ("PYTEST_ADDOPTS", combined_opts),
                )
            )

            # Step 3: Execute baseline test container
            baseline_config = config.model_copy(
                update={"host_repo_path": str(baseline_staging), "environment": guarded_env}
            )
            try:
                base_raw = self.runner.run(baseline_config)
            except DockerUnavailableError as err:
                dummy_res = ExecutionResult(
                    exit_code=1,
                    stderr=str(err),
                    duration_seconds=0.0,
                    failure_reason=SandboxFailureReason.DOCKER_UNAVAILABLE,
                )
                return ComparativeExecutionResult(
                    baseline_result=dummy_res,
                    patched_result=dummy_res,
                    verdict=PatchVerdict.INFRASTRUCTURE_FAILURE,
                    summary=f"Infrastructure failure: Docker daemon is unavailable ({err})",
                )
            except UnsupportedDockerEngineError as err:
                dummy_res = ExecutionResult(
                    exit_code=1,
                    stderr=str(err),
                    duration_seconds=0.0,
                    failure_reason=SandboxFailureReason.UNSUPPORTED_ENGINE,
                )
                return ComparativeExecutionResult(
                    baseline_result=dummy_res,
                    patched_result=dummy_res,
                    verdict=PatchVerdict.INFRASTRUCTURE_FAILURE,
                    summary=f"Infrastructure failure: Unsupported Docker engine ({err})",
                )

            base_report = self.parser.parse(
                stdout=base_raw.stdout,
                stderr=base_raw.stderr,
                exit_code=base_raw.exit_code,
                duration_seconds=base_raw.duration_seconds,
            )
            baseline_result = base_raw.model_copy(update={"test_report": base_report})

            # Early abort only if baseline execution suffered infrastructure or collection failure
            if (
                baseline_result.failure_reason in BASELINE_INFRASTRUCTURE_FAILURES
                or base_report.collection_failed
            ):
                return self.classifier.classify(baseline_result, baseline_result)

            # Step 4: Apply patch to patched staging workspace
            try:
                apply_patch(canonical_patch, patched_staging)
            except (PatchApplicationError, SandboxSecurityError) as err:
                synthetic_patched = ExecutionResult(
                    exit_code=1,
                    stderr=f"Patch application failed: {err}",
                    duration_seconds=0.0,
                    failure_reason=SandboxFailureReason.SETUP_ERROR,
                )
                return self.classifier.classify(baseline_result, synthetic_patched)

            # Step 5: Execute patched test container
            patched_config = config.model_copy(
                update={"host_repo_path": str(patched_staging), "environment": guarded_env}
            )
            try:
                patched_raw = self.runner.run(patched_config)
            except DockerUnavailableError as err:
                dummy_res = ExecutionResult(
                    exit_code=1,
                    stderr=str(err),
                    duration_seconds=0.0,
                    failure_reason=SandboxFailureReason.DOCKER_UNAVAILABLE,
                )
                return ComparativeExecutionResult(
                    baseline_result=baseline_result,
                    patched_result=dummy_res,
                    verdict=PatchVerdict.INFRASTRUCTURE_FAILURE,
                    summary=f"Patched run infrastructure failure: Docker unavailable ({err})",
                )
            except UnsupportedDockerEngineError as err:
                dummy_res = ExecutionResult(
                    exit_code=1,
                    stderr=str(err),
                    duration_seconds=0.0,
                    failure_reason=SandboxFailureReason.UNSUPPORTED_ENGINE,
                )
                return ComparativeExecutionResult(
                    baseline_result=baseline_result,
                    patched_result=dummy_res,
                    verdict=PatchVerdict.INFRASTRUCTURE_FAILURE,
                    summary=f"Patched run infrastructure failure: Unsupported engine ({err})",
                )

            patch_report = self.parser.parse(
                stdout=patched_raw.stdout,
                stderr=patched_raw.stderr,
                exit_code=patched_raw.exit_code,
                duration_seconds=patched_raw.duration_seconds,
            )
            patched_result = patched_raw.model_copy(update={"test_report": patch_report})

            # Step 6: Reconcile and classify results
            initial_result = self.classifier.classify(baseline_result, patched_result)

            # Step 7: Flakiness Disambiguation for introduced regressions
            if disambiguate_flakes and initial_result.introduced_regressions_count > 0:
                new_comparisons: list[ComparativeTestCase] = []
                remaining_regressions = 0
                flaky_count = 0

                for tc in initial_result.test_comparisons:
                    if tc.classification == RegressionClassification.INTRODUCED_REGRESSION:

                        def _single_runner(test_id: str) -> tuple[bool, float]:
                            retry_cfg = patched_config.model_copy(
                                update={"entrypoint_command": ("pytest", "-q", test_id)}
                            )
                            try:
                                r = self.runner.run(retry_cfg)
                                return (r.exit_code == 0, r.duration_seconds)
                            except Exception:
                                return (False, 0.0)

                        flake_rep = disambiguate_flakiness(tc.test_name, _single_runner, attempts=3)
                        if flake_rep.is_flaky:
                            flaky_count += 1
                            new_comparisons.append(
                                tc.model_copy(
                                    update={
                                        "classification": RegressionClassification.FLAKY_REGRESSION,
                                        "message": f"Flaky: {flake_rep.summary}",
                                    }
                                )
                            )
                        else:
                            remaining_regressions += 1
                            new_comparisons.append(tc)
                    else:
                        new_comparisons.append(tc)

                if flaky_count > 0:
                    new_verdict = (
                        PatchVerdict.REJECTED
                        if remaining_regressions > 0
                        else PatchVerdict.SUSPICIOUS
                    )
                    new_summary = (
                        f"Patch verified with {flaky_count} flaky test(s) detected; "
                        f"{remaining_regressions} confirmed regression(s)"
                    )
                    initial_result = initial_result.model_copy(
                        update={
                            "test_comparisons": tuple(new_comparisons),
                            "introduced_regressions_count": remaining_regressions,
                            "flaky_tests_count": flaky_count,
                            "verdict": new_verdict,
                            "summary": new_summary,
                        }
                    )

            if anti_cheat_report.tampering_detected:
                tamper_verdict = (
                    PatchVerdict.REJECTED
                    if anti_cheat_report.tests_deleted_count > 0
                    else PatchVerdict.SUSPICIOUS
                )
                tamper_summary = (
                    f"Test tampering detected ({anti_cheat_report.summary}); "
                    f"raw test verdict: {initial_result.verdict.value}"
                )
                return initial_result.model_copy(
                    update={
                        "test_tampering_detected": True,
                        "tampering_details": tuple(v.details for v in anti_cheat_report.violations),
                        "verdict": tamper_verdict,
                        "summary": tamper_summary,
                    }
                )

            return initial_result

        finally:
            # Guaranteed staging workspaces cleanup
            safe_cleanup_staging(baseline_staging)
            safe_cleanup_staging(patched_staging)
