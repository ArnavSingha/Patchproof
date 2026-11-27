"""Docker CLI command builder, environment sanitizer, and preflight verification."""

from __future__ import annotations

import pathlib
import shutil
import subprocess
import tempfile

from pydantic import BaseModel, ConfigDict, Field

from patchproof.exceptions import (
    DockerUnavailableError,
    SandboxSecurityError,
    UnsupportedDockerEngineError,
)
from patchproof.models.sandbox import (
    ALLOWLISTED_EXACT_VARS,
    ALLOWLISTED_PREFIXES,
    BROAD_ROOT_PATHS,
    DISALLOWED_ENV_PATTERNS,
    FORBIDDEN_HOST_PATHS,
    ContainerConfig,
    ContainerEnvironment,
    NetworkMode,
)

DEFAULT_SAFE_ENV: dict[str, str] = {
    "PATH": "/usr/local/bin:/usr/local/sbin:/usr/bin:/usr/sbin:/bin:/sbin",
    "LANG": "C.UTF-8",
    "LC_ALL": "C.UTF-8",
    "PYTHONUNBUFFERED": "1",
    "PYTHONDONTWRITEBYTECODE": "1",
    "PYTHONPATH": "/workspace",
}


class DockerPreflightResult(BaseModel):
    """Result of Docker preflight health and engine mode verification."""

    model_config = ConfigDict(frozen=True)

    is_available: bool = Field(description="True if Docker daemon is active and responsive")
    os_type: str = Field(description="Docker daemon operating system type (must be 'linux')")
    binary_path: str = Field(description="Resolved path to docker CLI executable")


class EnvironmentSanitizer:
    """Sanitizes environment variables to prevent host credential leakage."""

    def __init__(self) -> None:
        pass

    def validate_env_pair(self, key: str, val: str) -> None:
        """Validate an individual environment variable key-value pair.

        Raises:
            SandboxSecurityError: If key matches any sensitive pattern or is not allowlisted.
        """
        key_upper = key.upper()
        for pattern in DISALLOWED_ENV_PATTERNS:
            if pattern in key_upper:
                raise SandboxSecurityError(
                    f"Environment variable '{key}' violates credential isolation policy"
                )

        if key not in ALLOWLISTED_EXACT_VARS and not any(
            key.startswith(p) for p in ALLOWLISTED_PREFIXES
        ):
            raise SandboxSecurityError(
                f"Environment variable '{key}' violates credential isolation policy "
                "(not in allowlist)"
            )

    def sanitize(self, custom_env: ContainerEnvironment | None = None) -> dict[str, str]:
        """Construct an isolated environment dictionary from default safe variables.

        Never inherits host os.environ.
        """
        env = dict(DEFAULT_SAFE_ENV)
        if custom_env is not None:
            for k, v in custom_env.env_vars:
                self.validate_env_pair(k, v)
                env[k] = v
        return env


