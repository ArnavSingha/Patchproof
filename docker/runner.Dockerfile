# PatchProof Standard Python 3.11 Test Runner
# Version: 0.1.0-m4.1
# Base Image: python:3.11-slim-bookworm (pinned Debian Bookworm)
FROM python:3.11-slim-bookworm

# Metadata labels
LABEL org.opencontainers.image.title="PatchProof Python Test Runner"
LABEL org.opencontainers.image.description="Minimal, hardened container image for isolated Python test execution"
LABEL org.opencontainers.image.version="0.1.0-m4.1"
LABEL patchproof.runner=true
LABEL patchproof.version="0.1.0"
LABEL patchproof.python="3.11"

# Environment: Disable bytecode caching and unbuffer output
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    HOME=/home/patchproof

# Install core test execution dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && pip install --no-cache-dir \
        pytest>=8.0.0 \
        pytest-timeout \
        pytest-xdist

# Security: Create unprivileged user and group with UID/GID 10001
RUN groupadd -g 10001 patchproof \
    && useradd -u 10001 -g 10001 -m -s /bin/bash -d /home/patchproof patchproof \
    && mkdir -p /workspace /tmp \
    && chown -R patchproof:patchproof /workspace /tmp /home/patchproof \
    && chmod 1777 /tmp /workspace

# Switch to unprivileged user
USER 10001:10001
WORKDIR /workspace

# Default command
CMD ["pytest", "-q"]
