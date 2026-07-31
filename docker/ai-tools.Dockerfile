# syntax=docker/dockerfile:1@sha256:87999aa3d42bdc6bea60565083ee17e86d1f3339802f543c0d03998580f9cb89
# =============================================================================
# Lintro AI Tools Base Image
# =============================================================================
# The agent CLIs lintro's `--transport cli` providers shell out to (`claude`,
# `codex`, Cursor's `agent`), pre-installed on top of the lintro-tools base so
# the lint-only image stays lean.
#
# Published as ghcr.io/lgtm-hq/lintro-ai-tools by
# .github/workflows/docker-ai-tools-publish.yml (cosign-signed, SBOM +
# provenance). Once the first image is published, the root Dockerfile gains an
# `ai` stage that consumes it via a digest-pinned FROM and copies
# /opt/ai-tools out wholesale; Renovate manages the digest bump from then on.
#
# FROM lintro-tools rather than a bare python base so the copied install tree
# is built against exactly the libc/OS layer it will be dropped into.
#
# Build context is the repository root:
#   docker build -f docker/ai-tools.Dockerfile .
#
# Version policy: the agent CLIs release close to daily, so Renovate batches
# these ARGs weekly (see renovate.json) and the hybrid capability guard
# (lintro/ai/providers/cli_transport.py, #1612) tolerates the lag. Bumping a
# CLI here does not change what lintro sends -- that contract lives in
# lintro/ai/providers/cli_contracts.py.
# =============================================================================

# yamllint / hadolint: pin is immutable by digest; tag is informational.
FROM ghcr.io/lgtm-hq/lintro-tools:latest@sha256:e7afee4616ca6e9426f97fdbb080417585f11b011e2f57f264657d8b4cc51d36 AS ai-tools

# Renovate: npm datasource for the two published CLIs, node-version for the
# runtime. CURSOR_AGENT_VERSION has no Renovate datasource -- Cursor ships the
# agent CLI only as a tarball behind https://cursor.com/install, with no
# registry or release feed to query -- so it is bumped by hand; the weekly
# rebuild keeps everything around it fresh regardless.
ARG NODE_VERSION=24.18.0
ARG CLAUDE_CODE_VERSION=2.1.220
ARG CODEX_VERSION=0.145.0
ARG CURSOR_AGENT_VERSION=2026.07.23-e383d2b
# Cursor ships no checksum sidecar, so both architectures' hashes are pinned
# here by hand and bumped together with CURSOR_AGENT_VERSION. Recompute with:
#   curl -fsSL https://downloads.cursor.com/lab/<version>/linux/<arch>/agent-cli-package.tar.gz | sha256sum
ARG CURSOR_AGENT_SHA256_X64=702ad595213bee5df0268be9f80a19f29fcceaa2a42fc55e39f2b5199051f0c4
ARG CURSOR_AGENT_SHA256_ARM64=f40b99647cb24e0da885e97620a2048034f1fe8961910d573d827d77c4d26dcb

LABEL maintainer="lgtm-hq"
LABEL org.opencontainers.image.source="https://github.com/lgtm-hq/py-lintro"
LABEL org.opencontainers.image.description="Pre-built AI agent CLIs for lintro --transport cli"
LABEL org.opencontainers.image.licenses="MIT"

ENV AI_TOOLS_PREFIX="/opt/ai-tools" \
    PATH="/opt/ai-tools/bin:${PATH}"

SHELL ["/bin/bash", "-o", "pipefail", "-c"]

COPY scripts/utils/install-ai-tools.sh /tmp/install-ai-tools.sh

RUN chmod +x /tmp/install-ai-tools.sh && \
    NODE_VERSION="${NODE_VERSION}" \
    CLAUDE_CODE_VERSION="${CLAUDE_CODE_VERSION}" \
    CODEX_VERSION="${CODEX_VERSION}" \
    CURSOR_AGENT_VERSION="${CURSOR_AGENT_VERSION}" \
    CURSOR_AGENT_SHA256_X64="${CURSOR_AGENT_SHA256_X64}" \
    CURSOR_AGENT_SHA256_ARM64="${CURSOR_AGENT_SHA256_ARM64}" \
    /tmp/install-ai-tools.sh && \
    rm -f /tmp/install-ai-tools.sh

# The installer already verifies each binary through an explicit prefix. This
# repeat proves the same thing through the image's own ENV PATH, which is what
# consumers actually get. The non-root smoke lives in the root Dockerfile's
# `ai` stage, where the `lintro` user exists.
RUN echo "=== Verifying AI agent CLIs on PATH ===" && \
    claude --version && codex --version && agent --version && \
    echo "=== All AI agent CLIs verified! ==="
