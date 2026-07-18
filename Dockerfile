# syntax=docker/dockerfile:1@sha256:87999aa3d42bdc6bea60565083ee17e86d1f3339802f543c0d03998580f9cb89
# =============================================================================
# Lintro Docker Image (multi-stage)
# =============================================================================
# Stage `tools`: external linting toolchains (Rust, Node/bun, Python tools, …)
# Stage `full` (default): Python application layer on top of tools
#
# Minimal image (no bundled tools): ghcr.io/lgtm-hq/py-lintro-base (--target base)
# =============================================================================

# -----------------------------------------------------------------------------
# Stage: tools — published lintro-tools base image (digest-pinned)
# -----------------------------------------------------------------------------
# Built from docker/tools.Dockerfile and published by docker-tools-publish.yml
# (cosign-signed, SBOM + provenance). Renovate manages the digest bump (#1360).
# yamllint / hadolint: pin is immutable by digest; tag is informational.
FROM ghcr.io/lgtm-hq/lintro-tools:latest@sha256:0024f54a75d4cf7f2ba6563c8f18e05bb825affc37885bfe5fa10cc789df12aa AS tools

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

# Minimal cross-ecosystem smoke check. Comprehensive manifest-vs-image tool
# verification now runs in CI against this image
# (scripts/ci/verify-image-manifest-tools.sh, wired into docker-ci.yml, #1511),
# so the exhaustive hand-maintained per-tool --version list that used to live
# here is reduced to a representative smoke. That hand-maintained list was the
# exact edit that got forgotten for pip-audit (#1505); the manifest-driven gate
# self-updates as manifest entries change, no per-tool edit to forget. The full
# tool set is still enforced at tools-image build time in docker/tools.Dockerfile.
RUN echo "Smoke-testing tool stack..." && \
    ruff --version && prettier --version && rustfmt --version && \
    shellcheck --version && \
    echo "Tool stack smoke check passed."

HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD /app/.venv/bin/python -m lintro --version || exit 1

# Minimal non-root smoke: confirm the gosu privilege drop works and the tools
# group can execute the permission-sensitive toolchains under /opt/bun and
# /opt/cargo. The CI manifest gate runs as root, so it would not catch a
# non-root permission regression on these dirs — this stays as a targeted smoke.
RUN echo "Smoke-testing tools as non-root user..." && \
    gosu lintro prettier --version && \
    gosu lintro cargo clippy --version && \
    echo "Non-root tool smoke check passed."

# No USER directive: the container starts as root so entrypoint.sh can detect
# the UID/GID that owns the mounted /code volume and drop privileges to it via
# gosu. This lets auto-install write node_modules into the volume without
# consumers passing --user. See scripts/docker/entrypoint.sh.
ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
CMD ["--help"]

# -----------------------------------------------------------------------------
# Stage: base — minimal runtime without external toolchains
# -----------------------------------------------------------------------------
FROM python:3.14-slim@sha256:cea0e6040540fb2b965b6e7fb5ffa00871e632eef63719f0ea54bca189ce14a6 AS base

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

# No USER directive: the container starts as root so entrypoint.sh can detect
# the UID/GID that owns the mounted /code volume and drop privileges to it via
# gosu (installed above). See scripts/docker/entrypoint.sh.
ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
CMD ["--help"]
