#!/usr/bin/env bash
# SPDX-License-Identifier: MIT
# For license details, see the repository root LICENSE file.
#
# Report a scheduled workflow's verdict as one commit status on main's HEAD
# (#2603, epic #2598).
#
# A scheduled job's verdict lives only in the run list, which is how the
# weekly GHCR prune failed for five weeks without reaching anyone. A commit
# status puts the same verdict on the branch, where the checks tab shows it.
#
# Required environment:
#   GH_TOKEN         token with statuses: write
#   GITHUB_REPOSITORY, GITHUB_SHA, GITHUB_SERVER_URL, GITHUB_RUN_ID
#   STATUS_CONTEXT   the status context, e.g. ghcr-cleanup
#   JOB_RESULTS      whitespace-separated job results from the needs graph,
#                    e.g. "success failure skipped"

set -euo pipefail

if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
	cat <<'USAGE'
Report a scheduled workflow's verdict as a commit status on main's HEAD.

Usage:
  STATUS_CONTEXT=ghcr-cleanup JOB_RESULTS="success success" \
    scripts/ci/maintenance/report-workflow-commit-status.sh

Environment:
  GH_TOKEN         Token with `statuses: write`.
  STATUS_CONTEXT   Status context shown in the checks tab.
  JOB_RESULTS      Whitespace-separated results of the jobs being summarised
                   (success, failure, cancelled or skipped).
  GITHUB_REPOSITORY, GITHUB_SHA, GITHUB_SERVER_URL, GITHUB_RUN_ID

State: failure when any job failed or was cancelled, otherwise success. A
skipped job is neither: it did not run, so it says nothing about the lane.
USAGE
	exit 0
fi

: "${STATUS_CONTEXT:?STATUS_CONTEXT is required}"
: "${JOB_RESULTS:?JOB_RESULTS is required}"
: "${GITHUB_REPOSITORY:?GITHUB_REPOSITORY is required}"
: "${GITHUB_SHA:?GITHUB_SHA is required}"

state="success"
failed=0
total=0
for result in ${JOB_RESULTS}; do
	total=$((total + 1))
	case "${result}" in
	failure | cancelled)
		failed=$((failed + 1))
		state="failure"
		;;
	success | skipped) ;;
	*)
		echo "ERROR: unknown job result '${result}' in JOB_RESULTS" >&2
		exit 1
		;;
	esac
done

if [[ "${state}" == "failure" ]]; then
	description="${failed} of ${total} job(s) failed — see the run"
else
	description="all ${total} job(s) succeeded"
fi

target_url="${GITHUB_SERVER_URL:-https://github.com}/${GITHUB_REPOSITORY}/actions/runs/${GITHUB_RUN_ID:-}"

gh api \
	--method POST \
	"repos/${GITHUB_REPOSITORY}/statuses/${GITHUB_SHA}" \
	-f "state=${state}" \
	-f "context=${STATUS_CONTEXT}" \
	-f "description=${description}" \
	-f "target_url=${target_url}"

echo "${STATUS_CONTEXT}: ${state} (${description})"