class DockerCommandBuilder:
    """Deterministically builds and verifies hardened docker run argument vectors."""

    def __init__(self, config: ContainerConfig) -> None:
        self.config = config
        self.sanitizer = EnvironmentSanitizer()

    def canonicalize_host_path(self, path_str: str) -> str:
        """Resolve and canonicalize host repository path, enforcing security boundaries."""
        p = pathlib.Path(path_str).resolve()
        if not p.exists() or not p.is_dir():
            raise SandboxSecurityError(
                f"Host repository path does not exist or is not a directory: {path_str}"
            )

        normalized = str(p).replace("\\", "/").lower().rstrip("/")
        if normalized in BROAD_ROOT_PATHS:
            raise SandboxSecurityError(f"Broad root host path prohibited from mount: {path_str}")

        for forbidden in FORBIDDEN_HOST_PATHS:
            if forbidden in normalized:
                raise SandboxSecurityError(f"Sensitive host path prohibited from mount: {path_str}")

        return str(p)

    def build(
        self,
        container_name: str | None = None,
        execution_id: str | None = None,
        auto_remove: bool = True,
        extra_labels: dict[str, str] | None = None,
    ) -> list[str]:
        """Construct the complete docker run argument list."""
        canonical_host_path = self.canonicalize_host_path(self.config.host_repo_path)
        sanitized_env = self.sanitizer.sanitize(self.config.environment)

        workspace = self.config.container_workspace_path

        # Check if canonical_host_path is an ephemeral staging directory
        is_staging = False
        try:
            resolved_host = pathlib.Path(canonical_host_path).resolve()
            temp_root = pathlib.Path(tempfile.gettempdir()).resolve()
            is_staging = resolved_host.is_relative_to(temp_root) and resolved_host.name.startswith(
                "patchproof-staging-"
            )
        except (ValueError, OSError):
            is_staging = False

        bind_readonly = ",readonly=true" if not is_staging else ""

        cmd: list[str] = [
            "docker",
            "run",
        ]
        if auto_remove:
            cmd.append("--rm")

        if container_name:
            cmd.extend(["--name", container_name])

        if self.config.security.network_mode == NetworkMode.INTERNAL_BRIDGE:
            net_name = self.config.security.network_name or self.config.network.network_name
            if not net_name or not net_name.startswith("patchproof-net-"):
                raise SandboxSecurityError(
                    "NetworkMode.INTERNAL_BRIDGE requires a verified "
                    "'patchproof-net-*' network name"
                )
            network_arg = net_name
        else:
            network_arg = NetworkMode.NONE.value

        cmd.extend(
            [
                "--network",
                network_arg,
                "--user",
                self.config.security.user,
                "--cap-drop",
                "ALL",
                "--security-opt",
                "no-new-privileges",
                "--read-only",
                "--cpus",
                str(self.config.resources.cpu_count),
                "--memory",
                f"{self.config.resources.memory_limit_mb}m",
                "--memory-swap",
                f"{self.config.resources.memory_limit_mb}m",
                "--pids-limit",
                str(self.config.resources.pids_limit),
                "--mount",
                f"type=bind,source={canonical_host_path},target={workspace}{bind_readonly}",
                "--mount",
                f"type=tmpfs,destination={workspace}/.git,tmpfs-size=1k,tmpfs-mode=000",
                "--mount",
                "type=tmpfs,destination=/tmp,tmpfs-size=64m,tmpfs-mode=1777",
            ]
        )

        for k, v in sorted(sanitized_env.items()):
            cmd.extend(["-e", f"{k}={v}"])

        cmd.extend(["-w", workspace, "--label", "patchproof.managed=true"])

        if execution_id:
            cmd.extend(["--label", f"patchproof.execution_id={execution_id}"])

        if extra_labels:
            for lk, lv in sorted(extra_labels.items()):
                cmd.extend(["--label", f"{lk}={lv}"])

        cmd.extend(
            [
                self.config.image,
                *self.config.entrypoint_command,
            ]
        )

        self.verify_command_security(cmd)
        return cmd

    def verify_command_security(self, cmd: list[str]) -> bool:
        """Verify that all required security flags are present in the argument vector."""
        if not (len(cmd) >= 3 and cmd[0] == "docker" and cmd[1] == "run"):
            raise SandboxSecurityError("Command must start with 'docker run'")

        required_flags = [
            "--network",
            "--user",
            "--cap-drop",
            "--security-opt",
            "--read-only",
            "--cpus",
            "--memory",
            "--memory-swap",
            "--pids-limit",
        ]
        for flag in required_flags:
            if flag not in cmd:
                raise SandboxSecurityError(f"Missing required security flag: {flag}")

        net_idx = cmd.index("--network")
        if net_idx + 1 >= len(cmd):
            raise SandboxSecurityError("Missing argument for --network")
        net_arg = cmd[net_idx + 1]
        if net_arg != NetworkMode.NONE.value and not net_arg.startswith("patchproof-net-"):
            raise SandboxSecurityError(
                f"Invalid network mode '{net_arg}' in command: violates sandbox isolation policy. "
                "Only 'none' or 'patchproof-net-*' internal networks are permitted."
            )

        user_idx = cmd.index("--user")
        if user_idx + 1 >= len(cmd):
            raise SandboxSecurityError("Missing user value in command")
        user_val = cmd[user_idx + 1].split(":", 1)[0].strip()
        if user_val == "root" or (user_val.isdigit() and int(user_val) == 0):
            raise SandboxSecurityError("Root user specified in command")

        cap_idx = cmd.index("--cap-drop")
        if cap_idx + 1 >= len(cmd) or cmd[cap_idx + 1] != "ALL":
            raise SandboxSecurityError("Invalid cap-drop in command: must be 'ALL'")

        sec_idx = cmd.index("--security-opt")
        if sec_idx + 1 >= len(cmd) or cmd[sec_idx + 1] != "no-new-privileges":
            raise SandboxSecurityError("Missing 'no-new-privileges' security option")

        if not any("/.git" in arg and "type=tmpfs" in arg for arg in cmd):
            raise SandboxSecurityError("Missing git credential mask tmpfs mount")

        if not any("patchproof.managed=true" in arg for arg in cmd):
            raise SandboxSecurityError("Missing required label 'patchproof.managed=true'")

        return True


