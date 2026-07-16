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

# html-validate is added via install-tools.sh for the next lintro-tools publish;
# install here so digest-pinned FROM still verifies until Renovate bumps the pin.
# hadolint ignore=DL3016,SC2086
RUN --mount=type=cache,target=/root/.bun,sharing=locked \
    HTML_VALIDATE_VERSION="$(python3 -c "import json; print(json.load(open('package.json'))['devDependencies']['html-validate'])")" && \
    bun add -g "html-validate@${HTML_VALIDATE_VERSION}"

RUN echo "Verifying tools..." && \
    rustfmt --version && cargo clippy --version && cargo audit --version && \
    cargo deny --version && semgrep --version && ruff --version && \
    black --version && hadolint --version && actionlint --version && \
    shellcheck --version && shfmt --version && taplo --version && \
    dotenv-linter --version && \
    gitleaks version && osv-scanner --version && prettier --version && \
    commitlint --version && \
    html-validate --version && \
    markdownlint-cli2 --version && tsc --version && astro --version && \
    vue-tsc --version && oxlint --version && oxfmt --version && \
    bandit --version && mypy --version && pydoclint --version && \
    yamllint --version && sqlfluff --version && stylelint --version && \
    vale --version && \
    echo "All tools verified!"

HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD /app/.venv/bin/python -m lintro --version || exit 1

RUN echo "Verifying tools as non-root user..." && \
    gosu lintro prettier --version && \
    gosu lintro commitlint --version && \
    gosu lintro html-validate --version && \
    gosu lintro markdownlint-cli2 --version && \
    gosu lintro tsc --version && \
    gosu lintro astro --version && \
    gosu lintro vue-tsc --version && \
    gosu lintro oxlint --version && \
    gosu lintro oxfmt --version && \
    gosu lintro stylelint --version && \
    gosu lintro rustfmt --version && \
    gosu lintro cargo clippy --version && \
    gosu lintro cargo audit --version && \
    gosu lintro cargo deny --version && \
    gosu lintro osv-scanner --version && \
    gosu lintro semgrep --version && \
    gosu lintro dotenv-linter --version && \
    echo "All tools verified for non-root user!"

# No USER directive: the container starts as root so entrypoint.sh can detect
# the UID/GID that owns the mounted /code volume and drop privileges to it via
# gosu. This lets auto-install write node_modules into the volume without
# consumers passing --user. See scripts/docker/entrypoint.sh.
ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
CMD ["--help"]

# -----------------------------------------------------------------------------
# Stage: base — minimal runtime without external toolchains
# -----------------------------------------------------------------------------
FROM python:3.14-slim@sha256:d3400aa122fa42cf0af0dbe8ec3091b047eac5c8f7e3539f7135e86d855dc015 AS base

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
