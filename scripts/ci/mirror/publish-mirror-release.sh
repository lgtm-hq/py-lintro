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
  * Refuses to run when the mirror has "Allow auto-merge" turned off — the
    merge would fail after the checks anyway; the failure names the setting
    and the PR to fix (#2742).
  * A stale bump branch is healed only when its PR cannot merge: an open,
    clean PR from an earlier run is reused (auto-merge re-armed and polled)
    instead of being thrown away; a dirty/closed one is deleted and
    recreated from current main (dirty-PR auto-heal).
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
	# App-form noreply email (ID + username) so the tag object is attributed
	# to the App account like the commit; the annotated tag itself stays
	# unsigned — the mirror rulesets govern commits, not tags.
	git config user.email \
		"${GIT_USER_EMAIL:-5047677+lgtm-mirror-bot[bot]@users.noreply.github.com}"
	if tag_exists_remote "$TAG"; then
		log_info "Tag ${TAG} already exists on the mirror; nothing to tag"
		return 0
	fi
	log_info "Tagging mirror ${TAG}"
	git tag -a "$TAG" -m "$TAG"
	git push origin "$TAG"
	log_success "Pushed mirror tag ${TAG}"
}

# --- merge without racing the required checks --------------------------------

require_auto_merge_enabled() {
	# `gh pr merge --auto` is refused by the API unless the repo setting is
	# on; fail fast naming the setting and where to flip it (#2742).
	if [[ "$(gh api "repos/${MIRROR_REPO}" --jq .allow_auto_merge)" != "true" ]]; then
		log_error "Auto-merge is disabled on ${MIRROR_REPO}; enable \"Allow auto-merge\" in the repo settings (owners) before the mirror bump can merge"
		exit 1
	fi
}

merge_mirror_pr() {
	require_auto_merge_enabled
	log_info "Enabling auto-merge (squash) for mirror PR #${pr_number}"
	# No --delete-branch: with --auto gh cannot delete the branch at the
	# later auto-merge moment, and the mirror keeps branches; the ref is
	# removed explicitly after MERGED is confirmed.
	gh pr merge "$pr_number" --squash --auto

	deadline=$((SECONDS + MERGE_TIMEOUT_SECONDS))
	while ((SECONDS < deadline)); do
		state="$(gh pr view "$pr_number" --json state --jq .state 2>/dev/null || echo UNKNOWN)"
		if [[ "$state" == "MERGED" ]]; then
			log_success "Mirror PR #${pr_number} merged"
			return 0
		fi
		# A closed PR can never merge; stop early instead of burning the
		# whole bound on it.
		if [[ "$state" == "CLOSED" ]]; then
			log_error "Mirror PR #${pr_number} was closed without merging: https://github.com/${MIRROR_REPO}/pull/${pr_number}"
			exit 1
		fi
		log_info "Mirror PR #${pr_number} state ${state}; waiting for the required checks"
		sleep "$((MERGE_POLL_SECONDS < deadline - SECONDS ? MERGE_POLL_SECONDS : deadline - SECONDS))"
	done

	log_error "Mirror PR #${pr_number} did not merge within ${MERGE_TIMEOUT_SECONDS}s; finish it by hand: https://github.com/${MIRROR_REPO}/pull/${pr_number}"
	exit 1
}

finish_after_merge() {
	# The bump branch is now part of main; drop the stale ref (the mirror
	# keeps branches on merge, so gh never deleted it).
	gh api -X DELETE "repos/${MIRROR_REPO}/git/refs/heads/${BRANCH}" >/dev/null 2>&1 || true

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

# Reuse-or-heal (#2742 review): a branch left over from an earlier run is
# only thrown away when its PR cannot merge. An open, clean PR (auto-merge
# still pending from a cancelled or timed-out run) is reused as-is; a dirty
# one would replay the same conflict every rerun and is healed by
# recreation from the mirror's current main, which cannot conflict. Its
# stale PR closes with the deleted branch.
existing_pr=""
if git ls-remote --heads origin "$BRANCH" | grep -q .; then
	existing_pr="$(gh pr list --head "$BRANCH" --state open --json number \
		--jq '.[0].number' || true)"
	if [[ -n "$existing_pr" ]]; then
		pr_state="$(gh pr view "$existing_pr" --json state,mergeStateStatus \
			--jq '.state + " " + .mergeStateStatus')"
		if [[ "$pr_state" == "OPEN CLEAN" || "$pr_state" == "OPEN BLOCKED" ]]; then
			log_info "Reusing open PR #${existing_pr} for ${BRANCH} (clean; re-arming auto-merge)"
			pr_number="$existing_pr"
			pr_url="https://github.com/${MIRROR_REPO}/pull/${pr_number}"
			merge_mirror_pr
			finish_after_merge
			exit 0
		fi
		log_warning "PR #${existing_pr} for ${BRANCH} is not mergeable (${pr_state}); healing the branch"
	else
		log_warning "Bump branch ${BRANCH} exists with no open PR; recreating it from origin/main"
	fi
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
			  variables: {
			    input: {
			      branch: { repositoryNameWithOwner: $repo, branchName: $branch },
			      expectedHeadOid: $oid,
			      message: { headline: $headline, body: $body },
			      fileChanges: { additions: [ { path: $path, contents: $contents } ] }
			    }
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

log_info "Opening mirror version-bump PR"
pr_url="$(
	gh pr create --base main --head "$BRANCH" \
		--title "$PR_TITLE" --body "$PR_BODY"
)"
pr_number="${pr_url##*/}"

# --- merge without racing the required checks --------------------------------

merge_mirror_pr
finish_after_merge
