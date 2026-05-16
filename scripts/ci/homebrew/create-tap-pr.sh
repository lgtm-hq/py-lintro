#!/usr/bin/env bash
# SPDX-License-Identifier: MIT
# Purpose: Commit Homebrew tap updates to a branch and enable PR auto-merge.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=../../utils/utils.sh disable=SC1091
source "$SCRIPT_DIR/../../utils/utils.sh"

if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
	cat <<'EOF'
Create or update a Homebrew tap PR and enable auto-merge.

Usage: create-tap-pr.sh <file-pattern>... <commit-message> [--skip-if-empty]

Options:
  --skip-if-empty  Exit 0 if there are no changes (default: error)

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

if [[ "$#" -lt 2 ]]; then
	log_error "At least one file pattern and a commit message are required"
	exit 1
fi

SKIP_IF_EMPTY=""
if [[ "${!#}" == "--skip-if-empty" ]]; then
	SKIP_IF_EMPTY="--skip-if-empty"
	set -- "${@:1:$(($# - 1))}"
fi

if [[ "$#" -lt 2 ]]; then
	log_error "At least one file pattern and a commit message are required"
	exit 1
fi

COMMIT_MESSAGE="${!#}"
FILE_PATTERNS=("${@:1:$(($# - 1))}")
WORKING_DIR="${WORKING_DIR:-.}"
PR_BASE="${PR_BASE:-main}"

: "${PR_BRANCH:?PR_BRANCH is required}"
: "${PR_TITLE:?PR_TITLE is required}"
: "${PR_BODY:?PR_BODY is required}"
: "${GH_TOKEN:?GH_TOKEN is required}"

cd "$WORKING_DIR"

git config user.name "lgtm-homebrew-tap[bot]"
git config user.email "lgtm-homebrew-tap[bot]@users.noreply.github.com"

log_info "Staging changes: ${FILE_PATTERNS[*]}"
git add -- "${FILE_PATTERNS[@]}"

if git diff --staged --quiet; then
	if [[ "$SKIP_IF_EMPTY" == "--skip-if-empty" ]]; then
		log_info "No changes to commit, skipping"
		exit 0
	fi

	log_error "No changes to commit"
	exit 1
fi

log_info "Creating update commit"
git checkout -B "$PR_BRANCH"
git commit -m "$COMMIT_MESSAGE"

log_info "Pushing branch: $PR_BRANCH"
git push --force-with-lease origin "HEAD:$PR_BRANCH"

existing_pr="$(
	gh pr list \
		--head "$PR_BRANCH" \
		--base "$PR_BASE" \
		--state open \
		--json number \
		--jq '.[0].number // ""'
)"

if [[ -n "$existing_pr" ]]; then
	log_info "Using existing Homebrew tap PR #$existing_pr"
	pr_number="$existing_pr"
else
	log_info "Creating Homebrew tap PR"
	pr_url="$(
		gh pr create \
			--base "$PR_BASE" \
			--head "$PR_BRANCH" \
			--title "$PR_TITLE" \
			--body "$PR_BODY"
	)"
	pr_number="${pr_url##*/}"
fi

log_info "Enabling auto-merge for Homebrew tap PR #$pr_number"
gh pr merge "$pr_number" --auto --squash --delete-branch

if [[ -n "${GITHUB_OUTPUT:-}" ]]; then
	tap_repository="$(gh repo view --json nameWithOwner --jq .nameWithOwner)"
	echo "pr-number=$pr_number" >>"$GITHUB_OUTPUT"
	echo "pr-url=${GITHUB_SERVER_URL}/${tap_repository}/pull/${pr_number}" >>"$GITHUB_OUTPUT"
fi

log_success "Homebrew tap PR ready: #$pr_number"
