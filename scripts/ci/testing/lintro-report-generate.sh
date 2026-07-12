#!/usr/bin/env bash
set -e

# Lintro Report Generation Script (Docker)
# Generates comprehensive lintro reports by running lintro inside a Docker container.
# Expects py-lintro:latest locally (pulled via pull-lintro-image.sh from sha-<commit>).

# Show help if requested
if [ "$1" = "--help" ] || [ "$1" = "-h" ]; then
	echo "Usage: $0 [--help]"
	echo ""
	echo "Lintro Report Generation Script (Docker)"
	echo "Generates comprehensive Lintro reports for GitHub Actions."
	echo ""
	echo "Features:"
	echo "  - Runs Lintro analysis once (markdown format)"
	echo "  - Reuses output for both step summary and report artifact"
	echo "  - Lists available tools in GitHub Actions summary"
	echo ""
	echo "Requires: py-lintro:latest Docker image (pull via pull-lintro-image.sh first)"
	echo "This script is designed to be run in GitHub Actions CI environment."
	exit 0
fi

# Source shared utilities
# SC1091: path is dynamically constructed, file exists at runtime
# shellcheck disable=SC1091
source "$(dirname "$0")/../../utils/utils.sh"

# Common docker run flags matching ci-lintro.sh pattern:
# --user: match host UID/GID for volume writes
# HOME=/tmp: tools like semgrep need a writable home for cache files
# Use a bash array to preserve argument boundaries (handles paths with spaces)
DOCKER_RUN=(docker run --rm --user "$(id -u):$(id -g)" -e HOME=/tmp -v "$PWD:/code" -w /code py-lintro:latest)

# Run analysis once in markdown format, reuse for both summary and artifact.
# This halves peak memory usage and runtime vs running lintro check twice.
mkdir -p lintro-report

# Capture the lintro check output and exit code separately so we can
# distinguish Docker/runtime failures from lint issues (non-zero exit but
# valid report content). Docker reserves exit codes 125 (daemon error),
# 126 (container command cannot be invoked), and 127 (command not found).
LINTRO_OUTPUT=$(mktemp)
LINTRO_RC=0
"${DOCKER_RUN[@]}" lintro check . --output-format markdown \
	--exclude "$EXCLUDE_DIRS" \
	--tool-options pydoclint:timeout=120 >"$LINTRO_OUTPUT" 2>&1 || LINTRO_RC=$?

if [ "$LINTRO_RC" -ne 0 ] && { [ ! -s "$LINTRO_OUTPUT" ] ||
	[ "$LINTRO_RC" -eq 125 ] || [ "$LINTRO_RC" -eq 126 ] || [ "$LINTRO_RC" -eq 127 ]; }; then
	# Container or runtime failure
	echo "::error::Docker/runtime failure running lintro check (exit code $LINTRO_RC):" >&2
	cat "$LINTRO_OUTPUT" >&2
	rm -f "$LINTRO_OUTPUT"
	exit 1
fi

{
	echo "# Lintro Report - $(date)"
	echo ""
	cat "$LINTRO_OUTPUT"
} >lintro-report/report.md
rm -f "$LINTRO_OUTPUT"

# Build step summary from the single analysis run
container_note="py-lintro:latest (sha-pinned via pull-lintro-image.sh)"
if [ "${LINTRO_IMAGE_FALLBACK:-}" = "true" ]; then
	container_note="py-lintro:latest (fallback: requested \`${LINTRO_IMAGE_REQUESTED_SHA:-unknown}\`, resolved \`${LINTRO_IMAGE_RESOLVED_SHA:-unknown}\`)"
fi

{
	echo "## 🔧 Lintro Full Codebase Report"
	echo ""
	echo "**Generated on:** $(date)"
	echo "**Container:** ${container_note}"
	echo ""
	echo "### 📋 Available Tools"
	echo '```'
	"${DOCKER_RUN[@]}" lintro list-tools
	echo '```'
	echo ""
	echo "### 🔍 Analysis Results"
	echo ""
	# Drop the first two lines (title and blank line) to keep summary heading hierarchy clean
	tail -n +3 lintro-report/report.md
} >>"$GITHUB_STEP_SUMMARY"

log_success "Lintro report generated successfully"
log_info "The report is available as a workflow artifact"
log_info "Download it from the Actions tab in the GitHub repository"
