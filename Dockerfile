# syntax=docker/dockerfile:1@sha256:87999aa3d42bdc6bea60565083ee17e86d1f3339802f543c0d03998580f9cb89
# =============================================================================
# Lintro Docker Image (multi-stage)
# =============================================================================
# Stage `tools`: external linting toolchains (Rust, Node/bun, Python tools, …)
# Stage `full` (default): Python application layer on top of tools
#
# Minimal image (no bundled tools): ghcr.io/lgtm-hq/py-lintro-base (--target base)
# AI variant (bundled agent CLIs):  ghcr.io/lgtm-hq/py-lintro-ai   (--target ai)
# =============================================================================

# -----------------------------------------------------------------------------
# Stage: tools — published lintro-tools base image (digest-pinned)
# -----------------------------------------------------------------------------
# Built from docker/tools.Dockerfile and published by docker-tools-publish.yml
# (cosign-signed, SBOM + provenance). Renovate manages the digest bump (#1360).
# yamllint / hadolint: pin is immutable by digest; tag is informational.
FROM ghcr.io/lgtm-hq/lintro-tools:latest@sha256:9a976b39ace2f48c49f2f74ed36c11c673ca0e8985d13233ac0d1ac024fe4582 AS tools

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

# -----------------------------------------------------------------------------
# Stage: aitools — published lintro-ai-tools base image (digest-pinned)
# -----------------------------------------------------------------------------
# Built from docker/ai-tools.Dockerfile and published by
# docker-ai-tools-publish.yml (cosign-signed, SBOM + provenance). Renovate
# manages the digest bump. Only the `ai` target below depends on this stage, so
# `--target base` / `--target full` builds never pull it.
# yamllint / hadolint: pin is immutable by digest; tag is informational.
FROM ghcr.io/lgtm-hq/lintro-ai-tools:latest@sha256:8daf68214ca1f8e5af4a20f166284c444f5ba36dff0858426731d66185934478 AS aitools

# -----------------------------------------------------------------------------
# Stage: ai — full image plus the agent CLIs `--transport cli` drives
# -----------------------------------------------------------------------------
FROM full AS ai

LABEL org.opencontainers.image.description="Lintro with bundled AI agent CLIs; GHCR package py-lintro-ai"

# One directory holds the bundled Node.js runtime, the npm-global claude/codex
# trees, the Cursor agent release and the launcher shims — see
# scripts/utils/install-ai-tools.sh for the layout. Only /opt/ai-tools/bin goes
# on PATH, so `node` keeps resolving to bun for the lint toolchain.
COPY --from=aitools /opt/ai-tools /opt/ai-tools

ENV PATH="/opt/ai-tools/bin:${PATH}"

# The `full` stage syncs without the `ai` extra so the lint-only image does not
# carry the provider SDKs; add them here for the API transports. The chown
# mirrors `full`'s, which runs before these newly written .venv files exist.
RUN --mount=type=cache,target=/root/.cache/uv,sharing=locked \
    uv sync --dev --extra full --extra tools --extra ai --no-progress && \
    (uv cache clean || true) && \
    chown -R lintro:lintro /app

# Mirrors the non-root smoke in `full`: the entrypoint drops privileges to the
# UID owning the mounted volume, so a root-only-readable CLI tree would break
# every real review while passing a root-run check.
RUN echo "Smoke-testing AI agent CLIs..." && \
    claude --version && codex --version && agent --version && \
    gosu lintro claude --version && \
    gosu lintro codex --version && \
    gosu lintro agent --version && \
    echo "AI CLI smoke check passed."

# ENTRYPOINT, CMD and HEALTHCHECK are inherited from `full`.
