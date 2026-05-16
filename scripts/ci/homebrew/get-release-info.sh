#!/usr/bin/env bash
# SPDX-License-Identifier: MIT
# Purpose: Resolve the release tag and prerelease flag for Homebrew publishing.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=../../utils/utils.sh disable=SC1091
source "$SCRIPT_DIR/../../utils/utils.sh"

if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
	cat <<'EOF'
Resolve the release tag used by Homebrew publishing.

Usage: get-release-info.sh

Environment:
  GITHUB_EVENT_NAME          GitHub Actions event name.
  WORKFLOW_RUN_HEAD_BRANCH   Release tag from the upstream workflow_run event.
  GITHUB_OUTPUT              GitHub Actions output file.
  GH_TOKEN                   GitHub token for workflow_dispatch release lookup.

Outputs:
  tag             Release tag, including any leading v prefix.
  is_prerelease   true when the release version looks like a prerelease.
EOF
	exit 0
fi

EVENT_NAME="${GITHUB_EVENT_NAME:?GITHUB_EVENT_NAME is required}"

if [[ "$EVENT_NAME" == "workflow_run" ]]; then
	TAG="${WORKFLOW_RUN_HEAD_BRANCH:?WORKFLOW_RUN_HEAD_BRANCH is required}"
else
	TAG="$(gh release view --json tagName -q .tagName 2>/dev/null || true)"
fi

VERSION="${TAG#v}"
IS_PRERELEASE=false
if [[ "$VERSION" =~ (a|b|rc)[0-9]* ]]; then
	IS_PRERELEASE=true
fi

if [[ -n "${GITHUB_OUTPUT:-}" ]]; then
	{
		echo "tag=$TAG"
		echo "is_prerelease=$IS_PRERELEASE"
	} >>"$GITHUB_OUTPUT"
fi

log_info "Release tag: $TAG (prerelease: $IS_PRERELEASE)"
