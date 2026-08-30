# syntax=docker/dockerfile:1@sha256:ecfaec9ed6d810b56388c508f4121597bfbba70d41a6dfeee4d8cad5f295fc32
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
FROM ghcr.io/lgtm-hq/lintro-tools:latest@sha256:7ed8812e769c6087f11b271998577c243f14236deda9bb541a72685bf0897d4a AS tools

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
COPY lintro_build/ /app/lintro_build/
COPY requirements-semgrep.txt /app/requirements-semgrep.txt
COPY scripts/ci/generate-tool-versions.py /app/scripts/ci/generate-tool-versions.py
COPY scripts/ci/generate-builtin-tool-index.py /app/scripts/ci/generate-builtin-tool-index.py
COPY scripts/utils/install-semgrep.sh /app/scripts/utils/install-semgrep.sh
COPY scripts/utils/install-tools.sh /app/scripts/utils/install-tools.sh
COPY scripts/utils/utils.sh /app/scripts/utils/utils.sh

# Regenerate the version artifacts from their sources (#2179): this COPY of
# lintro/ overwrites the tools stage's regenerated artifacts with the build
# context's, so the app layer regenerates for itself. A no-op while the
# artifacts are committed; load-bearing once they stop being committed
# (epic #2176 phase 4).
RUN python3 scripts/ci/generate-tool-versions.py && \
    python3 scripts/ci/generate-builtin-tool-index.py

ARG WITH_AI=false

RUN --mount=type=cache,target=/root/.cache/uv,sharing=locked \
    if [ "$WITH_AI" = "true" ]; then \
      uv sync --dev --extra full --extra tools --extra ai --no-progress; \
    else \
      uv sync --dev --extra full --extra tools --no-progress; \
    fi && (uv cache clean || true)

# Semgrep lives in an isolated venv, not lintro[tools] (#2104). Drop the
# digest-pinned tools image's leftover system copy first — `uv pip uninstall`
# deletes RECORD-listed paths, including any /usr/local/bin/semgrep symlink
# already created — then re-sync from this build's lockfile.
RUN --mount=type=cache,target=/root/.cache/uv,sharing=locked \
    (UV_SYSTEM_PYTHON=1 uv pip uninstall --system semgrep || true) && \
    chmod +x /app/scripts/utils/install-semgrep.sh && \
    /app/scripts/utils/install-semgrep.sh --docker

# New binaries land in docker/tools.Dockerfile, but this app image still
# FROMs a digest-pinned tools image that will not contain them until the
# next published digest. Bridge typos, spectral, and buf here so dogfood and
# the manifest-vs-image gate actually run them instead of failing with
# binary_missing. No-op once the digest already has them on PATH.
RUN chmod +x /app/scripts/utils/install-tools.sh && \
    /app/scripts/utils/install-tools.sh --docker --tools typos,spectral,buf

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
    chgrp -R tools /opt/semgrep-venv && \
    chmod -R g+rwX /opt/semgrep-venv && \
    chmod -R a+rX /opt/semgrep-venv && \
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
    shellcheck --version && semgrep --version && typos --version && \
    spectral --version && \
    echo "Tool stack smoke check passed."

HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD ["/app/.venv/bin/python", "-m", "lintro", "--version"]

# Minimal non-root smoke: confirm the gosu privilege drop works and the tools
# group can execute the permission-sensitive toolchains under /opt/bun and
# /opt/cargo. The CI manifest gate runs as root, so it would not catch a
# non-root permission regression on these dirs — this stays as a targeted smoke.
RUN echo "Smoke-testing tools as non-root user..." && \
    gosu lintro prettier --version && \
    gosu lintro cargo clippy --version && \
    gosu lintro semgrep --version && \
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
FROM python:3.14-slim@sha256:cae66f2ef0ec51a9891263eeee7f987dacf0a9879e8aa9353d5606e0530619a5 AS base

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

COPY pyproject.toml uv.lock package.json /app/
COPY lintro/ /app/lintro/
# The in-tree PEP 517 backend and its generator inputs (#2180): uv sync
# builds lintro from /app, which regenerates the derived version artifacts.
COPY lintro_build/ /app/lintro_build/
COPY requirements-semgrep.txt /app/requirements-semgrep.txt

RUN uv sync --no-dev --no-progress && (uv cache clean || true)

COPY scripts/docker/entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh

RUN useradd -m lintro && \
    mkdir -p /code && \
    chown -R lintro:lintro /app /code

HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD ["/app/.venv/bin/python", "-m", "lintro", "--version"]

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
FROM ghcr.io/lgtm-hq/lintro-ai-tools:latest@sha256:d9605c7bf34e66f305b94cfe088f7263eb41e2e0cf7ef62987c1c0b5eb07e745 AS aitools

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
