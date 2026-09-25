#!/usr/bin/env bash
# SPDX-License-Identifier: MIT
# Purpose: Bump the lintro pin in the lintro-pre-commit mirror, open+merge a
#          version-bump PR, and tag the mirror with the matching release version.
#
# The bump commit is created by lgtm-ci's shared
# scripts/ci/git/create-signed-commit.sh (reset mode, GraphQL
# createCommitOnBranch) with a lgtm-mirror-bot App token, so GitHub signs it
# and attributes it to the App account — the mirror's rulesets require both
# (#2742, #2834). The merge uses --auto plus a bounded poll, so the required
# checks decide the merge instead of racing them.

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
  LGTM_CI_TOOLING_DIR  Checkout of lgtm-hq/lgtm-ci (scripts/ci/) providing
                 scripts/ci/git/create-signed-commit.sh (required when a
                 bump commit is needed).
  MERGE_TIMEOUT_SECONDS  Bound for the auto-merge poll (default: 900).
  MERGE_POLL_SECONDS     Interval for the auto-merge poll (default: 20).

Behavior:
  * Rewrites pyproject.toml's lintro pin to <version>.
  * If already pinned (idempotent re-run), ensures the vX.Y.Z tag exists and
    exits 0 without opening a PR.
  * Otherwise resets the bump branch to the mirror's current main plus the
    bump commit via lgtm-ci's create-signed-commit.sh --mode reset (signed +
    attributed), opens the PR, enables auto-merge, polls until MERGED, then
    tags vX.Y.Z on main.
  * Refuses to run when the mirror has "Allow auto-merge" turned off — the
    merge would fail after the checks anyway; the failure names the setting
    and the PR to fix (#2742).
  * A stale bump branch is healed only when its PR cannot merge: an open,
    clean PR from an earlier run is reused (auto-merge re-armed and polled)
    instead of being thrown away; a dirty one (or one against another
    base) is deleted, closing its PR, and recreated from current main
    (dirty-PR auto-heal). A leftover branch with no open PR is reset in
    place.
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
	# on; fail fast naming the setting and where to flip it (#2742). The
	# API output is captured first so a failed call dies with its own
	# message instead of an empty result reading as "disabled".
	local setting
	if ! setting="$(gh api "repos/${MIRROR_REPO}" --jq .allow_auto_merge)"; then
		log_error "Could not read the auto-merge setting on ${MIRROR_REPO}; refusing to continue without knowing it (#2742)"
		exit 1
	fi
	if [[ "$setting" != "true" ]]; then
		log_error "Auto-merge is disabled on ${MIRROR_REPO}; enable \"Allow auto-merge\" in the repo settings (owners) before the mirror bump can merge"
		exit 1
	fi
}

arm_auto_merge() {
	# On the reuse path the earlier run may already have armed auto-merge;
	# `gh pr merge --auto` on an armed PR errors, so only arm when unset.
	local armed
	armed="$(gh pr view "$pr_number" --json autoMergeRequest \
		--jq '.autoMergeRequest != null')"
	if [[ "$armed" == "true" ]]; then
		log_info "Auto-merge already armed for mirror PR #${pr_number}"
	else
		log_info "Enabling auto-merge (squash) for mirror PR #${pr_number}"
		# No --delete-branch: with --auto gh cannot delete the branch at the
		# later auto-merge moment, and the mirror keeps branches; the ref is
		# removed explicitly after MERGED is confirmed.
		gh pr merge "$pr_number" --squash --auto
	fi
}

poll_until_merged() {
	deadline=$((SECONDS + MERGE_TIMEOUT_SECONDS))
	while :; do
		# Poll both fields: a CLOSED PR can never merge, and a DIRTY merge
		# state means the queued auto-merge would fail — stop early with
		# the PR URL instead of burning the bound on either.
		read -r state merge_state <<<"$(gh pr view "$pr_number" \
			--json state,mergeStateStatus \
			--jq '.state + " " + (.mergeStateStatus // "UNKNOWN")' \
			2>/dev/null || echo "UNKNOWN UNKNOWN")"
		if [[ "$state" == "MERGED" ]]; then
			log_success "Mirror PR #${pr_number} merged"
			return 0
		fi
		if [[ "$state" == "CLOSED" ]]; then
			log_error "Mirror PR #${pr_number} was closed without merging: https://github.com/${MIRROR_REPO}/pull/${pr_number}"
			exit 1
		fi
		if [[ "$merge_state" == "DIRTY" ]]; then
			log_error "Mirror PR #${pr_number} is DIRTY (merge conflict): https://github.com/${MIRROR_REPO}/pull/${pr_number}"
			exit 1
		fi
		log_info "Mirror PR #${pr_number} state ${state}/${merge_state}; waiting for the required checks"
		remaining=$((deadline - SECONDS))
		if ((remaining <= 0)); then
			break
		fi
		sleep "$((MERGE_POLL_SECONDS < remaining ? MERGE_POLL_SECONDS : remaining))"
	done

	log_error "Mirror PR #${pr_number} did not merge within ${MERGE_TIMEOUT_SECONDS}s; finish it by hand: https://github.com/${MIRROR_REPO}/pull/${pr_number}"
	exit 1
}

