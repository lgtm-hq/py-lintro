#!/usr/bin/env bash
set -euo pipefail

# run-ai-review.sh
#
# Dogfood `lintro review` on py-lintro's own pull requests. Runs an AI diff
# review over the PR, posts a rich sticky comment (and inline findings) to the
# PR via `--post`, and prints the JSON result to the log.
#
# NO-SILENT-SKIP (#1826): this script used to always exit 0, so the `AI Review`
# check read green even when the review aborted — for months it reported success
# while producing nothing, because the Anthropic balance was depleted. It now
# exits non-zero whenever no review was produced:
#
#   exit 0  a review was produced (findings or clean)
#   exit 1  no review was produced — no credential, dead credential, depleted
#           balance, unreachable provider, or a lintro-side failure
#
# The check is deliberately NOT required, so a billing condition is loud without
# blocking a merge. P1 findings still exit 0: the review ran, and findings are
# advisory here.
#
# Classification lives in scripts/ci/classify_review_outcome.py, which reads
# lintro's own machine-readable error envelope — the failure taxonomy is not
# re-implemented in shell.
#
# Transport: the review runs on the `cli` transport (pinned in
# enable_review_config.py), so the credential is CLAUDE_CODE_OAUTH_TOKEN — the
# `claude` binary's OAuth session — not ANTHROPIC_API_KEY, whose account has no
# balance (#1894). The missing-credential guard below checks that variable
# accordingly; checking the API key would report "no credential" on a perfectly
# authenticated run, and vice versa.
#
# Trusted install: the workflow checks out the PR's BASE ref (main) before
# invoking this script, so the lintro that runs with CLAUDE_CODE_OAUTH_TOKEN is
# trusted code — never the PR head. The PR diff is fetched independently by
# `lintro review --pr` via `gh` (GitHub API), so the PR's changes are reviewed
# as data and never executed with the token.
#
# Fork PRs never reach this script: the workflow's job guard requires the head
# repo to be the base repo, so an empty CLAUDE_CODE_OAUTH_TOKEN means the secret
# is genuinely missing — a visible failure, not a skip.
#
# Usage:
#   PR_NUMBER=<n> CLAUDE_CODE_OAUTH_TOKEN=<token> GH_TOKEN=<token> \
#     scripts/ci/run-ai-review.sh
#   scripts/ci/run-ai-review.sh <pr-number>
#
# Environment:
#   CLAUDE_CODE_OAUTH_TOKEN Claude Code OAuth token used by the `claude` CLI.
#                           Empty => visible failure.
#   PR_NUMBER               Pull request number (alternative to the argument).
#   GH_TOKEN                Token used by `gh` to fetch the PR diff.
#   GITHUB_TOKEN            Token used by lintro's `--post` to write comments.
#   GITHUB_REPOSITORY       owner/name; supplies --repo for `lintro review`.
#   AI_REVIEW_MAX_COST_USD  Optional spend cap (advisory under CLI transport).
#   GITHUB_STEP_SUMMARY     When set, the outcome is appended as Markdown.

if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
	cat <<'EOF'
Dogfood `lintro review` on a pull request (informational, not required).

Usage:
  PR_NUMBER=<n> scripts/ci/run-ai-review.sh
  scripts/ci/run-ai-review.sh <pr-number>

Exits 0 only when a review was actually produced. A missing or dead credential,
a depleted balance, or an unreachable provider exits 1 with a visible reason.
EOF
	exit 0
fi

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Sentinels understood by classify_review_outcome.py: the review was never
# invoked, either for want of a credential or for some other reason.
NO_CREDENTIAL_STATUS=-1
NOT_INVOKED_STATUS=-2

# Every "no review happened" path goes through the classifier rather than exiting
# directly, so each one produces the same annotation and job summary. An `exit 1`
# on its own reddens the check without saying why.
#
# python3, not `uv run`: the classifier is stdlib-only, so the visible-failure
# path must not depend on a synced virtualenv being present — which is exactly the
# kind of thing that breaks when a setup step is what failed.
report_not_invoked() {
	python3 "${script_dir}/classify_review_outcome.py" \
		--status "$NOT_INVOKED_STATUS" \
		--transport cli \
		--reason "$1"
	exit 1
}

pr_number="${1:-${PR_NUMBER:-}}"

if [[ -z "${CLAUDE_CODE_OAUTH_TOKEN:-}" ]]; then
	exec python3 "${script_dir}/classify_review_outcome.py" \
		--status "$NO_CREDENTIAL_STATUS" \
		--transport cli
fi

if [[ -z "$pr_number" ]]; then
	report_not_invoked "No PR number provided (set PR_NUMBER or pass it as an argument)."
fi

echo "Running AI review on PR #${pr_number} (posts comment)..."

# Enable AI review in the base-ref (trusted) checkout's config. `lintro review`
# reads ai.enabled only from .lintro-config.yaml (env/flag overrides for
# provider/model/transport land separately in #1970), so patch it here rather
# than passing non-existent flags. The config comes from the base ref, not the
# PR, so a PR cannot loosen the cost cap. Transport/provider and the CLI
# transport profile (timeout + advisory cost) are pinned too (#1923).
if ! uv run python "${script_dir}/enable_review_config.py"; then
	report_not_invoked "Could not enable AI review in .lintro-config.yaml; see the log above."
fi

# `--post` maintains the sticky review comment (and inline findings) on the PR.
# It needs GITHUB_TOKEN (write) and the repo; the diff is still fetched via `gh`.
repo_arg=()
if [[ -n "${GITHUB_REPOSITORY:-}" ]]; then
	repo_arg=(--repo "${GITHUB_REPOSITORY}")
fi

# Capture rather than stream: the classifier needs the JSON error envelope, and
# the full output is echoed straight afterwards so the log is unchanged.
output_file="$(mktemp)"
trap 'rm -f "$output_file"' EXIT

set +e
# Timeout comes from ai.transports.cli.timeout (default 900s), written by
# enable_review_config.py — no hand-tuned --timeout at this call site (#1923).
# The default ai.api_timeout (60s) is sized for streaming API chunks; a CLI
# turn runs the whole review in one `claude` invocation and needs minutes.
#
# COUPLED to ai-review.yml's `timeout-minutes`: the resolved CLI timeout must
# fire BEFORE the Actions runner kills the job, or the review dies without a
# JSON envelope and classify_review_outcome.py reads a truncated file.
# Invariant (enforced by tests/scripts/test_run_ai_review.py):
# ceil(cli_timeout / 60) + setup overhead (~7 min) + posting margin
# < timeout-minutes. Bump both together.
#
# CLI_REVIEW_TIMEOUT_SECONDS documents the profile default the job budget
# must cover (keep in sync with lintro.ai.transport.DEFAULT_CLI_TIMEOUT and
# enable_review_config.DEFAULT_CLI_TIMEOUT).
CLI_REVIEW_TIMEOUT_SECONDS=900
uv run lintro review --pr "${pr_number}" "${repo_arg[@]}" --depth 1 --post --output json >"$output_file" 2>&1
review_status=$?
set -e

cat "$output_file"

# Exits 0 only when a review was produced; the classifier writes the annotation
# and job summary either way. --transport names the failure vocabulary (#1923).
python3 "${script_dir}/classify_review_outcome.py" \
	--status "$review_status" \
	--transport cli \
	--output-file "$output_file"
