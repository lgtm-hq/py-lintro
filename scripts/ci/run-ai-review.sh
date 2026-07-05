#!/usr/bin/env bash
set -euo pipefail

# run-ai-review.sh
#
# Dogfood `lintro review` on py-lintro's own pull requests. Runs an AI diff
# review over the PR and prints the JSON result to the log. Informational only:
# this script always exits 0 so it can never fail a pull request check.
#
# It gracefully skips (logs a message and exits 0) when ANTHROPIC_API_KEY is
# empty. That covers both "the repo secret is not configured yet" and fork PRs
# (which cannot access repository secrets), so merging the workflow never
# breaks CI.
#
# Usage:
#   PR_NUMBER=<n> ANTHROPIC_API_KEY=<key> GH_TOKEN=<token> \
#     scripts/ci/run-ai-review.sh
#   scripts/ci/run-ai-review.sh <pr-number>
#
# Environment:
#   ANTHROPIC_API_KEY       Anthropic API key. Empty => graceful skip.
#   PR_NUMBER               Pull request number (alternative to the argument).
#   GH_TOKEN                Token used by `gh` to fetch the PR diff.
#   GITHUB_REPOSITORY       owner/name; supplies --repo for `lintro review`.
#   AI_REVIEW_MAX_COST_USD  Optional spend cap (defaults handled downstream).

if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
	cat <<'EOF'
Dogfood `lintro review` on a pull request (informational, non-blocking).

Usage:
  PR_NUMBER=<n> scripts/ci/run-ai-review.sh
  scripts/ci/run-ai-review.sh <pr-number>

Skips gracefully (exit 0) when ANTHROPIC_API_KEY is empty.
EOF
	exit 0
fi

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

pr_number="${1:-${PR_NUMBER:-}}"

if [[ -z "${ANTHROPIC_API_KEY:-}" ]]; then
	echo "ANTHROPIC_API_KEY is not set — skipping AI review (informational only)."
	echo "Add the ANTHROPIC_API_KEY repository secret to activate AI review on PRs."
	exit 0
fi

if [[ -z "$pr_number" ]]; then
	echo "No PR number provided (set PR_NUMBER or pass it as an argument)." >&2
	echo "Skipping AI review — nothing to review." >&2
	exit 0
fi

echo "Running AI review on PR #${pr_number} (informational, non-blocking)..."

# Enable AI review in the ephemeral CI checkout's config. `lintro review` reads
# ai.enabled and ai.max_cost_usd only from .lintro-config.yaml, so patch it here
# rather than passing non-existent flags. Transport/provider are pinned too.
uv run python "${script_dir}/enable_review_config.py"

# Never let a P1 finding (exit 1) or any review error fail the PR check.
set +e
review_output="$(uv run lintro review --pr "${pr_number}" --depth 1 --output json 2>&1)"
review_status=$?
set -e

printf '%s\n' "$review_output"

if [[ "$review_status" -ne 0 ]]; then
	echo "lintro review exited with status ${review_status} " \
		"(findings or error) — informational only, not failing the PR."
fi

exit 0