merge_mirror_pr() {
	arm_auto_merge
	poll_until_merged
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

# The shared signed-commit script must be present before any mirror write:
# a missing checkout would otherwise fail after the branch heal (#2834).
if [[ -z "${LGTM_CI_TOOLING_DIR:-}" ]]; then
	log_error "LGTM_CI_TOOLING_DIR is not set; point it at a checkout of lgtm-hq/lgtm-ci (scripts/ci/)"
	exit 1
fi
SIGNED_COMMIT_SCRIPT="${LGTM_CI_TOOLING_DIR}/scripts/ci/git/create-signed-commit.sh"
if [[ ! -f "$SIGNED_COMMIT_SCRIPT" ]]; then
	log_error "lgtm-ci create-signed-commit script not found at ${SIGNED_COMMIT_SCRIPT}; check the LGTM_CI_TOOLING_DIR checkout"
	exit 1
fi

# A disabled auto-merge setting fails the run here — after "a bump is
# needed" is known, but BEFORE any mirror mutation (branch heal, temp
# branch, signed commit, PR): nothing is written for a run that cannot
# merge (#2742).
require_auto_merge_enabled

# --- create the bump branch on current main (API-created commit) ------------

base_oid="$(git rev-parse origin/main)"

# Reuse-or-heal (#2742 review): a branch left over from an earlier run is
# only thrown away when its PR cannot merge. An open PR against main
# (auto-merge still pending from a cancelled or timed-out run) is reused
# as-is unless its merge state is DIRTY: a dirty PR would replay the same
# conflict every rerun and is healed by recreation from the mirror's
# current main, which cannot conflict. Its stale PR closes with the deleted
# branch. A leftover branch with no open PR is not deleted: reset mode
# moves it. A failed `pr list` fails the run — guessing "no PR" would
# rewrite the branch under the very PR that needs reusing or healing.
existing_pr=""
if git ls-remote --heads origin "$BRANCH" | grep -q .; then
	existing_pr="$(gh pr list --head "$BRANCH" --base main --state open --json number \
		--jq '.[0].number // ""')"
	if [[ -n "$existing_pr" ]]; then
		pr_info="$(gh pr view "$existing_pr" --json state,mergeStateStatus,baseRefName \
			--jq '.state + " " + (.mergeStateStatus // "UNKNOWN") + " " + .baseRefName')"
		read -r pr_state pr_merge_state pr_base <<<"$pr_info"
		# Any open PR against main whose merge state is not DIRTY (CLEAN,
		# BLOCKED, UNKNOWN, UNSTABLE, BEHIND, HAS_HOOKS) is
		# mergeable-in-waiting; only a dirty PR — or one targeting another
		# base, which merging would not update main — heals.
		if [[ "$pr_state" == "OPEN" && "$pr_merge_state" != "DIRTY" && "$pr_base" == "main" ]]; then
			log_info "Reusing open PR #${existing_pr} for ${BRANCH} (${pr_state} ${pr_merge_state})"
			pr_number="$existing_pr"
			pr_url="https://github.com/${MIRROR_REPO}/pull/${pr_number}"
			merge_mirror_pr
			finish_after_merge
			exit 0
		fi
		# Deleting the branch closes the unmergeable PR, so the fresh PR
		# below never inherits its cached DIRTY state or foreign base.
		log_warning "PR #${existing_pr} for ${BRANCH} is not mergeable (${pr_info}); healing the branch"
		gh api -X DELETE "repos/${MIRROR_REPO}/git/refs/heads/${BRANCH}"
	else
		# No PR to close: reset mode below moves the stale branch onto
		# origin/main plus the bump commit in one step, no delete needed.
		log_warning "Bump branch ${BRANCH} exists with no open PR; resetting it onto origin/main"
	fi
fi

# lgtm-ci's shared create-signed-commit script in reset mode makes the bump
# branch exactly base_oid plus the bump commit: the commit is made on a
# short-lived signed-commit-tmp/* branch, then the bump branch is created or
# force-moved to it in one step (never parked at base), and a failed commit
# leaves it untouched. Run from the mirror checkout: --file paths are read
# from the current directory.
log_info "Creating bump commit on ${BRANCH} via lgtm-ci create-signed-commit (reset onto ${base_oid})"
commit_output="$(
	bash "$SIGNED_COMMIT_SCRIPT" \
		--mode reset \
		--base "$base_oid" \
		--branch "$BRANCH" \
		--repository "$MIRROR_REPO" \
		--message "chore: bump lintro to ${VERSION}" \
		--body "Sync the pinned lintro wheel to the ${TAG} py-lintro release.

Refs lgtm-hq/py-lintro (mirror-release automation)" \
		--file pyproject.toml
)"
created_oid="$(sed -n 's/^commit-sha=//p' <<<"$commit_output" | tail -n 1)"
if [[ -z "$created_oid" ]]; then
	log_error "create-signed-commit reported no commit-sha; the bump branch was not updated"
	exit 1
fi
log_success "Created bump commit ${created_oid} (GitHub-signed, attributed to the App)"

# --- open the PR -------------------------------------------------------------

PR_TITLE="chore: bump lintro to ${VERSION}"
PR_BODY="Automated version bump: pins the published \`lintro==${VERSION}\` wheel to match py-lintro ${TAG}. Merged and tagged \`${TAG}\` by mirror-release automation."

# Always open a fresh PR: the branch was just created (fresh run), had no
# open PR (reset in place), or its old PR closed when the heal deleted the
# branch — no open PR can exist for it.
log_info "Opening mirror version-bump PR"
pr_url="$(
	gh pr create --base main --head "$BRANCH" \
		--title "$PR_TITLE" --body "$PR_BODY"
)"
pr_number="${pr_url##*/}"

# --- merge without racing the required checks --------------------------------

merge_mirror_pr
finish_after_merge
