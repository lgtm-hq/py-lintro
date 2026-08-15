#!/usr/bin/env bash
# Cloud Agent install phase for the lintro Python CLI.
#
# Idempotent bootstrap: ensure the `uv` package manager is available, then sync
# the project's Python dependencies plus the Python-based wrapped tools (ruff,
# black, mypy, bandit, pydoclint, yamllint) into `.venv`. Safe to re-run.
set -euo pipefail

# Avoid uv hardlink warnings when the cache and workspace are on different mounts.
export UV_LINK_MODE=copy

# uv installs to ~/.local/bin; make sure it is discoverable for this process.
export PATH="$HOME/.local/bin:$PATH"

if ! command -v uv >/dev/null 2>&1; then
	echo "uv not found; installing uv..."
	curl -LsSf https://astral.sh/uv/install.sh | sh
	export PATH="$HOME/.local/bin:$PATH"
fi

echo "uv version: $(uv --version)"

# Create/refresh .venv with dev dependencies and the full set of Python tools.
uv sync --dev --extra full

echo "Install complete. Try: uv run lintro check ."
