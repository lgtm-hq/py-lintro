# =============================================================================
# Lintro Docker Image
# =============================================================================
# Built on top of the pre-built tools image which contains all linting tools.
# This image adds the Python application layer.
#
# The tools image is rebuilt weekly and contains:
# - Rust toolchain (rustfmt, clippy, cargo-audit, cargo-deny)
# - Node.js tools via bun (prettier, markdownlint-cli2, tsc, astro, vue-tsc, oxlint, oxfmt)
# - Python tools (ruff, black, bandit, mypy, semgrep, etc.)
# - Standalone binaries (hadolint, actionlint, shellcheck, shfmt, taplo, gitleaks)
# =============================================================================

# TOOLS_IMAGE can be overridden at build time (e.g., for PR testing with new tools)
# yamllint disable-line rule:line-length
ARG TOOLS_IMAGE=ghcr.io/lgtm-hq/lintro-tools:latest@sha256:19c1cd7e4ba40f5101282de5049058ca6bc9b929ecc6dc73fa1ae05f63f584ff
# checkov:skip=CKV_DOCKER_7: Tools image is pinned by digest; tag is for readability.
# hadolint ignore=DL3006
FROM ${TOOLS_IMAGE}

# Add Docker labels
LABEL maintainer="lgtm-hq"
LABEL org.opencontainers.image.source="https://github.com/lgtm-hq/py-lintro"
LABEL org.opencontainers.image.description="Making Linters Play Nice... Mostly."
LABEL org.opencontainers.image.licenses="MIT"

# Set environment variables
# Explicitly include tool paths to ensure they're available for non-root user
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app \
    RUFF_CACHE_DIR=/tmp/.ruff_cache \
    PATH="/usr/local/bin:/opt/cargo/bin:/opt/bun/bin:${PATH}"

WORKDIR /app

# Copy dependency files first for better layer caching
COPY pyproject.toml uv.lock package.json /app/

# Copy full source
COPY lintro/ /app/lintro/

# Build argument: set WITH_AI=true to include AI provider dependencies
ARG WITH_AI=false

# Install Python dependencies (conditionally include AI extras)
RUN if [ "$WITH_AI" = "true" ]; then \
      uv sync --dev --extra tools --extra ai --no-progress; \
    else \
      uv sync --dev --extra tools --no-progress; \
    fi && (uv cache clean || true)

# Install gosu for secure privilege dropping in the entrypoint
# hadolint ignore=DL3008
RUN apt-get update && apt-get install -y --no-install-recommends gosu && \
    rm -rf /var/lib/apt/lists/* && \
    gosu nobody true

# Copy entrypoint script
COPY scripts/docker/entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh

# Create non-root user and directories
RUN getent group tools >/dev/null || groupadd -r tools && \
    id -u lintro >/dev/null 2>&1 || useradd -m -G tools lintro && \
    mkdir -p /code && \
    chown -R lintro:lintro /app /code

# Verify all tools are working (fail the build if any tool is broken)
RUN echo "Verifying tools..." && \
    rustfmt --version && \
    cargo clippy --version && \
    cargo audit --version && \
    cargo deny --version && \
    semgrep --version && \
    ruff --version && \
    black --version && \
    hadolint --version && \
    actionlint --version && \
    shellcheck --version && \
    shfmt --version && \
    taplo --version && \
    gitleaks version && \
    prettier --version && \
    markdownlint-cli2 --version && \
    tsc --version && \
    astro --version && \
    vue-tsc --version && \
    oxlint --version && \
    oxfmt --version && \
    bandit --version && \
    mypy --version && \
    pydoclint --version && \
    yamllint --version && \
    sqlfluff --version && \
    echo "All tools verified!"

# Health check to verify lintro is working
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD /app/.venv/bin/python -m lintro --version || exit 1

# Verify tools work as non-root user (catches permission issues early)
# The entrypoint handles dropping to non-root via gosu at runtime.
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
    gosu lintro semgrep --version && \
    echo "All tools verified for non-root user!"

# Use the flexible entrypoint
ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
CMD ["--help"]
