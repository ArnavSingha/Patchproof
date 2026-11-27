"""Dual-Mode Ephemeral Bridge Sandbox subsystem.

Manages isolated intra-sandbox Docker bridge networks allowing multi-service sidecars
(e.g., PostgreSQL, Redis) while strictly dropping external internet egress via kernel iptables.
"""

from __future__ import annotations

import logging
import subprocess
import uuid
from collections.abc import Iterator
from contextlib import contextmanager

from patchproof.exceptions import SandboxSecurityError

logger = logging.getLogger(__name__)

NETWORK_PREFIX = "patchproof-net-"
DEFAULT_DOCKER_TIMEOUT = 10.0


class EphemeralNetworkBridge:
    """Manages creation, verification, and teardown of ephemeral internal Docker networks."""

    def __init__(self, timeout_seconds: float = DEFAULT_DOCKER_TIMEOUT) -> None:
        self.timeout_seconds = timeout_seconds

    def create_bridge(self, network_name: str | None = None) -> str:
        """Create an ephemeral internal bridge network with zero external internet egress.

        Raises:
            SandboxSecurityError: If network creation fails or network is not strictly internal.
        """
        if network_name is None:
            name = f"{NETWORK_PREFIX}{uuid.uuid4().hex[:12]}"
        else:
            name = network_name

        if not name.startswith(NETWORK_PREFIX):
            raise SandboxSecurityError(
                f"Custom network name must begin with '{NETWORK_PREFIX}' prefix: {name}"
            )

        cmd = [
            "docker",
            "network",
            "create",
            "--internal",
            "--opt",
            "com.docker.network.bridge.enable_icc=true",
            name,
        ]

        try:
            res = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=self.timeout_seconds,
                shell=False,
                close_fds=True,
                check=False,
            )
            if res.returncode != 0:
                raise SandboxSecurityError(
                    f"Failed to create ephemeral bridge network '{name}': {res.stderr.strip()}"
                )
        except (subprocess.TimeoutExpired, OSError) as err:
            raise SandboxSecurityError(
                f"Failed to execute docker network create for '{name}': {err}"
            ) from err

        # Verify internal isolation guarantee via inspection
        if not self._verify_internal(name):
            self.remove_bridge(name)
            raise SandboxSecurityError(
                f"Network '{name}' is not strictly internal. Potential internet egress detected."
            )

        logger.info("Created ephemeral internal network bridge: %s", name)
        return name

    def _verify_internal(self, network_name: str) -> bool:
        """Inspect network and verify that Docker has configured it with internal=true."""
        inspect_cmd = [
            "docker",
            "network",
            "inspect",
            network_name,
            "--format",
            "{{.Internal}}",
        ]
        try:
            res = subprocess.run(
                inspect_cmd,
                capture_output=True,
                text=True,
                timeout=self.timeout_seconds,
                shell=False,
                close_fds=True,
                check=False,
            )
            if res.returncode == 0:
                return res.stdout.strip().lower() == "true"
        except (subprocess.TimeoutExpired, OSError) as err:
            logger.warning("Network inspection failed for %s: %s", network_name, err)
        return False

    def remove_bridge(self, network_name: str) -> None:
        """Safely remove a verified PatchProof ephemeral bridge network.

        Raises:
            SandboxSecurityError: If attempting to remove an unmanaged or host network.
        """
        if not network_name.startswith(NETWORK_PREFIX):
            raise SandboxSecurityError(
                f"Refusing to remove network '{network_name}': not managed by PatchProof"
            )

        rm_cmd = ["docker", "network", "rm", network_name]
        try:
            subprocess.run(
                rm_cmd,
                capture_output=True,
                text=True,
                timeout=self.timeout_seconds,
                shell=False,
                close_fds=True,
                check=False,
            )
            logger.info("Cleaned up ephemeral bridge network: %s", network_name)
        except (subprocess.TimeoutExpired, OSError) as err:
            logger.warning("Failed to remove network %s: %s", network_name, err)


@contextmanager
def ephemeral_network_bridge(network_name: str | None = None) -> Iterator[str]:
    """Context manager creating a strictly internal network bridge and guaranteeing cleanup."""
    bridge = EphemeralNetworkBridge()
    name = bridge.create_bridge(network_name)
    try:
        yield name
    finally:
        bridge.remove_bridge(name)
