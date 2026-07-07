#!/usr/bin/env bash
# SPDX-License-Identifier: MIT
# Purpose: Resolve the release tag/version for the lintro-pre-commit mirror bump.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=../../utils/utils.sh disable=SC1091
source "$SCRIPT_DIR/../../utils/utils.sh"

if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
	cat <<'EOF'
Resolve the release tag/version used by the lintro-pre-commit mirror bump.

Usage: resolve-version.sh

Environment:
  RELEASE_TAG    Release tag from the release event or workflow_dispatch input.
  GITHUB_OUTPUT  GitHub Actions output file.

Outputs:
  tag             Release tag, including any leading v prefix (e.g. v0.69.0).
  version         Version without the leading v (e.g. 0.69.0).
  is_prerelease   true when the version looks like a prerelease.
EOF
	exit 0
fi

TAG="${RELEASE_TAG:?RELEASE_TAG is required}"

# Trim surrounding whitespace, then fail fast if the tag is still empty.
TAG="${TAG#"${TAG%%[![:space:]]*}"}"
TAG="${TAG%"${TAG##*[![:space:]]}"}"
: "${TAG:?Release tag is required but was empty or whitespace-only}"

VERSION="${TAG#v}"
IS_PRERELEASE=false
if [[ "$VERSION" =~ (a|b|rc)[0-9]+ ]]; then
	IS_PRERELEASE=true
fi

if [[ -n "${GITHUB_OUTPUT:-}" ]]; then
	{
		echo "tag=$TAG"
		echo "version=$VERSION"
		echo "is_prerelease=$IS_PRERELEASE"
	} >>"$GITHUB_OUTPUT"
fi

log_info "Mirror release tag: $TAG (version: $VERSION, prerelease: $IS_PRERELEASE)"
