#!/usr/bin/env bash
# SPDX-License-Identifier: MIT
# For license details, see the repository root LICENSE file.
#
# Report one provider smoke row as a commit status on main's HEAD (#2600).
#
# A scheduled job's verdict lives only in the run list, which is why the CLI
# smoke it replaces failed every Monday for a month without reaching anyone.
# A commit status puts the same verdict on the branch, where the checks tab and
# the badge show it.
#
# Three states, and the middle one is the point:
#
#   success  the provider answered
#   pending  the row's secret is not configured — nothing was called, so
#            nothing may look green
#   failure  the provider failed, credit exhaustion included
#
# Required environment:
#   GH_TOKEN       token with statuses: write
#   GITHUB_REPOSITORY, GITHUB_SHA, GITHUB_SERVER_URL, GITHUB_RUN_ID
#   SMOKE_NAME     table row name, e.g. anthropic-api
#   SMOKE_KEY_ENV  the row's credential variable NAME, quoted in the skip
#   SMOKE_OUTCOME  the smoke script's own outcome output (may be empty)
#   SMOKE_STEP     the smoke step's outcome, used when the script wrote none

set -euo pipefail

if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
	cat <<'EOF'
Report one provider API smoke row as a commit status on main's HEAD.

Usage:
  SMOKE_NAME=anthropic-api SMOKE_OUTCOME=success \
    scripts/ci/ai_provider_smoke/report-commit-status.sh

Environment:
  GH_TOKEN       Token with `statuses: write`.
  SMOKE_NAME     Table row name; becomes the context ai-provider-smoke/<name>.
  SMOKE_KEY_ENV  The row's credential variable NAME, quoted in a skip status.
  SMOKE_OUTCOME  The smoke script's outcome: success, failure or skipped.
  SMOKE_STEP     The smoke step's outcome, used when the script wrote none.
                 'skipped' there means the step never ran (an earlier step
                 failed) and reports pending; anything else is a failure.
  GITHUB_REPOSITORY, GITHUB_SHA, GITHUB_SERVER_URL, GITHUB_RUN_ID

States: success (the provider answered), pending (nothing was called, so nothing
may look green — either the row's credential variable is empty or the smoke step
never ran because an earlier step failed) and failure (anything else, credit
exhaustion included).
EOF
	exit 0
fi

: "${SMOKE_NAME:?SMOKE_NAME is required}"
: "${GITHUB_REPOSITORY:?GITHUB_REPOSITORY is required}"
: "${GITHUB_SHA:?GITHUB_SHA is required}"

outcome="${SMOKE_OUTCOME:-}"
if [[ -z "$outcome" ]]; then
	# The script never reported. Two different things hide behind that, and
	# only one of them is about the provider:
	#
	#   * the step ran and died (or reported nothing) — a failure about this
	#     provider, which is what an empty outcome used to mean; and
	#   * the step never ran at all, because an earlier step in the job
	#     failed. Nothing was asked of the provider, so nothing may be said
	#     about it: that reports *pending*, like an unset credential, but
	#     with its own description so the checks tab does not claim a
	#     credential is missing when one is sitting right there.
	if [[ "${SMOKE_STEP:-}" == "skipped" ]]; then
		outcome="infra"
	else
		outcome="failure"
	fi
fi

case "$outcome" in
success)
	state="success"
	description="provider answered the smoke prompt"
	;;
skipped)
	state="pending"
	description="skipped: no credential in ${SMOKE_KEY_ENV:-unknown}"
	;;
infra)
	state="pending"
	description="infra failure before the smoke ran — see the run"
	;;
*)
	state="failure"
	description="smoke failed — see the run for the provider error"
	;;
esac

target_url="${GITHUB_SERVER_URL:-https://github.com}/${GITHUB_REPOSITORY}/actions/runs/${GITHUB_RUN_ID:-}"

gh api \
	--method POST \
	"repos/${GITHUB_REPOSITORY}/statuses/${GITHUB_SHA}" \
	-f "state=${state}" \
	-f "context=ai-provider-smoke/${SMOKE_NAME}" \
	-f "description=${description}" \
	-f "target_url=${target_url}"

echo "ai-provider-smoke/${SMOKE_NAME}: ${state} (${description})"
