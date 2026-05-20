#!/usr/bin/env bash
set -euo pipefail

# sync-deps.sh - Sync Python dependencies via uv
# Single Responsibility: Only handle dependency synchronization

# Show help if requested
if [ "${1:-}" = "--help" ] || [ "${1:-}" = "-h" ]; then
	cat <<'EOF'
Sync Python dependencies via uv.

Usage:
  scripts/utils/sync-deps.sh [--help|-h] [--dry-run] [--verbose] [--dev] [--no-dev]

Options:
  --help, -h    Show this help message
  --dry-run     Show what would be done without executing  
  --verbose     Enable verbose output
  --dev         Include development dependencies (default)
  --no-dev      Exclude development dependencies

Environment Variables:
  UV_PYTHON     Python version for uv (set by setup-python.sh)
EOF
	exit 0
fi

DRY_RUN=0
VERBOSE=0
INCLUDE_DEV=1

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
	--dev)
		INCLUDE_DEV=1
		shift
		;;
	--no-dev)
		INCLUDE_DEV=0
		shift
		;;
	--help | -h)
		# Already handled above, but include for completeness
		shift
		;;
	*)
		echo "Unknown argument: $1" >&2
		exit 1
		;;
	esac
done

log_info() {
	echo "[sync-deps] $*"
}

log_verbose() {
	[ $VERBOSE -eq 1 ] && echo "[sync-deps] [verbose] $*" || true
}

# Check if uv is available
if ! command -v uv >/dev/null 2>&1; then
	echo "[sync-deps] ERROR: uv is required but not available" >&2
	echo "[sync-deps] Install uv first: scripts/utils/install-uv.sh" >&2
	exit 1
fi

# Build uv sync command
sync_cmd="uv sync --no-progress"
if [ $INCLUDE_DEV -eq 1 ]; then
	sync_cmd="$sync_cmd --dev --extra full"
	log_info "Syncing Python dependencies (including dev and full extras)"
else
	log_info "Syncing Python dependencies (production only)"
fi

log_verbose "UV_PYTHON=${UV_PYTHON:-not set}"
log_verbose "Command: $sync_cmd"

if [ $DRY_RUN -eq 1 ]; then
	log_info "[DRY-RUN] Would run: $sync_cmd"
	exit 0
fi

# Run dependency sync
if ! $sync_cmd; then
	echo "[sync-deps] ERROR: Failed to sync dependencies" >&2
	echo "[sync-deps] Check pyproject.toml and uv.lock for issues" >&2
	exit 1
fi

log_info "Dependency sync complete"

# Verify installation by checking if we can import the main package
if [ $VERBOSE -eq 1 ]; then
	log_verbose "Verifying installation..."
	if uv run python -c "import lintro; print(f'lintro version: {lintro.__version__}')" 2>/dev/null; then
		log_verbose "Package verification successful"
	else
		log_verbose "Package verification skipped (import failed - may be normal during bootstrap)"
	fi
fi
