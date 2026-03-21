#!/usr/bin/env bash
set -euo pipefail

# ci-auto-fix.sh
# Auto-format using lintro inside Docker and push changes back to PR branch

if [ "${1:-}" = "--help" ] || [ "${1:-}" = "-h" ]; then
	echo "Usage: scripts/ci/ci-auto-fix.sh"
	echo ""
	echo "Run 'lintro format' inside Docker and push fixes back to PR branch."
	echo "Environment:"
	echo "  HEAD_REF            PR head ref (required for pull_request)"
	echo "  GITHUB_EVENT_NAME   GitHub event name (pull_request or push)"
	exit 0
fi

HEAD_REF=${HEAD_REF:-}
GITHUB_EVENT_NAME=${GITHUB_EVENT_NAME:-}
GITHUB_REPOSITORY=${GITHUB_REPOSITORY:-}
GITHUB_TOKEN=${GITHUB_TOKEN:-}
PR_HEAD_REPO_FULL_NAME=${PR_HEAD_REPO_FULL_NAME:-}
GITHUB_STEP_SUMMARY=${GITHUB_STEP_SUMMARY:-/dev/null}

# Guard: only run on pull_request events
if [ "$GITHUB_EVENT_NAME" != "pull_request" ]; then
	echo "Not a pull_request event; skipping auto-fix."
	exit 0
fi

# Guard: require same-repo PR (no pushes for forks)
if [ -z "$PR_HEAD_REPO_FULL_NAME" ]; then
	echo "PR_HEAD_REPO_FULL_NAME not provided; assuming fork and skipping."
	exit 0
fi

if [ "$PR_HEAD_REPO_FULL_NAME" != "$GITHUB_REPOSITORY" ]; then
	echo "Head repo $PR_HEAD_REPO_FULL_NAME != $GITHUB_REPOSITORY; skipping push."
	exit 0
fi

# Guard: require HEAD_REF and token
if [ -z "$HEAD_REF" ] || [ -z "$GITHUB_TOKEN" ]; then
	echo "Missing HEAD_REF or GITHUB_TOKEN; cannot push."
	exit 0
fi

git config user.name "github-actions[bot]"
git config user.email "41898282+github-actions[bot]@users.noreply.github.com"

docker run --rm \
	-v "$PWD:/code" \
	-w /code \
	py-lintro:latest \
	lintro format . --output-format grid || true

CHANGED=$(git status --porcelain)
if [ -n "$CHANGED" ]; then
	echo "Changes detected after formatting:"
	git --no-pager diff --name-status

	{
		printf '\n### 🧹 Auto-format changes\n'
		echo "The following files were modified by lintro format:"
		echo '```'
		git --no-pager diff --name-status
		echo '```'
	} >>"$GITHUB_STEP_SUMMARY" || true

	# Stage all formatting changes — lintro only touches files it's configured to handle
	git add -A

	if git diff --cached --quiet; then
		echo "No changes to commit after staging."
		exit 0
	fi

	git commit -m "style: auto-format via Lintro"

	# Use token-authenticated remote to avoid permission issues
	git remote set-url origin "https://x-access-token:${GITHUB_TOKEN}@github.com/${GITHUB_REPOSITORY}.git"
	git push origin HEAD:"$HEAD_REF"
	echo "Auto-format changes pushed back to PR branch."
	{
		printf '\n✅ Auto-format commit pushed back to PR branch (%s).\n' "${HEAD_REF}"
	} >>"$GITHUB_STEP_SUMMARY" || true
else
	echo "No changes after formatting."
	printf '\nNo auto-format changes detected.\n' >>"$GITHUB_STEP_SUMMARY" || true
fi
