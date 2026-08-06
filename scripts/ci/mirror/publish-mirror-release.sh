#!/usr/bin/env bash
# SPDX-License-Identifier: MIT
# Purpose: Bump the lintro pin in the lintro-pre-commit mirror, open+merge a
#          version-bump PR, and tag the mirror with the matching release version.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=../../utils/utils.sh disable=SC1091
source "$SCRIPT_DIR/../../utils/utils.sh"

if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
	cat <<'EOF'
Publish a lintro-pre-commit mirror release for a py-lintro version.

Usage: publish-mirror-release.sh <version>

Arguments:
  version        Released lintro version without a leading v (e.g. 0.69.0).

Environment:
  MIRROR_DIR    Checkout of the lintro-pre-commit mirror (default: current dir).
  BUMP_SCRIPT   Path to bump_pin.py (default: alongside this script).
  GH_TOKEN      Token with contents + pull-requests write on the mirror repo.
  GIT_USER_NAME   Commit author name (default: lgtm-release-bot).
  GIT_USER_EMAIL  Commit author email.

Behavior:
  * Rewrites pyproject.toml's lintro pin to <version>.
  * If already pinned (idempotent re-run), ensures the vX.Y.Z tag exists and
    exits 0 without opening a PR.
  * Otherwise opens a version-bump PR, merges it, then tags vX.Y.Z on main.
EOF
	exit 0
fi

VERSION="${1:?Version is required}"
VERSION="${VERSION#v}"
TAG="v${VERSION}"
MIRROR_DIR="${MIRROR_DIR:-.}"
BUMP_SCRIPT="${BUMP_SCRIPT:-$SCRIPT_DIR/bump_pin.py}"
GIT_USER_NAME="${GIT_USER_NAME:-lgtm-release-bot}"
GIT_USER_EMAIL="${GIT_USER_EMAIL:-lgtm-release-bot@users.noreply.github.com}"
BRANCH="mirror/bump-lintro-${VERSION}"

: "${GH_TOKEN:?GH_TOKEN is required}"

cd "$MIRROR_DIR"

git config user.name "$GIT_USER_NAME"
git config user.email "$GIT_USER_EMAIL"

log_info "Bumping lintro pin to ${VERSION}"
python3 "$BUMP_SCRIPT" --pyproject pyproject.toml --version "$VERSION"

tag_exists_remote() {
	git ls-remote --tags origin "refs/tags/$1" | grep -q .
}

push_tag() {
	if tag_exists_remote "$TAG"; then
		log_info "Tag ${TAG} already exists on the mirror; nothing to tag"
		return 0
	fi
	log_info "Tagging mirror ${TAG}"
	git tag -a "$TAG" -m "$TAG"
	git push origin "$TAG"
	log_success "Pushed mirror tag ${TAG}"
}

if git diff --quiet -- pyproject.toml; then
	log_info "Mirror already pins lintro==${VERSION}; ensuring tag exists"
	git fetch origin main --quiet
	git checkout -q main
	git reset -q --hard origin/main
	push_tag
	exit 0
fi

log_info "Creating version-bump branch ${BRANCH}"
git checkout -B "$BRANCH"
git add pyproject.toml
git commit -m "chore: bump lintro to ${VERSION}

Sync the pinned lintro wheel to the ${TAG} py-lintro release.

Refs lgtm-hq/py-lintro (mirror-release automation)"

log_info "Pushing branch ${BRANCH}"
git push --force-with-lease origin "HEAD:$BRANCH"

PR_TITLE="chore: bump lintro to ${VERSION}"
PR_BODY="Automated version bump: pins the published \`lintro==${VERSION}\` wheel to match py-lintro ${TAG}. Merged and tagged \`${TAG}\` by mirror-release automation."

existing_pr="$(
	gh pr list --head "$BRANCH" --base main --state open \
		--json number --jq '.[0].number // ""'
)"

if [[ -n "$existing_pr" ]]; then
	log_info "Reusing open mirror PR #${existing_pr}"
	pr_number="$existing_pr"
else
	log_info "Opening mirror version-bump PR"
	pr_url="$(
		gh pr create --base main --head "$BRANCH" \
			--title "$PR_TITLE" --body "$PR_BODY"
	)"
	pr_number="${pr_url##*/}"
fi

log_info "Merging mirror PR #${pr_number}"
gh pr merge "$pr_number" --squash --admin --delete-branch

git fetch origin main --quiet
git checkout -q main
git reset -q --hard origin/main
push_tag

if [[ -n "${GITHUB_OUTPUT:-}" ]]; then
	{
		echo "pr-number=$pr_number"
		echo "tag=$TAG"
	} >>"$GITHUB_OUTPUT"
fi

log_success "Mirror release ${TAG} published (PR #${pr_number})"
