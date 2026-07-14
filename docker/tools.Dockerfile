# syntax=docker/dockerfile:1@sha256:87999aa3d42bdc6bea60565083ee17e86d1f3339802f543c0d03998580f9cb89
# =============================================================================
# Lintro Tools Base Image
# =============================================================================
# Pre-built external linting toolchains (Rust, Node/bun, ~40 linter binaries).
# Published as ghcr.io/lgtm-hq/lintro-tools by
# .github/workflows/docker-tools-publish.yml (weekly + on changes to this file
# or the pinned tool versions).
#
# Once the first image is published, the root Dockerfile will consume it via
# a digest-pinned FROM so the slow, rarely-changing tools layer stays off the
# per-PR build path. Renovate manages the digest bump (native Docker digest
# support — see renovate.json).
#
# Build context is the repository root:
#   docker build -f docker/tools.Dockerfile .
#
# Tool versions come from lintro/_tool_versions.py via
# scripts/utils/install-tools.sh — this file mirrors the `tools` stage in the
# root Dockerfile until the FROM flip lands (see issue #1360).
# =============================================================================

FROM python:3.14-slim@sha256:d3400aa122fa42cf0af0dbe8ec3091b047eac5c8f7e3539f7135e86d855dc015 AS tools

ARG BUN_VERSION=1.3.14
ARG UV_VERSION=0.11.28

LABEL maintainer="lgtm-hq"
LABEL org.opencontainers.image.source="https://github.com/lgtm-hq/py-lintro"
LABEL org.opencontainers.image.description="Pre-built tools layer for lintro"
LABEL org.opencontainers.image.licenses="MIT"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_SYSTEM_PYTHON=1 \
    BUN_INSTALL="/opt/bun" \
    CARGO_HOME="/opt/cargo" \
    RUSTUP_HOME="/opt/rustup" \
    PATH="/usr/local/bin:/opt/cargo/bin:/opt/bun/bin:${PATH}"

SHELL ["/bin/bash", "-o", "pipefail", "-c"]

WORKDIR /app

# hadolint ignore=DL3008
RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,target=/var/lib/apt,sharing=locked \
    apt-get update && apt-get install -y --no-install-recommends \
    curl \
    ca-certificates \
    build-essential \
    git \
    libssl-dev \
    pkg-config \
    unzip \
    jq && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

# hadolint ignore=DL3003,SC2086
RUN --mount=type=cache,target=/root/.cache/bun,sharing=locked \
    ARCH=$(uname -m) && \
    if [ "$ARCH" = "x86_64" ]; then BUN_ARCH="x64"; \
    elif [ "$ARCH" = "aarch64" ]; then BUN_ARCH="aarch64"; \
    else echo "Unsupported arch: $ARCH" && exit 1; fi && \
    BUN_ZIP="bun-linux-${BUN_ARCH}.zip" && \
    BUN_URL="https://github.com/oven-sh/bun/releases/download/bun-v${BUN_VERSION}/${BUN_ZIP}" && \
    CHECKSUM_URL="https://github.com/oven-sh/bun/releases/download/bun-v${BUN_VERSION}/SHASUMS256.txt" && \
    curl -fsSL "$BUN_URL" -o "/tmp/${BUN_ZIP}" && \
    curl -fsSL "$CHECKSUM_URL" -o /tmp/SHASUMS256.txt && \
    cd /tmp && grep "${BUN_ZIP}" SHASUMS256.txt | sha256sum -c - && \
    unzip -q "${BUN_ZIP}" && \
    mv "bun-linux-${BUN_ARCH}/bun" /usr/local/bin/bun && \
    chmod +x /usr/local/bin/bun && \
    ln -sf /usr/local/bin/bun /usr/local/bin/bunx && \
    ln -sf /usr/local/bin/bun /usr/local/bin/node && \
    rm -rf /tmp/bun* /tmp/SHASUMS256.txt

# hadolint ignore=DL3003,SC2086
RUN ARCH=$(uname -m) && \
    if [ "$ARCH" = "x86_64" ]; then UV_ARCH="x86_64"; \
    elif [ "$ARCH" = "aarch64" ]; then UV_ARCH="aarch64"; \
    else echo "Unsupported arch: $ARCH" && exit 1; fi && \
    UV_TAR="uv-${UV_ARCH}-unknown-linux-gnu.tar.gz" && \
    UV_URL="https://github.com/astral-sh/uv/releases/download/${UV_VERSION}/${UV_TAR}" && \
    CHECKSUM_URL="https://github.com/astral-sh/uv/releases/download/${UV_VERSION}/${UV_TAR}.sha256" && \
    curl -fsSL "$UV_URL" -o "/tmp/${UV_TAR}" && \
    curl -fsSL "$CHECKSUM_URL" -o "/tmp/${UV_TAR}.sha256" && \
    cd /tmp && sha256sum -c "${UV_TAR}.sha256" && \
    tar -xzf "${UV_TAR}" && \
    mv "uv-${UV_ARCH}-unknown-linux-gnu/uv" /usr/local/bin/uv && \
    chmod +x /usr/local/bin/uv && \
    rm -rf /tmp/uv*

COPY lintro/ /app/lintro/
COPY scripts/ /app/scripts/
COPY package.json /app/package.json

RUN groupadd -r tools && \
    mkdir -p /opt/bun /opt/cargo /opt/rustup

RUN --mount=type=cache,target=/opt/cargo/registry,sharing=locked \
    --mount=type=cache,target=/opt/cargo/git,sharing=locked \
    --mount=type=cache,target=/root/.cache/uv,sharing=locked \
    find /app/scripts -type f -name "*.sh" -exec chmod +x {} \; && \
    /app/scripts/utils/install-tools.sh --docker && \
    rustup default stable && \
    rustup component add clippy

RUN chgrp -R tools /opt/cargo /opt/rustup /opt/bun && \
    chmod -R g+rwX /opt/cargo /opt/rustup /opt/bun && \
    chmod -R a+rX /opt/cargo /opt/rustup /opt/bun

RUN echo "=== Verifying all tools ===" && \
    bun --version && uv --version && cargo --version && rustc --version && \
    rustfmt --version && cargo clippy --version && cargo audit --version && \
    cargo deny --version && actionlint --version && bandit --version && \
    black --version && commitlint --version && gitleaks version && \
    hadolint --version && \
    markdownlint-cli2 --version && mypy --version && osv-scanner --version && \
    oxfmt --version && oxlint --version && prettier --version && \
    pydoclint --version && ruff --version && semgrep --version && \
    shellcheck --version && shfmt --version && sqlfluff --version && \
    dotenv-linter --version && \
    stylelint --version && \
    taplo --version && tsc --version && astro --version && \
    svelte-check --version && vue-tsc --version && yamllint --version && \
    vale --version && \
    echo "=== All tools verified! ==="
