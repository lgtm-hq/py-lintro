#!/usr/bin/env bash
set -euo pipefail

# setup-python.sh - Install and configure specific Python version via uv
# Single Responsibility: Only handle Python version setup

# Show help if requested
if [ "${1:-}" = "--help" ] || [ "${1:-}" = "-h" ]; then
	cat <<'EOF'
Install and configure specific Python version via uv.

Usage:
  scripts/utils/setup-python.sh [--help|-h] [--dry-run] [--verbose] [PYTHON_VERSION]

Options:
  --help, -h    Show this help message
  --dry-run     Show what would be done without executing
  --verbose     Enable verbose output

Arguments:
  PYTHON_VERSION  Python version to install (default: 3.14)

Environment Variables:
  UV_PYTHON     Set to the installed Python version for downstream tools
  GITHUB_ENV    GitHub Actions environment file (for UV_PYTHON persistence)
EOF
	exit 0
fi

DRY_RUN=0
VERBOSE=0
PYTHON_VERSION=""

# Parse arguments
while [[ $# -gt 0 ]]; do
	case $1 in
	--dry-run)
		DRY_RUN=1
		shift
		;;
	--verbose)
		VERBOSE=1
		shift
		;;
	--help | -h)
		# Already handled above, but include for completeness
		shift
		;;
	*)
		if [ -z "$PYTHON_VERSION" ]; then
			PYTHON_VERSION="$1"
		else
			echo "Unknown argument: $1" >&2
			exit 1
		fi
		shift
		;;
	esac
done

# Set default Python version
PYTHON_VERSION="${PYTHON_VERSION:-3.14}"

log_info() {
	echo "[setup-python] $*"
}

log_verbose() {
	[ $VERBOSE -eq 1 ] && echo "[setup-python] [verbose] $*" || true
}

# Check if uv is available
if ! command -v uv >/dev/null 2>&1; then
	echo "[setup-python] ERROR: uv is required but not available" >&2
	echo "[setup-python] Install uv first: scripts/utils/install-uv.sh" >&2
	exit 1
fi

log_info "Setting up Python ${PYTHON_VERSION}"

if [ $DRY_RUN -eq 1 ]; then
	log_info "[DRY-RUN] Would install Python ${PYTHON_VERSION} via uv"
	log_info "[DRY-RUN] Would set UV_PYTHON=${PYTHON_VERSION} in environment"
	exit 0
fi

# Install requested Python version
log_verbose "Running: uv python install ${PYTHON_VERSION}"
if ! uv python install "${PYTHON_VERSION}"; then
	echo "[setup-python] ERROR: Failed to install Python ${PYTHON_VERSION}" >&2
	exit 1
fi

# Set UV_PYTHON for uv tools and export to GitHub Actions environment
export UV_PYTHON="${PYTHON_VERSION}"
if [ -n "${GITHUB_ENV:-}" ]; then
	echo "UV_PYTHON=${PYTHON_VERSION}" >>"${GITHUB_ENV}"
	log_verbose "Set UV_PYTHON=${PYTHON_VERSION} in GitHub Actions environment"
fi

# Verify installation
python_path="$(uv python find "${PYTHON_VERSION}" 2>/dev/null || true)"
if [ -n "$python_path" ]; then
	python_version="$("$python_path" --version 2>/dev/null || echo 'unknown')"
	log_info "Python setup complete: $python_version at $python_path"
else
	echo "[setup-python] ERROR: Could not verify Python ${PYTHON_VERSION} installation" >&2
	exit 1
fi
