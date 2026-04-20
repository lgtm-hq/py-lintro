#!/usr/bin/env bash
# SPDX-License-Identifier: MIT
# For license details, see the repository root LICENSE file.
#
# tools-image-commit-digest.sh - Commit updated tools image digest back to main
#
# Called by tools-image.yml after a new image is built and pushed to GHCR.
# Updates the pinned digest in repo files (Dockerfile, action.yml, docker-compose.yml)
# and commits the result back so the stable-image reference stays in sync automatically.
#
# Without this step the digest in .github/actions/resolve-tools-image/action.yml
# drifts whenever tool versions change, causing SKIP results in subsequent CI runs
# on PRs that don't touch tool files (they inherit the stale stable image).
#
# Usage (called from tools-image.yml):
#   DIGEST=sha256:<64-hex> \
#   GITHUB_TOKEN=<token> \
#   GITHUB_REPOSITORY=owner/repo \
#     scripts/ci/tools-image-commit-digest.sh
#
# Environment Variables:
#   DIGEST              New image digest (sha256:<64-hex-chars>)
#   GITHUB_TOKEN        Token used to authenticate the git push
#   GITHUB_REPOSITORY   Repository slug (owner/repo) — set automatically by GitHub Actions
#   BUILT_COMMIT        Commit SHA the image was built from; validated against origin/main
#                       to prevent pinning a stale digest onto an incompatible main HEAD

set -euo pipefail

if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
	cat <<'EOF'
Commit updated tools image digest back to main after a build.

Usage:
  scripts/ci/tools-image-commit-digest.sh

Environment Variables (required):
  DIGEST              New image digest (sha256:<64-hex-chars>)
  GITHUB_TOKEN        Token used to authenticate the git push
  GITHUB_REPOSITORY   Repository slug (owner/repo) — set automatically by GitHub Actions
  BUILT_COMMIT        Commit SHA the image was built from (github.sha in the workflow)

Called by tools-image.yml pin-digest job after the image is built and pushed.
Updates Dockerfile, action.yml, and docker-compose.yml (if present) via
tools-image-update-digest.sh, then commits and pushes the result to main.

Uses reset --hard rather than rebase to avoid merge conflicts in the concurrent-build
scenario (two simultaneous tools builds both trying to pin their digest). If a
concurrent run already committed the same or a newer digest, apply_update detects the
no-op and exits cleanly without a redundant commit.

The commit message includes [skip ci] to prevent the digest-only commit from
triggering a full CI pipeline run.
EOF
	exit 0
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

: "${DIGEST:?DIGEST env var is required}"
: "${GITHUB_TOKEN:?GITHUB_TOKEN env var is required}"
: "${GITHUB_REPOSITORY:?GITHUB_REPOSITORY env var is required}"
: "${BUILT_COMMIT:?BUILT_COMMIT env var is required}"

echo "Updating pinned digest to: $DIGEST"

git config user.name "github-actions[bot]"
git config user.email "github-actions[bot]@users.noreply.github.com"

# actions/checkout uses persist-credentials: false — authenticate via token URL
git remote set-url origin \
	"https://x-access-token:${GITHUB_TOKEN}@github.com/${GITHUB_REPOSITORY}.git"

# apply_update: fetch origin/main, reset hard to it, run the update script, and
# stage the result. Returns 0 if there are staged changes ready to commit, 1 if
# the digest is already current (idempotent no-op).
#
# Using reset --hard instead of rebase avoids merge conflicts entirely: if a
# concurrent pin-digest run already committed a digest to main, we reset to that
# state and re-apply our own digest. If the result is identical (same build, same
# digest), git diff is clean and we exit without a duplicate commit.
apply_update() {
	git fetch origin main

	# Validate that BUILT_COMMIT is an ancestor of (or equal to) origin/main.
	# If main has diverged — i.e., a force-push or branch reset moved it to a
	# commit that does not include the build commit — refuse to pin the digest,
	# since we cannot guarantee the image matches what is on main.
	if ! git merge-base --is-ancestor "$BUILT_COMMIT" origin/main; then
		echo "::error::BUILT_COMMIT ($BUILT_COMMIT) is not an ancestor of origin/main" \
			"— refusing to pin a digest built from an unrelated commit"
		exit 1
	fi

	git reset --hard origin/main

	"$SCRIPT_DIR/tools-image-update-digest.sh" "$DIGEST"

	if git diff --quiet; then
		echo "Digest already up-to-date — nothing to commit."
		return 1
	fi

	# Stage only the files the update script is known to touch
	git add Dockerfile .github/actions/resolve-tools-image/action.yml

	# docker-compose.yml is optional — stage if it exists and was modified
	if [[ -f docker-compose.yml ]] && ! git diff --quiet -- docker-compose.yml; then
		git add docker-compose.yml
	fi

	return 0
}

# Apply update onto latest main; exit early if already current
if ! apply_update; then
	exit 0
fi

# [skip ci] prevents this digest-only commit from triggering a full CI pipeline run
git commit -m "chore(ci): pin tools image digest after build [skip ci]"

# Push with retry — another commit may land between our fetch and push.
# On non-fast-forward: reset to origin/main and re-apply from scratch.
# If a concurrent pin-digest already committed the same digest, apply_update
# returns 1 (no-op) and we exit cleanly without a duplicate commit.
# Any other push error (auth, permissions, branch protection) fails immediately.
MAX_RETRIES=3
attempt=0
while true; do
	# set +e: prevent errexit from aborting before push_exit is captured;
	# the assignment exit-status equals git push's exit-status under set -e,
	# so a failed push would kill the script before the retry logic runs.
	set +e
	push_output=$(git push origin HEAD:main 2>&1)
	push_exit=$?
	set -e

	if [[ $push_exit -eq 0 ]]; then
		break
	fi

	if echo "$push_output" | grep -qE "non-fast-forward|failed to push some refs"; then
		attempt=$((attempt + 1))
		if [[ $attempt -ge $MAX_RETRIES ]]; then
			echo "::error::Failed to push digest commit after $MAX_RETRIES attempts (non-fast-forward)"
			echo "$push_output"
			exit 1
		fi
		echo "Push rejected (attempt $attempt/$MAX_RETRIES) — resetting to origin/main and re-applying..."
		if ! apply_update; then
			echo "Digest already committed by a concurrent run — nothing to do."
			exit 0
		fi
		git commit -m "chore(ci): pin tools image digest after build [skip ci]"
	else
		echo "::error::Push failed with a non-retryable error:"
		echo "$push_output"
		exit 1
	fi
done

echo "Digest commit pushed to main."
