"""Sandbox isolation and execution subsystem for PatchProof."""

from patchproof.sandbox.classifier import RegressionClassifier
from patchproof.sandbox.comparative import ComparativeRunner
from patchproof.sandbox.docker_cli import (
    DockerCommandBuilder,
    DockerPreflight,
    DockerPreflightResult,
    EnvironmentSanitizer,
    parse_test_command,
)
from patchproof.sandbox.doctor import DoctorCheckResult, SystemDoctor
from patchproof.sandbox.parser import PytestOutputParser
from patchproof.sandbox.runner import (
    ContainerRunner,
    OrphanSweeper,
    is_docker_available,
)
from patchproof.sandbox.setup_runner import DEFAULT_RUNNER_TAG, build_runner_image

__all__ = [
    "DEFAULT_RUNNER_TAG",
    "ComparativeRunner",
    "ContainerRunner",
    "DockerCommandBuilder",
    "DockerPreflight",
    "DockerPreflightResult",
    "DoctorCheckResult",
    "EnvironmentSanitizer",
    "OrphanSweeper",
    "PytestOutputParser",
    "RegressionClassifier",
    "SystemDoctor",
    "build_runner_image",
    "is_docker_available",
    "parse_test_command",
]
