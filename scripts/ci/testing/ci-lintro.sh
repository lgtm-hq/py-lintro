#!/usr/bin/env bash

# CI Lintro Analysis Script
# Handles running lintro analysis in Docker for CI pipeline with GitHub Actions integration

set -e

# Show help if requested
if [ "$1" = "--help" ] || [ "$1" = "-h" ]; then
	echo "Usage: $0 [--help]"
	echo ""
	echo "CI Lintro Analysis Script"
	echo "Runs Lintro analysis in Docker for CI pipeline with GitHub Actions integration."
	echo ""
	echo "Features:"
	echo "  - Runs Lintro in Docker container"
	echo "  - Excludes test files via .lintro-ignore"
	echo "  - Generates GitHub Actions summaries"
	echo "  - Stores exit code for PR comment step"
	echo ""
	echo "This script is designed to be run in GitHub Actions CI environment."
	exit 0
fi

# Source shared utilities
# SC1091: path is dynamically constructed, file exists at runtime
# shellcheck disable=SC1091
source "$(dirname "$0")/../../utils/utils.sh"

# Set up step summary if not in GitHub Actions
GITHUB_STEP_SUMMARY="${GITHUB_STEP_SUMMARY:-/dev/null}"
GITHUB_ENV="${GITHUB_ENV:-/dev/null}"

# Ensure CHK_EXIT_CODE is always set, even on early exit
# This prevents the "Fail on Linting Issues" step from triggering on empty value
trap 'echo "CHK_EXIT_CODE=${CHK_EXIT_CODE:-1}" >> "$GITHUB_ENV"' EXIT

{
	echo "## 🔧 Lintro Code Quality & Analysis"
	echo ""
	echo "### 🛠️ Step 1: Running Lintro Checks"
	echo "Running \`lintro check\` in Docker container against the entire project..."
	echo ""
} >>"$GITHUB_STEP_SUMMARY"

# NOTE: Docker image is pre-built by the workflow step "Build Docker image"
# Do NOT rebuild here - it can fail silently and exit before setting CHK_EXIT_CODE

# Run lintro check in Docker container against the entire project
# The .lintro-ignore file will automatically exclude test_samples/
# Note: pydoclint timeout increased for CI (Docker is slower than local)
# Lintro writes its full console output, structured reports, and console.log
# into .lintro/run-<timestamp>/ on the host (via the -v mount). Downstream jobs
# (fail-on-lint.sh, the PR comment job) consume report.md / console.log from
# that directory instead of scraping stdout, so no tee is needed here.
set +e # Don't exit on error
# Use the image entrypoint to invoke lintro directly; avoid shell passthrough
# Run with matching UID/GID to allow writes to mounted volume (e.g., bun install for node_modules)
# Enable auto-install for CI (uses --ignore-scripts for security)
# Set HOME=/tmp to ensure tools like semgrep can write config/cache files (no valid home dir for UID)
# Disable osv_scanner suppression probe here; the dedicated security scan job handles it
docker run --rm --user "$(id -u):$(id -g)" -e HOME=/tmp -e LINTRO_AUTO_INSTALL_DEPS=1 \
	-v "$PWD:/code" -w /code py-lintro:latest lintro check . \
	--tool-options "pydoclint:timeout=120,osv_scanner:check_suppressions=false"
CHK_EXIT_CODE=$?
set -e # Exit on error again

# Locate the newest run directory so we can surface its console.log in the
# GitHub step summary. The directory is created per-invocation by OutputManager.
LATEST_RUN_DIR=$(find .lintro -maxdepth 1 -type d -name 'run-*' -print0 2>/dev/null |
	xargs -0 ls -dt 2>/dev/null | head -n1)

{
	echo "### 📊 Linting Results:"
	echo '```'
	if [ -n "$LATEST_RUN_DIR" ] && [ -f "$LATEST_RUN_DIR/console.log" ]; then
		cat "$LATEST_RUN_DIR/console.log"
	else
		echo "No linting output captured"
	fi
	echo '```'
	echo ""
	echo "**Linting exit code:** $CHK_EXIT_CODE"
	echo ""
} >>"$GITHUB_STEP_SUMMARY"

# Store the exit code for the PR comment step
echo "CHK_EXIT_CODE=$CHK_EXIT_CODE" >>"$GITHUB_ENV"

{
	echo "### 📋 Summary"
	echo "- **Step 1:** Code quality checks performed with \`lintro check\` in Docker"
	echo "- **Test files:** Excluded via \`.lintro-ignore\`"
	echo ""
	echo "---"
	echo "🚀 **Lintro** provides a unified interface for multiple code quality tools!"
	echo "This ensures consistent formatting and linting across different file types."
} >>"$GITHUB_STEP_SUMMARY"

log_success "Docker lintro analysis completed with exit code $CHK_EXIT_CODE"

# Exit with the check exit code
exit "$CHK_EXIT_CODE"
