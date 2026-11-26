"""ContainerRunner and lifecycle management subsystem for isolated container execution."""

from __future__ import annotations

import json
import logging
import subprocess
import threading
import time
import uuid
from typing import IO, Any

from patchproof.exceptions import (
    DockerUnavailableError,
    SandboxSecurityError,
    UnsupportedDockerEngineError,
)
from patchproof.models.sandbox import (
    ContainerConfig,
    ExecutionResult,
    SandboxFailureReason,
)
from patchproof.sandbox.docker_cli import (
    DockerCommandBuilder,
    DockerPreflight,
)

logger = logging.getLogger(__name__)

DEFAULT_MAX_OUTPUT_BYTES = 4 * 1024 * 1024  # 4 MB
KILL_TIMEOUT_SECONDS = 5.0
INSPECT_TIMEOUT_SECONDS = 5.0
CLEANUP_TIMEOUT_SECONDS = 5.0


def is_docker_available() -> bool:
    """Check if Docker daemon is available and operating in Linux Containers mode."""
    try:
        DockerPreflight().check()
        return True
    except (DockerUnavailableError, UnsupportedDockerEngineError):
        return False


class StreamCapture:
    """Captures an output stream in memory up to a bounded size limit."""

    def __init__(self, stream: IO[bytes] | None, max_bytes: int) -> None:
        self.stream = stream
        self.max_bytes = max_bytes
        self.chunks: list[bytes] = []
        self.total_bytes = 0
        self.truncated = False
        self.thread = threading.Thread(target=self._read_worker, daemon=True)

    def start(self) -> None:
        """Start reading the stream in a background daemon thread."""
        self.thread.start()

    def join(self, timeout: float = 2.0) -> None:
        """Wait for the reader thread to finish reading."""
        self.thread.join(timeout=timeout)

    def _read_worker(self) -> None:
        if self.stream is None:
            return

        chunk_size = 64 * 1024
        try:
            while True:
                chunk = self.stream.read(chunk_size)
                if not chunk:
                    break

                if self.total_bytes + len(chunk) > self.max_bytes:
                    remaining = self.max_bytes - self.total_bytes
                    if remaining > 0:
                        self.chunks.append(chunk[:remaining])
                        self.total_bytes += remaining
                    self.truncated = True
                    # Drain remaining stream to prevent child process write deadlocks
                    while self.stream.read(chunk_size):
                        pass
                    break

                self.chunks.append(chunk)
                self.total_bytes += len(chunk)
        except (ValueError, OSError):
            pass

    def get_output(self) -> str:
        """Return the collected stream content decoded as UTF-8 string."""
        raw = b"".join(self.chunks)
        text = raw.decode("utf-8", errors="replace")
        if self.truncated:
            text += "\n[TRUNCATED: Output exceeded limit]"
        return text


