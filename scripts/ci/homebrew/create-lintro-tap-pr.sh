#!/usr/bin/env bash
# SPDX-License-Identifier: MIT
# Purpose: Open a single Homebrew tap PR for whichever lintro formulas were generated.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=../../utils/utils.sh disable=SC1091
source "$SCRIPT_DIR/../../utils/utils.sh"

if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
	cat <<'EOF'
Create or update the lintro Homebrew tap PR.

Usage: create-lintro-tap-pr.sh <version> [--skip-if-empty]

Arguments:
  version          Lintro version being published.

Options:
  --skip-if-empty  Exit 0 if there are no changes.

Environment:
  WORKING_DIR   Tap checkout directory (default: current directory)
  PR_BRANCH     Branch to push in the tap repo
  PR_TITLE      Pull request title
  PR_BODY       Pull request body
  PR_BASE       Pull request base branch (default: main)
  GH_TOKEN      GitHub token with contents and pull-requests write permissions
EOF
	exit 0
fi

VERSION="${1:?Version is required}"
SKIP_IF_EMPTY="${2:-}"
WORKING_DIR="${WORKING_DIR:-.}"
FORMULA_PATHS=("Formula/lintro.rb")

if [[ -n "$SKIP_IF_EMPTY" && "$SKIP_IF_EMPTY" != "--skip-if-empty" ]]; then
	log_error "Unknown option: $SKIP_IF_EMPTY"
	exit 1
fi

if [[ -f "$WORKING_DIR/Formula/lintro-bin.rb" ]]; then
	FORMULA_PATHS+=("Formula/lintro-bin.rb")
fi

ARGS=(
	"${FORMULA_PATHS[@]}"
	"chore(homebrew): update lintro to ${VERSION}"
)
if [[ -n "$SKIP_IF_EMPTY" ]]; then
	ARGS+=("$SKIP_IF_EMPTY")
fi

"$SCRIPT_DIR/create-tap-pr.sh" "${ARGS[@]}"
