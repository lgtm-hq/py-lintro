#!/usr/bin/env bash
# SPDX-License-Identifier: MIT
# For license details, see the repository root LICENSE file.
#
# Detect tool file changes that require a fresh tools image build.
# Used by resolve-tools-image action.
#
# Required environment variables:
#   GITHUB_EVENT_NAME     - "push" or "pull_request"
#   GITHUB_OUTPUT         - Path to GitHub outputs file
# Required for pull_request:
#   PR_BASE_SHA           - Base commit SHA for PR
#   PR_HEAD_SHA           - Head commit SHA for PR
# Required for push:
#   GITHUB_EVENT_BEFORE   - Before SHA for push event

set -euo pipefail

if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
	cat <<'EOF'
Detect tool file changes that require a fresh tools image build.

Usage:
  scripts/ci/tools-image-detect-changes.sh

Environment Variables (required):
  GITHUB_EVENT_NAME   GitHub event name (push or pull_request)
  GITHUB_OUTPUT       Path to GitHub output file

Environment Variables (for pull_request):
  PR_BASE_SHA         Base commit SHA for PR
  PR_HEAD_SHA         Head commit SHA for PR

Environment Variables (for push):
  GITHUB_EVENT_BEFORE Before SHA for push event

Outputs (to GITHUB_OUTPUT):
  tools_changed       "true" if tool files changed, "false" otherwise

Example:
  GITHUB_EVENT_NAME=push GITHUB_OUTPUT=/tmp/out GITHUB_EVENT_BEFORE=abc123 \
    ./scripts/ci/tools-image-detect-changes.sh
EOF
	exit 0
fi

: "${GITHUB_EVENT_NAME:?GITHUB_EVENT_NAME is required}"
: "${GITHUB_OUTPUT:?GITHUB_OUTPUT is required}"

# Define tool-related file patterns that require CI to use a freshly built
# tools image. This list must stay aligned with the reusable workflow's
# workflow_call change detection so resolve-tools picks the same image that the
# build stage produced.
TOOL_PATTERNS=(
	"Dockerfile.tools"
	"scripts/utils/install-tools.sh"
	"package.json"
	"lintro/_tool_versions.py"
	"lintro/tools/manifest.json"
	".github/workflows/tools-image.yml"
)

matches_tool_pattern() {
	local changed_file="$1"
	local pattern

	for pattern in "${TOOL_PATTERNS[@]}"; do
		if [[ "$changed_file" == "$pattern" ]]; then
			echo "$pattern"
			return 0
		fi
	done

	if [[ "$changed_file" == scripts/ci/tools-image-*.sh ]]; then
		echo "scripts/ci/tools-image-*.sh"
		return 0
	fi

	return 1
}

tools_changed="false"

if [[ "$GITHUB_EVENT_NAME" == "pull_request" ]]; then
	: "${PR_BASE_SHA:?PR_BASE_SHA is required for pull_request events}"
	: "${PR_HEAD_SHA:?PR_HEAD_SHA is required for pull_request events}"

	# For PRs, compare base to head
	echo "Checking for tool file changes in PR..."
	changed_files=$(git diff --name-only "$PR_BASE_SHA" "$PR_HEAD_SHA" \
		2>/dev/null || echo "")

	while IFS= read -r changed_file; do
		[[ -z "$changed_file" ]] && continue
		if matched_pattern=$(matches_tool_pattern "$changed_file"); then
			echo "Found tool file change matching: $matched_pattern ($changed_file)"
			tools_changed="true"
			break
		fi
	done <<<"$changed_files"
elif [[ "$GITHUB_EVENT_NAME" == "push" ]]; then
	# For push events, check if tool files changed in the pushed commits
	echo "Checking for tool file changes in push..."
	# Get the before/after from the push event
	BEFORE_SHA="${GITHUB_EVENT_BEFORE:-}"
	ZERO_SHA="0000000000000000000000000000000000000000"
	if [[ -n "$BEFORE_SHA" ]] && [[ "$BEFORE_SHA" != "$ZERO_SHA" ]]; then
		changed_files=$(git diff --name-only "$BEFORE_SHA" HEAD \
			2>/dev/null || echo "")
		while IFS= read -r changed_file; do
			[[ -z "$changed_file" ]] && continue
			if matched_pattern=$(matches_tool_pattern "$changed_file"); then
				echo "Found tool file change matching: $matched_pattern ($changed_file)"
				tools_changed="true"
				break
			fi
		done <<<"$changed_files"
	fi
else
	echo "Event type: $GITHUB_EVENT_NAME, using stable image"
fi

echo "tools_changed=${tools_changed}" >>"$GITHUB_OUTPUT"
echo "Tools changed: ${tools_changed}"

if [[ "$tools_changed" == "true" ]]; then
	if [[ "$GITHUB_EVENT_NAME" == "pull_request" ]]; then
		echo "::notice::Tool files changed — fresh tools image will be built via workflow_call"
	else
		echo "::notice::Tool files changed — tools-image.yml will build a fresh image"
	fi
else
	echo "::notice::No tool file changes detected — stable pinned image will be used"
fi