class ContainerRunner:
    """Manages the lifecycle, execution, timeout, inspection, and cleanup of test containers."""

    def __init__(
        self,
        max_output_bytes: int = DEFAULT_MAX_OUTPUT_BYTES,
        preflight: DockerPreflight | None = None,
        session_id: str | None = None,
    ) -> None:
        self.max_output_bytes = max_output_bytes
        self.preflight = preflight or DockerPreflight()
        self.session_id = session_id or uuid.uuid4().hex[:8]

    def run(self, config: ContainerConfig) -> ExecutionResult:
        """Execute a test container according to the given configuration and monitor lifecycle.

        Raises:
            DockerUnavailableError: If Docker daemon is unavailable or not Linux mode.
            SandboxSecurityError: If container command security validation fails.
        """
        # 1. Preflight health verification (fail closed)
        self.preflight.check()

        # 2. Generate unique execution ID and container name
        execution_id = uuid.uuid4().hex[:12]
        container_name = f"patchproof-exec-{execution_id}"
        timestamp = str(int(time.time()))

        extra_labels = {
            "patchproof.session_id": self.session_id,
            "patchproof.timestamp": timestamp,
        }

        # 3. Deterministically build hardened Docker CLI command
        # (without auto-remove to permit post-exit state inspection)
        builder = DockerCommandBuilder(config)
        cmd = builder.build(
            container_name=container_name,
            execution_id=execution_id,
            auto_remove=False,
            extra_labels=extra_labels,
        )

        timeout_seconds = config.resources.timeout_seconds
        start_time = time.monotonic()
        timeout_triggered = False
        oom_killed = False
        exit_code = -1
        stdout_text = ""
        stderr_text = ""

        proc: subprocess.Popen[bytes] | None = None
        stdout_capture: StreamCapture | None = None
        stderr_capture: StreamCapture | None = None

        try:
            # 4. Spawn container process with close_fds=True and shell=False
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                shell=False,
                close_fds=True,
            )

            stdout_capture = StreamCapture(proc.stdout, self.max_output_bytes)
            stderr_capture = StreamCapture(proc.stderr, self.max_output_bytes)
            stdout_capture.start()
            stderr_capture.start()

            # 5. Host-monitored execution loop
            while True:
                poll_res = proc.poll()
                if poll_res is not None:
                    exit_code = poll_res
                    break

                elapsed = time.monotonic() - start_time
                if elapsed >= timeout_seconds:
                    timeout_triggered = True
                    self._kill_container(container_name)
                    try:
                        exit_code = proc.wait(timeout=2.0)
                    except subprocess.TimeoutExpired:
                        proc.kill()
                        exit_code = proc.wait(timeout=1.0)
                    break

                time.sleep(0.05)

            # 6. Finalize output capture
            stdout_capture.join(timeout=2.0)
            stderr_capture.join(timeout=2.0)
            stdout_text = stdout_capture.get_output()
            stderr_text = stderr_capture.get_output()

            # 7. Inspect Docker exit-state to classify OOM vs Timeout vs Execution Error
            oom_killed = self._inspect_oom_killed(container_name)

        finally:
            # 8. Defensive host child process reaping
            if proc is not None:
                try:
                    if proc.returncode is None:
                        proc.kill()
                        proc.wait(timeout=1.0)
                except Exception:
                    pass

            # 9. Guaranteed container cleanup in finally block
            self.cleanup_container(container_name)

        duration_seconds = max(0.0, time.monotonic() - start_time)

        # 9. Determine failure reason
        if oom_killed:
            failure_reason = SandboxFailureReason.OOM_KILLED
        elif timeout_triggered:
            failure_reason = SandboxFailureReason.TIMEOUT
        elif exit_code != 0:
            failure_reason = SandboxFailureReason.EXECUTION_ERROR
        else:
            failure_reason = SandboxFailureReason.NONE

        return ExecutionResult(
            exit_code=exit_code,
            stdout=stdout_text,
            stderr=stderr_text,
            duration_seconds=round(duration_seconds, 3),
            failure_reason=failure_reason,
            oom_killed=oom_killed,
            timeout_triggered=timeout_triggered,
        )

    def _kill_container(self, container_name: str) -> None:
        """Kill a running container by name."""
        try:
            subprocess.run(
                ["docker", "kill", container_name],
                capture_output=True,
                text=True,
                timeout=KILL_TIMEOUT_SECONDS,
                shell=False,
                close_fds=True,
                check=False,
            )
        except (subprocess.TimeoutExpired, OSError) as err:
            logger.warning("Failed to kill container %s: %s", container_name, err)

    def _inspect_oom_killed(self, container_name: str) -> bool:
        """Inspect container state via Docker CLI to determine if process was OOM-killed."""
        try:
            res = subprocess.run(
                ["docker", "inspect", container_name, "--format", "{{json .State}}"],
                capture_output=True,
                text=True,
                timeout=INSPECT_TIMEOUT_SECONDS,
                shell=False,
                close_fds=True,
                check=False,
            )
            if res.returncode == 0 and res.stdout.strip():
                state: dict[str, Any] = json.loads(res.stdout.strip())
                return bool(state.get("OOMKilled", False))
        except (subprocess.TimeoutExpired, OSError, json.JSONDecodeError) as err:
            logger.warning("State inspection failed for container %s: %s", container_name, err)
        return False

    def cleanup_container(self, container_name: str) -> None:
        """Verify PatchProof ownership and remove the container.

        Raises:
            SandboxSecurityError: If container is not managed by PatchProof.
        """
        # Step 1: Verify container ownership
        try:
            res = subprocess.run(
                [
                    "docker",
                    "inspect",
                    container_name,
                    "--format",
                    '{{index .Config.Labels "patchproof.managed"}}',
                ],
                capture_output=True,
                text=True,
                timeout=INSPECT_TIMEOUT_SECONDS,
                shell=False,
                close_fds=True,
                check=False,
            )
        except (subprocess.TimeoutExpired, OSError):
            return

        if res.returncode != 0:
            # Container already removed or does not exist
            return

        label_val = res.stdout.strip().lower()
        if label_val != "true":
            raise SandboxSecurityError(
                f"Refusing to clean up container '{container_name}': not managed by PatchProof"
            )

        # Step 2: Remove verified container
        try:
            subprocess.run(
                ["docker", "rm", "-f", container_name],
                capture_output=True,
                text=True,
                timeout=CLEANUP_TIMEOUT_SECONDS,
                shell=False,
                close_fds=True,
                check=False,
            )
        except (subprocess.TimeoutExpired, OSError) as err:
            logger.warning("Container removal failed for %s: %s", container_name, err)


