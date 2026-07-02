# syntax=docker/dockerfile:1
# =============================================================================
# Lintro Docker Image (multi-stage)
# =============================================================================
# Stage `tools`: external linting toolchains (Rust, Node/bun, Python tools, …)
# Stage `full` (default): Python application layer on top of tools
#
# Minimal image (no bundled tools): ghcr.io/lgtm-hq/py-lintro-base (--target base)
# =============================================================================

# -----------------------------------------------------------------------------
# Stage: tools — pre-built linting toolchains
# -----------------------------------------------------------------------------
FROM python:3.14-slim@sha256:7a500125bc50693f2214e842a621440a1b1b9cbb2188f74ab045d29ed2ea5856 AS tools

ARG BUN_VERSION=1.3.11
ARG UV_VERSION=0.11.26

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
    black --version && gitleaks version && hadolint --version && \
    markdownlint-cli2 --version && mypy --version && osv-scanner --version && \
    oxfmt --version && oxlint --version && prettier --version && \
    pydoclint --version && ruff --version && semgrep --version && \
    shellcheck --version && shfmt --version && sqlfluff --version && \
    taplo --version && tsc --version && astro --version && \
    svelte-check --version && vue-tsc --version && yamllint --version && \
    echo "=== All tools verified! ==="

# -----------------------------------------------------------------------------
# Stage: full — lintro application (default target)
# -----------------------------------------------------------------------------
FROM tools AS full

LABEL org.opencontainers.image.description="Making Linters Play Nice... Mostly."

ENV PYTHONPATH=/app \
    RUFF_CACHE_DIR=/tmp/.ruff_cache \
    PATH="/usr/local/bin:/opt/cargo/bin:/opt/bun/bin:${PATH}"

WORKDIR /app

COPY pyproject.toml uv.lock package.json /app/
COPY lintro/ /app/lintro/

ARG WITH_AI=false

RUN --mount=type=cache,target=/root/.cache/uv,sharing=locked \
    if [ "$WITH_AI" = "true" ]; then \
      uv sync --dev --extra full --extra tools --extra ai --no-progress; \
    else \
      uv sync --dev --extra full --extra tools --no-progress; \
    fi && (uv cache clean || true)

# hadolint ignore=DL3008
RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,target=/var/lib/apt,sharing=locked \
    apt-get update && apt-get install -y --no-install-recommends gosu && \
    rm -rf /var/lib/apt/lists/* && \
    gosu nobody true

COPY scripts/docker/entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh

RUN getent group tools >/dev/null || groupadd -r tools && \
    id -u lintro >/dev/null 2>&1 || useradd -m -G tools lintro && \
    mkdir -p /code && \
    chown -R lintro:lintro /app /code

RUN echo "Verifying tools..." && \
    rustfmt --version && cargo clippy --version && cargo audit --version && \
    cargo deny --version && semgrep --version && ruff --version && \
    black --version && hadolint --version && actionlint --version && \
    shellcheck --version && shfmt --version && taplo --version && \
    gitleaks version && osv-scanner --version && prettier --version && \
    markdownlint-cli2 --version && tsc --version && astro --version && \
    vue-tsc --version && oxlint --version && oxfmt --version && \
    bandit --version && mypy --version && pydoclint --version && \
    yamllint --version && sqlfluff --version && \
    echo "All tools verified!"

HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD /app/.venv/bin/python -m lintro --version || exit 1

RUN echo "Verifying tools as non-root user..." && \
    gosu lintro prettier --version && \
    gosu lintro markdownlint-cli2 --version && \
    gosu lintro tsc --version && \
    gosu lintro astro --version && \
    gosu lintro vue-tsc --version && \
    gosu lintro oxlint --version && \
    gosu lintro oxfmt --version && \
    gosu lintro rustfmt --version && \
    gosu lintro cargo clippy --version && \
    gosu lintro cargo audit --version && \
    gosu lintro cargo deny --version && \
    gosu lintro osv-scanner --version && \
    gosu lintro semgrep --version && \
    echo "All tools verified for non-root user!"

USER lintro

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
CMD ["--help"]

# -----------------------------------------------------------------------------
# Stage: base — minimal runtime without external toolchains
# -----------------------------------------------------------------------------
FROM python:3.14-slim@sha256:7a500125bc50693f2214e842a621440a1b1b9cbb2188f74ab045d29ed2ea5856 AS base

LABEL org.opencontainers.image.description="Lintro base image (no external tools); GHCR package py-lintro-base"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app \
    UV_SYSTEM_PYTHON=1

SHELL ["/bin/bash", "-o", "pipefail", "-c"]

WORKDIR /app

# hadolint ignore=DL3008
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    ca-certificates \
    gosu && \
    gosu nobody true && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

COPY --from=tools /usr/local/bin/uv /usr/local/bin/uv

COPY pyproject.toml uv.lock /app/
COPY lintro/ /app/lintro/

RUN uv sync --no-dev --no-progress && (uv cache clean || true)

COPY scripts/docker/entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh

RUN useradd -m lintro && \
    mkdir -p /code && \
    chown -R lintro:lintro /app /code

HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD /app/.venv/bin/python -m lintro --version || exit 1

USER lintro

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
CMD ["--help"]