class DockerPreflight:
    """Pre-flight verification for Docker daemon availability and engine mode."""

    def __init__(self, timeout_seconds: float = 2.0) -> None:
        self.timeout_seconds = timeout_seconds

    def check(self) -> DockerPreflightResult:
        """Verify Docker is available, responsive, and operating in Linux Containers mode.

        Raises:
            DockerUnavailableError: If Docker binary is missing, unresponsive, or timed out.
            UnsupportedDockerEngineError: If Docker is in Windows Containers mode.
        """
        which_path = shutil.which("docker")
        if not which_path:
            raise DockerUnavailableError("Docker binary not found in system PATH")

        try:
            res = subprocess.run(
                ["docker", "info", "--format", "{{.OSType}}"],
                capture_output=True,
                text=True,
                timeout=self.timeout_seconds,
                shell=False,
                close_fds=True,
            )
        except FileNotFoundError as err:
            raise DockerUnavailableError(f"Docker binary execution failed: {err}") from err
        except subprocess.TimeoutExpired as err:
            raise DockerUnavailableError(
                f"Docker daemon probe timed out after {self.timeout_seconds}s"
            ) from err

        if res.returncode != 0:
            err_msg = res.stderr.strip() or f"exit code {res.returncode}"
            raise DockerUnavailableError(f"Docker daemon is not responsive: {err_msg}")

        os_type = res.stdout.strip().lower()
        if os_type != "linux":
            raise UnsupportedDockerEngineError(
                f"Docker Desktop must be running in Linux Containers mode (WSL2 backend), "
                f"got '{os_type}'"
            )

        return DockerPreflightResult(
            is_available=True,
            os_type=os_type,
            binary_path=which_path,
        )


PROHIBITED_COMMAND_METASYMBOLS = (
    ";",
    "&",
    "|",
    "`",
    "$",
    ">",
    "<",
    "\n",
    "\r",
)


def parse_test_command(cmd_str: str) -> tuple[str, ...]:
    """Parse and validate a custom test command string into an argv tuple.

    Enforces security boundary checks against shell injection.

    Raises:
        SandboxSecurityError: If command is empty, contains shell metacharacters, or is malformed.
    """
    import shlex

    trimmed = cmd_str.strip()
    if not trimmed:
        raise SandboxSecurityError("Custom test command cannot be empty")

    for symbol in PROHIBITED_COMMAND_METASYMBOLS:
        if symbol in trimmed:
            raise SandboxSecurityError(
                f"Custom test command contains prohibited character '{symbol}'"
            )

    try:
        parts = shlex.split(trimmed)
    except ValueError as err:
        raise SandboxSecurityError(f"Invalid shell quoting in test command: {err}") from err

    if not parts:
        raise SandboxSecurityError("Custom test command cannot be empty")

    return tuple(parts)
