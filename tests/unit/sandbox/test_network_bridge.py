"""Unit tests for Dual-Mode Ephemeral Bridge Sandbox and network isolation."""

from __future__ import annotations

import pathlib
import subprocess
from unittest.mock import patch

import pytest

from patchproof.exceptions import SandboxSecurityError
from patchproof.models.sandbox import (
    ContainerConfig,
    NetworkMode,
    SandboxNetworkConfig,
    SecurityPolicy,
)
from patchproof.sandbox.docker_cli import DockerCommandBuilder
from patchproof.sandbox.network_bridge import (
    EphemeralNetworkBridge,
    ephemeral_network_bridge,
)


def test_sandbox_network_config_default_none() -> None:
    """Default network config is NetworkMode.NONE without network name."""
    cfg = SandboxNetworkConfig()
    assert cfg.mode == NetworkMode.NONE
    assert cfg.network_name is None
    assert cfg.sidecars == ()


def test_sandbox_network_config_validates_prefix() -> None:
    """Network name must begin with 'patchproof-net-' prefix."""
    with pytest.raises(
        SandboxSecurityError, match="Custom network must begin with 'patchproof-net-'"
    ):
        SandboxNetworkConfig(
            mode=NetworkMode.INTERNAL_BRIDGE,
            network_name="malicious-network",
        )

    valid = SandboxNetworkConfig(
        mode=NetworkMode.INTERNAL_BRIDGE,
        network_name="patchproof-net-abc123",
    )
    assert valid.network_name == "patchproof-net-abc123"


def test_create_bridge_success() -> None:
    """EphemeralNetworkBridge creates and validates internal network."""
    bridge = EphemeralNetworkBridge()
    with patch("subprocess.run") as mock_run:
        # Mock create returns 0, inspect returns 'true'
        mock_run.side_effect = [
            subprocess.CompletedProcess(
                args=["docker", "network", "create"], returncode=0, stdout=""
            ),
            subprocess.CompletedProcess(
                args=["docker", "network", "inspect"], returncode=0, stdout="true\n"
            ),
        ]

        net_name = bridge.create_bridge()
        assert net_name.startswith("patchproof-net-")
        assert mock_run.call_count == 2
        # Check create args contain --internal
        create_args = mock_run.call_args_list[0][0][0]
        assert "--internal" in create_args


def test_create_bridge_fails_closed_if_not_internal() -> None:
    """If Docker inspection reveals network is not internal, fails closed and cleans up."""
    bridge = EphemeralNetworkBridge()
    with patch("subprocess.run") as mock_run:
        mock_run.side_effect = [
            subprocess.CompletedProcess(
                args=["docker", "network", "create"], returncode=0, stdout=""
            ),
            # Inspect returns 'false' (external egress possible)
            subprocess.CompletedProcess(
                args=["docker", "network", "inspect"], returncode=0, stdout="false\n"
            ),
            # Cleanup call
            subprocess.CompletedProcess(args=["docker", "network", "rm"], returncode=0, stdout=""),
        ]

        with pytest.raises(SandboxSecurityError, match=r"Network.*is not strictly internal"):
            bridge.create_bridge()

        # Must have attempted cleanup
        assert mock_run.call_count == 3
        rm_args = mock_run.call_args_list[2][0][0]
        assert "rm" in rm_args


def test_remove_bridge_refuses_non_patchproof_network() -> None:
    """Refuses to remove networks that do not bear the patchproof-net- prefix."""
    bridge = EphemeralNetworkBridge()
    with pytest.raises(SandboxSecurityError, match="Refusing to remove network"):
        bridge.remove_bridge("host")

    with pytest.raises(SandboxSecurityError, match="Refusing to remove network"):
        bridge.remove_bridge("bridge")


def test_remove_bridge_success() -> None:
    """Removes verified patchproof network."""
    bridge = EphemeralNetworkBridge()
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = subprocess.CompletedProcess(
            args=["docker", "network", "rm"], returncode=0, stdout=""
        )
        bridge.remove_bridge("patchproof-net-test123")
        assert mock_run.call_count == 1
        assert "patchproof-net-test123" in mock_run.call_args[0][0]


def test_ephemeral_network_context_manager() -> None:
    """Context manager guarantees network cleanup even on error."""
    with patch("subprocess.run") as mock_run:
        mock_run.side_effect = [
            # create
            subprocess.CompletedProcess(args=[], returncode=0, stdout=""),
            # inspect
            subprocess.CompletedProcess(args=[], returncode=0, stdout="true\n"),
            # rm
            subprocess.CompletedProcess(args=[], returncode=0, stdout=""),
        ]

        with pytest.raises(RuntimeError, match="test error"):
            with ephemeral_network_bridge() as net_name:
                assert net_name.startswith("patchproof-net-")
                raise RuntimeError("test error")

        # Cleanup rm was called
        assert mock_run.call_count == 3


def test_docker_command_builder_internal_bridge(tmp_path: pathlib.Path) -> None:
    """DockerCommandBuilder accepts verified patchproof internal network."""
    net_cfg = SandboxNetworkConfig(
        mode=NetworkMode.INTERNAL_BRIDGE,
        network_name="patchproof-net-test123",
    )
    sec = SecurityPolicy(
        network_mode=NetworkMode.INTERNAL_BRIDGE,
        network_name="patchproof-net-test123",
    )
    config = ContainerConfig(
        host_repo_path=str(tmp_path),
        security=sec,
        network=net_cfg,
    )
    builder = DockerCommandBuilder(config)
    cmd = builder.build()
    net_idx = cmd.index("--network")
    assert cmd[net_idx + 1] == "patchproof-net-test123"


def test_docker_command_builder_rejects_external_network(tmp_path: pathlib.Path) -> None:
    """DockerCommandBuilder rejects networks other than 'none' or 'patchproof-net-*'."""
    builder = DockerCommandBuilder(ContainerConfig(host_repo_path=str(tmp_path)))
    # Forged command with host network
    bad_cmd = [
        "docker",
        "run",
        "--rm",
        "--network",
        "host",
        "--user",
        "1000:1000",
        "--cap-drop",
        "ALL",
        "--security-opt",
        "no-new-privileges",
        "--read-only",
        "--cpus",
        "2",
        "--memory",
        "2048m",
        "--memory-swap",
        "2048m",
        "--pids-limit",
        "256",
    ]
    with pytest.raises(SandboxSecurityError, match="violates sandbox isolation policy"):
        builder.verify_command_security(bad_cmd)
