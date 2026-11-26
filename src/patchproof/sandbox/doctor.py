"""System environment and dependency doctor for PatchProof."""

from __future__ import annotations

import json
import logging
import platform
import shutil
import subprocess
import sys

from pydantic import BaseModel, ConfigDict, Field

logger = logging.getLogger(__name__)

DEFAULT_RUNNER_TAG = "patchproof-runner-python:3.11"


class DoctorCheckResult(BaseModel):
    """Result of an individual environment health check."""

    model_config = ConfigDict(frozen=True)

    name: str = Field(description="Descriptive name of the diagnostic check")
    passed: bool = Field(description="True if the diagnostic check satisfied all requirements")
    details: str = Field(description="Detailed observation or version information")
    remediation: str | None = Field(
        default=None,
        description="Actionable remediation steps if the check failed",
    )


class SystemDoctor:
    """Diagnostic inspector verifying the host environment against PatchProof prerequisites."""

    def __init__(self, timeout_seconds: float = 5.0) -> None:
        self.timeout_seconds = timeout_seconds

    def check_python_version(self) -> DoctorCheckResult:
        """Verify Python runtime version is 3.11 or higher."""
        ver = sys.version_info
        major, minor, micro = ver[0], ver[1], ver[2]
        version_str = f"{major}.{minor}.{micro}"
        passed = (major, minor) >= (3, 11)
        if passed:
            return DoctorCheckResult(
                name="Python Version",
                passed=True,
                details=f"Python {version_str} (>= 3.11 required)",
            )
        return DoctorCheckResult(
            name="Python Version",
            passed=False,
            details=f"Python {version_str} is unsupported",
            remediation="Install Python 3.11 or newer from https://www.python.org",
        )

    def check_git_cli(self) -> DoctorCheckResult:
        """Verify Git CLI executable is installed and reachable on PATH."""
        git_path = shutil.which("git")
        if not git_path:
            return DoctorCheckResult(
                name="Git CLI",
                passed=False,
                details="Executable 'git' not found on system PATH",
                remediation=(
                    "Install Git and ensure 'git' is added to PATH (https://git-scm.com/downloads)"
                ),
            )

        try:
            res = subprocess.run(
                [git_path, "--version"],
                shell=False,
                capture_output=True,
                text=True,
                check=False,
                timeout=self.timeout_seconds,
            )
            if res.returncode == 0:
                version_line = res.stdout.strip() or f"Found at {git_path}"
                return DoctorCheckResult(
                    name="Git CLI",
                    passed=True,
                    details=version_line,
                )
            return DoctorCheckResult(
                name="Git CLI",
                passed=False,
                details=f"Git command failed with exit code {res.returncode}: {res.stderr.strip()}",
                remediation="Ensure the Git installation is healthy and operable",
            )
        except (subprocess.TimeoutExpired, OSError) as exc:
            return DoctorCheckResult(
                name="Git CLI",
                passed=False,
                details=f"Error executing git: {exc}",
                remediation="Verify Git permissions and executable integrity",
            )

    def check_docker_cli(self) -> DoctorCheckResult:
        """Verify Docker CLI binary is installed and reachable on PATH."""
        docker_path = shutil.which("docker")
        if not docker_path:
            return DoctorCheckResult(
                name="Docker CLI",
                passed=False,
                details="Executable 'docker' not found on system PATH",
                remediation=(
                    "Install Docker Desktop (Windows/macOS) or Docker Engine (Linux) "
                    "from https://docs.docker.com/get-docker/"
                ),
            )

        try:
            res = subprocess.run(
                [docker_path, "--version"],
                shell=False,
                capture_output=True,
                text=True,
                check=False,
                timeout=self.timeout_seconds,
            )
            if res.returncode == 0:
                version_line = res.stdout.strip() or f"Found at {docker_path}"
                return DoctorCheckResult(
                    name="Docker CLI",
                    passed=True,
                    details=version_line,
                )
            return DoctorCheckResult(
                name="Docker CLI",
                passed=False,
                details=(
                    f"Docker CLI failed with exit code {res.returncode}: {res.stderr.strip()}"
                ),
                remediation="Verify Docker CLI installation and executable integrity",
            )
        except (subprocess.TimeoutExpired, OSError) as exc:
            return DoctorCheckResult(
                name="Docker CLI",
                passed=False,
                details=f"Error executing docker: {exc}",
                remediation="Verify Docker CLI permissions and executable integrity",
            )

    def check_docker_daemon(self) -> tuple[DoctorCheckResult, bool]:
        """Verify Docker daemon is active and responding to API requests."""
        docker_path = shutil.which("docker")
        if not docker_path:
            return (
                DoctorCheckResult(
                    name="Docker Daemon",
                    passed=False,
                    details="Docker CLI is not installed",
                    remediation="Install Docker before starting the daemon",
                ),
                False,
            )

        try:
            res = subprocess.run(
                [docker_path, "info", "--format", "{{.ServerVersion}}"],
                shell=False,
                capture_output=True,
                text=True,
                check=False,
                timeout=self.timeout_seconds,
            )
            if res.returncode == 0:
                server_ver = res.stdout.strip()
                return (
                    DoctorCheckResult(
                        name="Docker Daemon",
                        passed=True,
                        details=f"Active (Server Version: {server_ver})",
                    ),
                    True,
                )
            stderr_msg = res.stderr.strip() or "Daemon unreachable"
            sys_os = platform.system().lower()
            if sys_os == "darwin":
                remed = (
                    "Start Docker Desktop by running 'open -a Docker' or launching Applications"
                )
            elif sys_os == "linux":
                remed = (
                    "Start Docker daemon by running 'sudo systemctl start docker' "
                    "or 'sudo service docker start'"
                )
            else:
                remed = (
                    "Start Docker Desktop or run 'sudo systemctl start docker' "
                    "and verify daemon is running"
                )
            return (
                DoctorCheckResult(
                    name="Docker Daemon",
                    passed=False,
                    details=f"Daemon not reachable: {stderr_msg}",
                    remediation=remed,
                ),
                False,
            )
        except subprocess.TimeoutExpired:
            return (
                DoctorCheckResult(
                    name="Docker Daemon",
                    passed=False,
                    details="Connection to Docker daemon timed out",
                    remediation="Ensure Docker service is responsive and not frozen",
                ),
                False,
            )
        except OSError as exc:
            return (
                DoctorCheckResult(
                    name="Docker Daemon",
                    passed=False,
                    details=f"Error checking Docker daemon: {exc}",
                    remediation="Verify Docker daemon access permissions and named pipe socket",
                ),
                False,
            )

    def check_docker_engine_mode(self, daemon_active: bool) -> DoctorCheckResult:
        """Verify Docker is operating in Linux containers mode (required for sandbox)."""
        if not daemon_active:
            return DoctorCheckResult(
                name="Docker Engine Mode",
                passed=False,
                details="Skipped (Docker daemon is offline)",
                remediation="Start Docker daemon in Linux containers mode",
            )

        docker_path = shutil.which("docker") or "docker"
        try:
            res = subprocess.run(
                [docker_path, "info", "--format", "{{.OSType}}"],
                shell=False,
                capture_output=True,
                text=True,
                check=False,
                timeout=self.timeout_seconds,
            )
            if res.returncode == 0:
                os_type = res.stdout.strip().lower()
                if os_type == "linux":
                    return DoctorCheckResult(
                        name="Docker Engine Mode",
                        passed=True,
                        details="Operating in Linux containers mode",
                    )
                return DoctorCheckResult(
                    name="Docker Engine Mode",
                    passed=False,
                    details=f"Operating in '{os_type}' containers mode (Linux required)",
                    remediation=(
                        "Switch Docker Desktop to Linux containers mode "
                        "(right-click Docker tray icon -> 'Switch to Linux containers...')"
                    ),
                )
            return DoctorCheckResult(
                name="Docker Engine Mode",
                passed=False,
                details=f"Failed to query engine mode: {res.stderr.strip()}",
                remediation="Verify Docker engine status",
            )
        except (subprocess.TimeoutExpired, OSError) as exc:
            return DoctorCheckResult(
                name="Docker Engine Mode",
                passed=False,
                details=f"Error querying engine mode: {exc}",
                remediation="Verify Docker daemon responsiveness",
            )

    def check_runner_image_exists(
        self,
        image_tag: str,
        daemon_active: bool,
    ) -> tuple[DoctorCheckResult, bool]:
        """Verify the configured runner container image exists locally."""
        if not daemon_active:
            return (
                DoctorCheckResult(
                    name="Runner Image Presence",
                    passed=False,
                    details=f"Skipped for '{image_tag}' (Docker daemon is offline)",
                    remediation="Start Docker daemon, then run 'patchproof setup-runner'",
                ),
                False,
            )

        docker_path = shutil.which("docker") or "docker"
        try:
            res = subprocess.run(
                [docker_path, "image", "inspect", image_tag, "--format", "{{.Id}}"],
                shell=False,
                capture_output=True,
                text=True,
                check=False,
                timeout=self.timeout_seconds,
            )
            if res.returncode == 0:
                image_id = res.stdout.strip()[:19]  # e.g. sha256:abcd...
                return (
                    DoctorCheckResult(
                        name="Runner Image Presence",
                        passed=True,
                        details=f"Image '{image_tag}' found locally ({image_id})",
                    ),
                    True,
                )
            return (
                DoctorCheckResult(
                    name="Runner Image Presence",
                    passed=False,
                    details=f"Image '{image_tag}' not found in local Docker cache",
                    remediation=(
                        f"Build the runner image by running: "
                        f"patchproof setup-runner --tag {image_tag}"
                    ),
                ),
                False,
            )
        except (subprocess.TimeoutExpired, OSError) as exc:
            return (
                DoctorCheckResult(
                    name="Runner Image Presence",
                    passed=False,
                    details=f"Error inspecting image '{image_tag}': {exc}",
                    remediation="Verify Docker daemon responsiveness",
                ),
                False,
            )

    def check_runner_image_compatibility(
        self,
        image_tag: str,
        image_exists: bool,
    ) -> DoctorCheckResult:
        """Verify runner image compatibility (architecture, unprivileged user, pytest)."""
        if not image_exists:
            return DoctorCheckResult(
                name="Runner Image Compatibility",
                passed=False,
                details=f"Skipped for '{image_tag}' (image does not exist)",
                remediation=f"Run 'patchproof setup-runner --tag {image_tag}' to build the image",
            )

        docker_path = shutil.which("docker") or "docker"
        try:
            # Query architecture and labels
            inspect_res = subprocess.run(
                [
                    docker_path,
                    "image",
                    "inspect",
                    image_tag,
                    "--format",
                    '{{.Architecture}}|{{index .Config.Labels "patchproof.runner"}}',
                ],
                shell=False,
                capture_output=True,
                text=True,
                check=False,
                timeout=self.timeout_seconds,
            )
            arch_info = ""
            if inspect_res.returncode == 0:
                parts = inspect_res.stdout.strip().split("|")
                arch = parts[0] if parts else "unknown"
                arch_info = f"arch={arch}"

            # Probe pytest inside container with strict sandbox flags
            probe_cmd = [
                docker_path,
                "run",
                "--rm",
                "--network",
                "none",
                "--user",
                "10001:10001",
                "--cap-drop",
                "ALL",
                "--security-opt",
                "no-new-privileges",
                "--read-only",
                image_tag,
                "pytest",
                "--version",
            ]
            run_res = subprocess.run(
                probe_cmd,
                shell=False,
                capture_output=True,
                text=True,
                check=False,
                timeout=self.timeout_seconds * 2,
            )
            if run_res.returncode == 0:
                pytest_ver = run_res.stdout.strip().splitlines()[0] if run_res.stdout else "pytest"
                detail_str = f"Compatible ({arch_info}, {pytest_ver}, user 10001)"
                return DoctorCheckResult(
                    name="Runner Image Compatibility",
                    passed=True,
                    details=detail_str,
                )

            err_msg = run_res.stderr.strip() or f"exit code {run_res.returncode}"
            return DoctorCheckResult(
                name="Runner Image Compatibility",
                passed=False,
                details=f"Test runner execution failed: {err_msg}",
                remediation=(
                    f"Rebuild the image with: patchproof setup-runner --tag {image_tag} --no-cache"
                ),
            )
        except (subprocess.TimeoutExpired, OSError) as exc:
            return DoctorCheckResult(
                name="Runner Image Compatibility",
                passed=False,
                details=f"Error probing image compatibility: {exc}",
                remediation="Ensure Docker daemon can execute lightweight container checks",
            )

    def run_all_checks(self, image_tag: str = DEFAULT_RUNNER_TAG) -> list[DoctorCheckResult]:
        """Execute all 7 environment checks sequentially."""
        results: list[DoctorCheckResult] = []

        # 1. Python version
        results.append(self.check_python_version())

        # 2. Git CLI
        results.append(self.check_git_cli())

        # 3. Docker CLI
        results.append(self.check_docker_cli())

        # 4. Docker Daemon
        daemon_res, daemon_active = self.check_docker_daemon()
        results.append(daemon_res)

        # 5. Docker Engine Mode
        results.append(self.check_docker_engine_mode(daemon_active))

        # 6. Runner Image Presence
        image_res, image_exists = self.check_runner_image_exists(image_tag, daemon_active)
        results.append(image_res)

        # 7. Runner Image Compatibility
        results.append(self.check_runner_image_compatibility(image_tag, image_exists))

        return results

    def auto_remediate(self, image_tag: str = DEFAULT_RUNNER_TAG) -> list[str]:
        """Automatically remediate repairable environment issues where possible."""
        actions: list[str] = []
        _, daemon_active = self.check_docker_daemon()
        if not daemon_active:
            actions.append("Docker daemon is offline; cannot auto-remediate container image.")
            return actions

        _, image_exists = self.check_runner_image_exists(image_tag, daemon_active=True)
        if not image_exists:
            from patchproof.sandbox.setup_runner import build_runner_image

            try:
                build_runner_image(image_tag=image_tag)
                actions.append(f"Successfully built runner container image '{image_tag}'.")
            except Exception as exc:
                actions.append(f"Failed to auto-build runner image '{image_tag}': {exc}")
        else:
            actions.append(f"Runner container image '{image_tag}' is already present.")

        return actions

    def format_report(
        self,
        results: list[DoctorCheckResult],
        json_output: bool = False,
    ) -> str:
        """Format check results as human-readable text or structured JSON."""
        if json_output:
            data = {
                "system": {
                    "os": platform.system(),
                    "release": platform.release(),
                    "machine": platform.machine(),
                },
                "all_passed": all(r.passed for r in results),
                "checks": [r.model_dump() for r in results],
            }
            return json.dumps(data, indent=2)

        lines: list[str] = [
            "================================================================================",
            "                        PatchProof System Doctor                                ",
            "================================================================================",
            f"Host OS: {platform.system()} {platform.release()} ({platform.machine()})",
            "",
        ]

        all_passed = True
        failed_checks: list[DoctorCheckResult] = []

        for r in results:
            symbol = "[PASS]" if r.passed else "[FAIL]"
            lines.append(f"{symbol} {r.name:<30} {r.details}")
            if not r.passed:
                all_passed = False
                failed_checks.append(r)

        lines.append("")
        if all_passed:
            lines.append("RESULT: All prerequisites passed! Environment is ready for PatchProof.")
        else:
            lines.append(
                f"RESULT: {len(failed_checks)} prerequisite check(s) failed. "
                "Actionable remediation required:\n"
            )
            for fc in failed_checks:
                lines.append(f"  * {fc.name}:")
                lines.append(f"    Issue:       {fc.details}")
                if fc.remediation:
                    lines.append(f"    Remediation: {fc.remediation}")
                lines.append("")

        lines.append(
            "================================================================================"
        )
        return "\n".join(lines)
