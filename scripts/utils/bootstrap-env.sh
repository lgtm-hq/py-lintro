#!/usr/bin/env bash
set -euo pipefail

# bootstrap-env.sh - Orchestrate complete environment setup
# Delegates to focused single-responsibility scripts

# Show help if requested
if [ "${1:-}" = "--help" ] || [ "${1:-}" = "-h" ]; then
	cat <<'EOF'
Bootstrap complete CI environment with Python, uv, and external tools.

Usage:
  scripts/utils/bootstrap-env.sh [--help|-h] [--dry-run] [--verbose] [PYTHON_VERSION]

Options:
  --help, -h    Show this help message
  --dry-run     Show what would be done without executing
  --verbose     Enable verbose output for all components

Arguments:
  PYTHON_VERSION  Python version to install (default: 3.14)

Environment Variables:
  BOOTSTRAP_SKIP_SYNC         Skip dependency sync (default: 0)
  BOOTSTRAP_SKIP_INSTALL_TOOLS Skip external tools install (default: 0)
  UV_VERSION                  Specific uv version to install
  GITHUB_TOKEN                GitHub token for API access
  GITHUB_PATH                 GitHub Actions PATH persistence
  GITHUB_ENV                  GitHub Actions environment persistence

Components:
  1. Install uv (scripts/utils/install-uv.sh)
  2. Setup Python version (scripts/utils/setup-python.sh) 
  3. Sync dependencies (scripts/utils/sync-deps.sh)
  4. Install external tools (scripts/utils/install-tools.sh)
  5. Ensure PATH persistence
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
		# Already handled above
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

PYTHON_VERSION="${PYTHON_VERSION:-3.14}"

log_info() {
	echo "[bootstrap-env] $*"
}

# Build common flags for sub-scripts
script_flags=()
if [ "$DRY_RUN" -eq 1 ]; then
	script_flags+=(--dry-run)
fi
if [ "$VERBOSE" -eq 1 ]; then
	script_flags+=(--verbose)
fi

log_info "Starting environment bootstrap (Python ${PYTHON_VERSION})"

# Component 1: Install uv (always required, even for Docker-centric jobs that skip sync)
log_info "Step 1/5: Installing uv"
if ! ./scripts/utils/install-uv.sh "${script_flags[@]}"; then
	echo "[bootstrap-env] ERROR: Failed to install uv" >&2
	exit 1
fi

# Early exit check for Docker-centric jobs
if [ "${BOOTSTRAP_SKIP_SYNC:-0}" -eq 1 ] && [ "${BOOTSTRAP_SKIP_INSTALL_TOOLS:-0}" -eq 1 ]; then
	log_info "Skipping remaining bootstrap steps (both BOOTSTRAP_SKIP_* flags set)"
	if [ "$DRY_RUN" -eq 0 ] && [ -n "${GITHUB_PATH:-}" ]; then
		[ -d "$HOME/.local/bin" ] && echo "$HOME/.local/bin" >>"$GITHUB_PATH"
		[ -d "$HOME/.bun/bin" ] && echo "$HOME/.bun/bin" >>"$GITHUB_PATH"
	fi
	log_info "Early-exit complete (uv installed)"
	exit 0
fi

# Component 2: Setup Python version
if [ "${BOOTSTRAP_SKIP_SYNC:-0}" -ne 1 ]; then
	log_info "Step 2/5: Setting up Python ${PYTHON_VERSION}"
	if ! ./scripts/utils/setup-python.sh "${script_flags[@]}" "$PYTHON_VERSION"; then
		echo "[bootstrap-env] ERROR: Failed to setup Python" >&2
		exit 1
	fi
else
	log_info "Step 2/5: Skipping Python setup (BOOTSTRAP_SKIP_SYNC=1)"
fi

# Component 3: Sync dependencies
if [ "${BOOTSTRAP_SKIP_SYNC:-0}" -ne 1 ]; then
	log_info "Step 3/5: Syncing Python dependencies"
	if ! ./scripts/utils/sync-deps.sh "${script_flags[@]}" --dev; then
		echo "[bootstrap-env] ERROR: Failed to sync dependencies" >&2
		exit 1
	fi
else
	log_info "Step 3/5: Skipping dependency sync (BOOTSTRAP_SKIP_SYNC=1)"
fi

# Component 4: Install external tools
if [ "${BOOTSTRAP_SKIP_INSTALL_TOOLS:-0}" -ne 1 ]; then
	log_info "Step 4/5: Installing external tools"
	if ! ./scripts/utils/install-tools.sh --local; then
		echo "[bootstrap-env] ERROR: Failed to install external tools" >&2
		exit 1
	fi
else
	log_info "Step 4/5: Skipping external tools install (BOOTSTRAP_SKIP_INSTALL_TOOLS=1)"
fi

# Component 5: Ensure PATH persistence
log_info "Step 5/5: Ensuring PATH persistence"
if [ "$DRY_RUN" -eq 0 ] && [ -n "${GITHUB_PATH:-}" ]; then
	if [ -d "$HOME/.local/bin" ]; then
		echo "$HOME/.local/bin" >>"$GITHUB_PATH"
		log_info "Added $HOME/.local/bin to GitHub Actions PATH"
	fi
	# Add bun global bin directory (used by prettier, markdownlint-cli2, oxlint, oxfmt)
	if [ -d "$HOME/.bun/bin" ]; then
		echo "$HOME/.bun/bin" >>"$GITHUB_PATH"
		log_info "Added $HOME/.bun/bin to GitHub Actions PATH"
	fi
elif [ "$DRY_RUN" -eq 1 ]; then
	log_info "[DRY-RUN] Would add $HOME/.local/bin and $HOME/.bun/bin to PATH"
fi

log_info "Environment bootstrap complete ✅"
