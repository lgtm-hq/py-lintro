#!/usr/bin/env bash
set -euo pipefail

# test-verify-imports.sh - Verify ALL package imports in installed lintro
# Tests that every package in the lintro source tree can be imported from the
# built distribution. Packages are discovered by walking the tree for
# __init__.py files rather than by reading a list from pyproject.toml, because
# the wheel is built with [tool.setuptools.packages.find] (#1225) and there is
# no explicit list to read. This catches packaging issues such as a subpackage
# that the find directive excludes by accident.

# Show help if requested
if [ "${1:-}" = "--help" ] || [ "${1:-}" = "-h" ]; then
	cat <<'EOF'
Verify ALL package imports from installed lintro package.

Usage:
  scripts/ci/test-verify-imports.sh [--help|-h] [DISTRIBUTION_TYPE] [PYTHON_BIN]

Arguments:
  DISTRIBUTION_TYPE 'wheel' or 'sdist', reported in the log (default: wheel)
  PYTHON_BIN        Python executable in venv (default: test_venv/bin/python)

Environment Variables:
  TEST_VENV_PYTHON  Python executable path (overrides PYTHON_BIN arg)

Verifies:
  - Every package in the lintro source tree (directories with __init__.py)
  - The PEP 561 py.typed marker ships with the installed package
  - CLI entry point functionality
  - Plugin registry loading
EOF
	exit 0
fi

DISTRIBUTION_TYPE="${1:-wheel}"
PYTHON_BIN="${TEST_VENV_PYTHON:-${2:-test_venv/bin/python}}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

log_info() {
	echo "[test-verify-imports] $*"
}

log_error() {
	echo "[test-verify-imports] ERROR: $*" >&2
}

log_info "Verifying $DISTRIBUTION_TYPE imports with: $PYTHON_BIN"

# Verify Python executable exists
if [ ! -f "$PYTHON_BIN" ]; then
	log_error "Python executable not found at $PYTHON_BIN"
	exit 1
fi

# ``python -c`` puts the current directory first on sys.path, so running from
# the checkout would import the source tree instead of the installed package
# and a subpackage missing from the distribution would still pass. Run every
# check from a scratch directory outside the repository, with any inherited
# PYTHONPATH dropped.
PYTHON_BIN="$(cd "$(dirname "$PYTHON_BIN")" && pwd)/$(basename "$PYTHON_BIN")"
ISOLATED_DIR="$(mktemp -d)"
trap 'rm -rf "$ISOLATED_DIR"' EXIT

run_isolated() {
	(cd "$ISOLATED_DIR" && env -u PYTHONPATH "$PYTHON_BIN" "$@")
}

# Discover packages by walking the source tree. The build uses
# [tool.setuptools.packages.find], so there is no explicit list to read.
log_info "Discovering packages in the lintro source tree..."
PACKAGES=$(run_isolated -c "
from pathlib import Path

root = Path('$PROJECT_ROOT/lintro')
for init in sorted(root.rglob('__init__.py')):
    print('.'.join(init.parent.relative_to(root.parent).parts))
")

if [ -z "$PACKAGES" ]; then
	log_error "No packages discovered under $PROJECT_ROOT/lintro"
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
	if run_isolated -c "import $package" 2>/dev/null; then
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
	log_error "This usually means [tool.setuptools.packages.find] in pyproject.toml excludes the package"
	exit 1
fi

# PEP 561 marker: type checkers only honour inline hints when py.typed ships.
log_info "Verifying the py.typed marker ships with the installed package..."
if ! run_isolated -c "
import pathlib
import lintro

marker = pathlib.Path(lintro.__file__).parent / 'py.typed'
if not marker.is_file():
    raise SystemExit(f'py.typed missing from installed package at {marker}')
"; then
	log_error "Installed lintro is missing the PEP 561 py.typed marker"
	exit 1
fi

# Additional functional tests
log_info "Running additional functional tests..."

# Test CLI module entry point
log_info "Testing CLI entry point..."
if ! run_isolated -c "from lintro.cli import cli; from lintro.cli import main"; then
	log_error "Failed to import CLI entry points"
	exit 1
fi

# Both distributions install the same package contents, so the registry has to
# load from either one.
log_info "Testing plugin registry..."
if ! run_isolated -c "
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

log_info "All import tests passed ($PACKAGE_COUNT packages verified)"
