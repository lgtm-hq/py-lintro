#!/usr/bin/env bash
# SPDX-License-Identifier: MIT
# For license details, see the repository root LICENSE file.
#
# tools-image-resolve-summary.sh - Append tools image resolution to GitHub step summary
#
# Environment Variables (required):
#   TOOLS_IMAGE           Full image reference resolved for this run
# Environment Variables (optional):
#   TOOLS_SOURCE          Where the image was resolved from (registry, artifact, stable)
#   TOOLS_CHANGED         "true" if tool files changed and a fresh image was built
#   GITHUB_STEP_SUMMARY   Path to GitHub Actions step summary file

set -euo pipefail

if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
	cat <<'EOF'
Append tools image resolution to the GitHub Actions step summary.

Usage:
  scripts/ci/tools-image-resolve-summary.sh

Environment Variables (required):
  TOOLS_IMAGE           Full image reference resolved for this run

Environment Variables (optional):
  TOOLS_SOURCE          Where the image was resolved from (registry, artifact, stable)
  TOOLS_CHANGED         "true" if tool files changed and a fresh image was built
  GITHUB_STEP_SUMMARY   Path to GitHub Actions step summary file
EOF
	exit 0
fi

: "${TOOLS_IMAGE:?TOOLS_IMAGE is required}"

TOOLS_SOURCE="${TOOLS_SOURCE:-unknown}"
TOOLS_CHANGED="${TOOLS_CHANGED:-false}"
GITHUB_STEP_SUMMARY="${GITHUB_STEP_SUMMARY:-/dev/null}"

{
	echo "## Tools Image Resolution"
	echo ""
	echo "- **Image:** \`${TOOLS_IMAGE}\`"
	echo "- **Source:** ${TOOLS_SOURCE}"
	echo "- **Changed:** ${TOOLS_CHANGED}"
} >>"$GITHUB_STEP_SUMMARY"