class OrphanSweeper:
    """Safe detector and sweeper for orphaned PatchProof execution containers."""

    def __init__(self, timeout_seconds: float = 10.0) -> None:
        self.timeout_seconds = timeout_seconds

    def cleanup_orphaned_containers(self, max_age_seconds: float = 0.0) -> list[str]:
        """Discover and terminate orphaned PatchProof containers.

        Strictly targets containers bearing 'patchproof.managed=true'.
        Never issues broad pruning commands like 'docker system prune'.
        """
        try:
            res = subprocess.run(
                [
                    "docker",
                    "ps",
                    "-a",
                    "--filter",
                    "label=patchproof.managed=true",
                    "--format",
                    "{{.ID}}\t{{.Names}}\t{{.CreatedAt}}",
                ],
                capture_output=True,
                text=True,
                timeout=self.timeout_seconds,
                shell=False,
                close_fds=True,
                check=False,
            )
        except (subprocess.TimeoutExpired, OSError) as err:
            logger.warning("Orphan scan failed: %s", err)
            return []

        if res.returncode != 0 or not res.stdout.strip():
            return []

        cleaned_containers: list[str] = []
        for line in res.stdout.strip().splitlines():
            parts = line.strip().split("\t")
            if not parts:
                continue
            container_id = parts[0].strip()
            if not container_id:
                continue

            # Verify ownership and metadata independently before destruction
            try:
                verify_res = subprocess.run(
                    [
                        "docker",
                        "inspect",
                        container_id,
                        "--format",
                        '{{index .Config.Labels "patchproof.managed"}}\t'
                        '{{index .Config.Labels "patchproof.timestamp"}}\t'
                        "{{.State.Status}}",
                    ],
                    capture_output=True,
                    text=True,
                    timeout=INSPECT_TIMEOUT_SECONDS,
                    shell=False,
                    close_fds=True,
                    check=False,
                )
                if verify_res.returncode != 0:
                    continue

                inspect_parts = verify_res.stdout.strip().split("\t")
                if len(inspect_parts) < 3:
                    continue

                managed_label = inspect_parts[0].strip().lower()
                timestamp_str = inspect_parts[1].strip()
                status = inspect_parts[2].strip().lower()

                if managed_label != "true":
                    continue

                current_time = time.time()
                container_ts = float(timestamp_str) if timestamp_str.isdigit() else 0.0

                if max_age_seconds > 0.0:
                    # Skip container if younger than max_age_seconds
                    if container_ts > 0.0 and (current_time - container_ts) < max_age_seconds:
                        continue
                else:
                    # When max_age_seconds is 0.0, protect actively running containers
                    if status == "running":
                        continue

                rm_res = subprocess.run(
                    ["docker", "rm", "-f", container_id],
                    capture_output=True,
                    text=True,
                    timeout=CLEANUP_TIMEOUT_SECONDS,
                    shell=False,
                    close_fds=True,
                    check=False,
                )
                if rm_res.returncode == 0:
                    cleaned_containers.append(container_id)
            except (subprocess.TimeoutExpired, OSError) as err:
                logger.warning("Failed cleaning orphan %s: %s", container_id, err)

        return cleaned_containers
