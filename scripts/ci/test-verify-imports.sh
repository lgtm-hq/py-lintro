#!/usr/bin/env bash
set -euo pipefail

# test-verify-imports.sh - Verify ALL package imports in installed lintro
# Tests that every package listed in pyproject.toml can be imported from
# the built distribution. This catches packaging issues like missing packages
# in the setuptools configuration.

# Show help if requested
if [ "${1:-}" = "--help" ] || [ "${1:-}" = "-h" ]; then
	cat <<'EOF'
Verify ALL package imports from installed lintro package.

Usage:
  scripts/ci/test-verify-imports.sh [--help|-h] [PYTHON_BIN] [DISTRIBUTION_TYPE]

Arguments:
  PYTHON_BIN        Python executable in venv (default: test_venv/bin/python)
  DISTRIBUTION_TYPE 'wheel', 'sdist', or 'both' (default: wheel)

Environment Variables:
  TEST_VENV_PYTHON  Python executable path (overrides PYTHON_BIN arg)

Verifies:
  - All packages listed in pyproject.toml [tool.setuptools] packages
  - CLI entry point functionality
  - Plugin registry loading
EOF
	exit 0
fi

PYTHON_BIN="${TEST_VENV_PYTHON:-${1:-test_venv/bin/python}}"
DISTRIBUTION_TYPE="${2:-wheel}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

log_info() {
	echo "[test-verify-imports] $*"
}

log_error() {
	echo "[test-verify-imports] ERROR: $*" >&2
}

log_info "Verifying imports with: $PYTHON_BIN"

# Verify Python executable exists
if [ ! -f "$PYTHON_BIN" ]; then
	log_error "Python executable not found at $PYTHON_BIN"
	exit 1
fi

# Extract all packages from pyproject.toml via setuptools (the same finder
# the wheel build uses). Discovery runs under uv so the test venv does not
# need setuptools installed.
log_info "Extracting packages from pyproject.toml..."
PACKAGES=$(
	cd "$PROJECT_ROOT"
	uv run python tests/packaging/configured_packages.py
)

if [ -z "$PACKAGES" ]; then
	log_error "No packages found in pyproject.toml"
	exit 1
fi

# Count packages
PACKAGE_COUNT=$(echo "$PACKAGES" | wc -l | tr -d ' ')
log_info "Found $PACKAGE_COUNT packages to verify"

# Test each package import
FAILED_IMPORTS=()
PASS_COUNT=0

log_info "Testing all package imports..."
while IFS= read -r package; do
	if "$PYTHON_BIN" -c "import $package" 2>/dev/null; then
		PASS_COUNT=$((PASS_COUNT + 1))
	else
		FAILED_IMPORTS+=("$package")
		log_error "Failed to import: $package"
	fi
done <<<"$PACKAGES"

# Report results
log_info "Import results: $PASS_COUNT/$PACKAGE_COUNT packages imported successfully"

if [ ${#FAILED_IMPORTS[@]} -gt 0 ]; then
	log_error "The following packages failed to import:"
	for pkg in "${FAILED_IMPORTS[@]}"; do
		echo "  - $pkg" >&2
	done
	log_error "This usually means the package is missing from [tool.setuptools] packages in pyproject.toml"
	exit 1
fi

# Additional functional tests
log_info "Running additional functional tests..."

# Test CLI module entry point
log_info "Testing CLI entry point..."
if ! "$PYTHON_BIN" -c "from lintro.cli import cli; from lintro.cli import main"; then
	log_error "Failed to import CLI entry points"
	exit 1
fi

# Wheel-only tests (plugin registry)
if [ "$DISTRIBUTION_TYPE" = "wheel" ] || [ "$DISTRIBUTION_TYPE" = "both" ]; then
	log_info "Testing plugin registry (wheel distribution)..."
	if ! "$PYTHON_BIN" -c "
from lintro.plugins.registry import ToolRegistry
from lintro.plugins.discovery import discover_all_tools
discover_all_tools()
# Verify at least some tools are registered
tools = ToolRegistry.get_all()
assert len(tools) > 0, 'No tools registered'
"; then
		log_error "Failed to load plugin registry"
		exit 1
	fi
fi

log_info "All import tests passed ($PACKAGE_COUNT packages verified)"
