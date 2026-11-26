"""Runner image build and lifecycle management for PatchProof."""

from __future__ import annotations

import logging
import pathlib
import platform
import shutil
import subprocess
import sys
from typing import IO

from patchproof.exceptions import DockerUnavailableError
from patchproof.sandbox.docker_cli import DockerPreflight

logger = logging.getLogger(__name__)

DEFAULT_RUNNER_TAG = "patchproof-runner-python:3.11"
DEFAULT_DOCKERFILE_PATH = pathlib.Path("docker/runner.Dockerfile")


def resolve_default_dockerfile() -> pathlib.Path:
    """Locate the default runner Dockerfile in the project or package hierarchy."""
    # Check 1: Relative to current working directory
    cwd_candidate = pathlib.Path.cwd() / DEFAULT_DOCKERFILE_PATH
    if cwd_candidate.exists() and cwd_candidate.is_file():
        return cwd_candidate.resolve()

    # Check 2: Relative to patchproof package root (e.g. installed package)
    package_dir = pathlib.Path(__file__).resolve().parent.parent.parent.parent
    pkg_candidate = package_dir / DEFAULT_DOCKERFILE_PATH
    if pkg_candidate.exists() and pkg_candidate.is_file():
        return pkg_candidate.resolve()

    # Fallback to direct path representation
    return DEFAULT_DOCKERFILE_PATH.resolve()


def build_runner_image(
    image_tag: str = DEFAULT_RUNNER_TAG,
    dockerfile_path: pathlib.Path | str | None = None,
    no_cache: bool = False,
    quiet: bool = False,
    timeout_seconds: float | None = 600.0,
    stdout_stream: IO[str] | object | None = None,
    stderr_stream: IO[str] | object | None = None,
) -> int:
    """Build the standard PatchProof runner container image locally using Docker CLI.

    Args:
        image_tag: The Docker image tag to assign (default: 'patchproof-runner-python:3.11').
        dockerfile_path: Path to the runner Dockerfile (default: auto-detected).
        no_cache: If True, passes --no-cache to docker build.
        quiet: If True, suppresses non-error output.
        stdout_stream: Stream to direct build stdout (defaults to sys.stdout).
        stderr_stream: Stream to direct build stderr (defaults to sys.stderr).

    Returns:
        0 on success, non-zero exit code on failure.
    """
    out = stdout_stream or sys.stdout
    err = stderr_stream or sys.stderr

    # Step 1: Preflight check for Docker CLI and daemon
    preflight = DockerPreflight(timeout_seconds=5.0)
    try:
        preflight.check()
    except DockerUnavailableError as exc:
        msg = (
            f"Error: Docker is unavailable: {exc}\n"
            "Remediation: Verify Docker Desktop or the docker daemon is installed and running.\n"
        )
        if hasattr(err, "write"):
            err.write(msg)
        return 1
    except Exception as exc:
        msg = f"Error: Docker preflight check failed: {exc}\n"
        if hasattr(err, "write"):
            err.write(msg)
        return 1

    # Step 2: Resolve Dockerfile path
    if dockerfile_path is None:
        resolved_dockerfile = resolve_default_dockerfile()
    else:
        resolved_dockerfile = pathlib.Path(dockerfile_path).resolve()

    if not resolved_dockerfile.exists() or not resolved_dockerfile.is_file():
        msg = (
            f"Error: Runner Dockerfile not found at '{resolved_dockerfile}'.\n"
            "Remediation: Ensure 'docker/runner.Dockerfile' exists in the repository root.\n"
        )
        if hasattr(err, "write"):
            err.write(msg)
        return 1

    # Step 3: Determine build context directory
    # Build context should be the parent repository root of the dockerfile
    context_dir = resolved_dockerfile.parent.parent
    if not (context_dir / "docker").exists():
        context_dir = resolved_dockerfile.parent

    arch = platform.machine()
    if not quiet and hasattr(out, "write"):
        out.write(
            f"Building PatchProof runner image '{image_tag}' (arch: {arch})\n"
            f"Dockerfile: {resolved_dockerfile}\n"
            f"Context:    {context_dir}\n"
        )

    # Step 4: Construct hardened argument vector (shell=False)
    docker_bin = shutil.which("docker") or "docker"
    build_cmd = [
        docker_bin,
        "build",
        "-t",
        image_tag,
        "-f",
        str(resolved_dockerfile),
    ]
    if no_cache:
        build_cmd.append("--no-cache")
    build_cmd.append(str(context_dir))

    # Step 5: Execute build with bound subprocess
    try:
        result = subprocess.run(
            build_cmd,
            shell=False,
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
        )
        if result.stdout and not quiet and hasattr(out, "write"):
            out.write(result.stdout)
    except subprocess.TimeoutExpired:
        if hasattr(err, "write"):
            err.write(f"Error: docker build timed out after {timeout_seconds} seconds.\n")
        return 1
    except FileNotFoundError:
        if hasattr(err, "write"):
            err.write("Error: 'docker' executable not found on system PATH.\n")
        return 1
    except OSError as exc:
        if hasattr(err, "write"):
            err.write(f"Error executing docker build: {exc}\n")
        return 1

    if result.returncode == 0:
        if not quiet and hasattr(out, "write"):
            out.write(f"\nSuccessfully built and tagged runner image: {image_tag}\n")
        return 0

    if hasattr(err, "write"):
        err.write(f"\nError: docker build failed with exit code {result.returncode}\n")
        if result.stderr:
            err.write(result.stderr)
    return result.returncode
