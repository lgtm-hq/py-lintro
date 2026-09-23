#!/usr/bin/env bash
# SPDX-License-Identifier: MIT
# Purpose: Bump the lintro pin in the lintro-pre-commit mirror, open+merge a
#          version-bump PR, and tag the mirror with the matching release version.
#
# The bump commit is created through the GitHub API (GraphQL
# createCommitOnBranch) with a lgtm-mirror-bot App token, so GitHub signs it
# and attributes it to the App account — the mirror's rulesets require both
# (#2742). The merge uses --auto plus a bounded poll, so the required checks
# decide the merge instead of racing them.

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
  MIRROR_DIR     Checkout of the lintro-pre-commit mirror (default: current dir).
  BUMP_SCRIPT    Path to bump_pin.py (default: alongside this script).
  GH_TOKEN       lgtm-mirror-bot App installation token with contents +
                 pull-requests write on the mirror repo (required).
  MIRROR_REPO    owner/name of the mirror repository (default: lgtm-hq/lintro-pre-commit).
  MERGE_TIMEOUT_SECONDS  Bound for the auto-merge poll (default: 900).
  MERGE_POLL_SECONDS     Interval for the auto-merge poll (default: 20).

Behavior:
  * Rewrites pyproject.toml's lintro pin to <version>.
  * If already pinned (idempotent re-run), ensures the vX.Y.Z tag exists and
    exits 0 without opening a PR.
  * Otherwise recreates the bump branch on the mirror's current main via
    createCommitOnBranch (signed + attributed), opens the PR, enables
    auto-merge, polls until MERGED, then tags vX.Y.Z on main.
  * A stale bump branch from a failed earlier run is deleted and recreated
    (dirty-PR auto-heal); its open PR closes with the branch.
EOF
	exit 0
fi

VERSION="${1:?Version is required}"
VERSION="${VERSION#v}"
TAG="v${VERSION}"
MIRROR_DIR="${MIRROR_DIR:-.}"
BUMP_SCRIPT="${BUMP_SCRIPT:-$SCRIPT_DIR/bump_pin.py}"
MIRROR_REPO="${MIRROR_REPO:-lgtm-hq/lintro-pre-commit}"
MERGE_TIMEOUT_SECONDS="${MERGE_TIMEOUT_SECONDS:-900}"
MERGE_POLL_SECONDS="${MERGE_POLL_SECONDS:-20}"
BRANCH="mirror/bump-lintro-${VERSION}"

: "${GH_TOKEN:?GH_TOKEN is required}"

cd "$MIRROR_DIR"

tag_exists_remote() {
	git ls-remote --tags origin "refs/tags/$1" | grep -q .
}

push_tag() {
	# git tag -a needs a tagger identity; the runner's checkout has none
	# (the commit itself is API-created and needs no local identity).
	git config user.name "${GIT_USER_NAME:-lgtm-mirror-bot[bot]}"
	git config user.email "${GIT_USER_EMAIL:-lgtm-mirror-bot[bot]@users.noreply.github.com}"
	if tag_exists_remote "$TAG"; then
		log_info "Tag ${TAG} already exists on the mirror; nothing to tag"
		return 0
	fi
	log_info "Tagging mirror ${TAG}"
	git tag -a "$TAG" -m "$TAG"
	git push origin "$TAG"
	log_success "Pushed mirror tag ${TAG}"
}

# Sync to the mirror's current main BEFORE touching pyproject.toml: the pin
# check must judge the exact commit the tag will land on, not a stale
# checkout (#2742 review).
git fetch origin main --quiet
git checkout -q main
git reset -q --hard origin/main

log_info "Bumping lintro pin to ${VERSION}"
python3 "$BUMP_SCRIPT" --pyproject pyproject.toml --version "$VERSION"

if git diff --quiet -- pyproject.toml; then
	log_info "Mirror already pins lintro==${VERSION}; ensuring tag exists"
	push_tag
	exit 0
fi

# --- create the bump branch on current main (API-created commit) ------------

base_oid="$(git rev-parse origin/main)"

# Dirty-PR auto-heal (#2742): a branch left over from a failed earlier run
# replays the same conflict every rerun. Delete it and recreate from the
# mirror's current main; its stale PR closes with the branch.
if git ls-remote --heads origin "$BRANCH" | grep -q .; then
	log_warning "Bump branch ${BRANCH} already exists; recreating it from origin/main"
	gh api -X DELETE "repos/${MIRROR_REPO}/git/refs/heads/${BRANCH}"
fi

# createCommitOnBranch appends to an EXISTING branch (CommittableBranch
# .branchName: "the branch to append the commit to"), so the ref must exist
# at base_oid first — on fresh runs and after the heal alike.
log_info "Creating ${BRANCH} at ${base_oid}"
gh api -X POST "repos/${MIRROR_REPO}/git/refs" \
	-f ref="refs/heads/${BRANCH}" -f sha="$base_oid" >/dev/null

log_info "Creating bump commit on ${BRANCH} via createCommitOnBranch"
# The mutation and its input travel as one JSON payload on gh's stdin; no
# quoting layer ever sees the base64 file contents.
created_oid="$(
	{
		jq -n \
			--rawfile query <(
				cat <<'GRAPHQL'
mutation($input: CreateCommitOnBranchInput!) {
	createCommitOnBranch(input: $input) {
		commit {
			oid
		}
	}
}
GRAPHQL
			) \
			--arg repo "$MIRROR_REPO" \
			--arg branch "${BRANCH}" \
			--arg oid "$base_oid" \
			--arg headline "chore: bump lintro to ${VERSION}" \
			--arg body "Sync the pinned lintro wheel to the ${TAG} py-lintro release.

Refs lgtm-hq/py-lintro (mirror-release automation)" \
			--arg path "pyproject.toml" \
			--arg contents "$(base64 <pyproject.toml | tr -d '\n')" \
			'{
			  query: $query,
			  input: {
			    branch: { repositoryNameWithOwner: $repo, branchName: $branch },
			    expectedHeadOid: $oid,
			    message: { headline: $headline, body: $body },
			    fileChanges: { additions: [ { path: $path, contents: $contents } ] }
			  }
			}'
	} | gh api graphql --input - |
		jq -r '.data.createCommitOnBranch.commit.oid'
)"
if [[ -z "$created_oid" || "$created_oid" == "null" ]]; then
	log_error "createCommitOnBranch returned no commit; the bump branch was not created"
	exit 1
fi
log_success "Created bump commit ${created_oid} (GitHub-signed, attributed to the App)"

# --- open the PR -------------------------------------------------------------

PR_TITLE="chore: bump lintro to ${VERSION}"
PR_BODY="Automated version bump: pins the published \`lintro==${VERSION}\` wheel to match py-lintro ${TAG}. Merged and tagged \`${TAG}\` by mirror-release automation."

# Always open a fresh PR: the branch was just created (fresh run) or its old
# PR closed when the heal deleted the branch — no open PR can exist for it.
log_info "Opening mirror version-bump PR"
pr_url="$(
	gh pr create --base main --head "$BRANCH" \
		--title "$PR_TITLE" --body "$PR_BODY"
)"
pr_number="${pr_url##*/}"

# --- merge without racing the required checks --------------------------------

log_info "Enabling auto-merge (squash) for mirror PR #${pr_number}"
gh pr merge "$pr_number" --squash --delete-branch --auto

deadline=$((SECONDS + MERGE_TIMEOUT_SECONDS))
while ((SECONDS < deadline)); do
	state="$(gh pr view "$pr_number" --json state --jq .state 2>/dev/null || echo UNKNOWN)"
	if [[ "$state" == "MERGED" ]]; then
		log_success "Mirror PR #${pr_number} merged"
		break
	fi
	log_info "Mirror PR #${pr_number} state ${state}; waiting ${MERGE_POLL_SECONDS}s for the required checks"
	sleep "$MERGE_POLL_SECONDS"
done

if [[ "${state:-UNKNOWN}" != "MERGED" ]]; then
	log_error "Mirror PR #${pr_number} did not merge within ${MERGE_TIMEOUT_SECONDS}s; finish it by hand: https://github.com/${MIRROR_REPO}/pull/${pr_number}"
	exit 1
fi

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
