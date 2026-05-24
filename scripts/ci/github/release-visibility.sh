#!/usr/bin/env bash
set -euo pipefail

if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
	cat <<'EOF'
release visibility helper:
  write_summary       Write release trigger context to $GITHUB_STEP_SUMMARY
  notify_failure      Create or update a GitHub issue for main release failures
EOF
	exit 0
fi

issue_marker() {
	local branch
	branch="$(release_branch)"
	echo "<!-- release-automation-failure:${GITHUB_WORKFLOW:-Release - Automated PR Creation}:${branch} -->"
}

release_branch() {
	if [[ "${GITHUB_EVENT_NAME:-}" == "workflow_run" ]]; then
		echo "${UPSTREAM_HEAD_BRANCH:-${GITHUB_REF_NAME:-unknown}}"
	else
		echo "${GITHUB_REF_NAME:-unknown}"
	fi
}

release_sha() {
	if [[ -n "${CHECKOUT_SHA:-}" ]]; then
		echo "$CHECKOUT_SHA"
	elif [[ "${GITHUB_EVENT_NAME:-}" == "workflow_run" && -n "${UPSTREAM_HEAD_SHA:-}" ]]; then
		echo "$UPSTREAM_HEAD_SHA"
	else
		echo "${GITHUB_SHA:-unknown}"
	fi
}

run_url() {
	echo "${GITHUB_SERVER_URL:-https://github.com}/${GITHUB_REPOSITORY:?GITHUB_REPOSITORY is required}/actions/runs/${GITHUB_RUN_ID:?GITHUB_RUN_ID is required}"
}

upstream_run_url() {
	if [[ -n "${UPSTREAM_RUN_URL:-}" ]]; then
		echo "$UPSTREAM_RUN_URL"
	elif [[ -n "${UPSTREAM_RUN_ID:-}" ]]; then
		echo "${GITHUB_SERVER_URL:-https://github.com}/${GITHUB_REPOSITORY:?GITHUB_REPOSITORY is required}/actions/runs/${UPSTREAM_RUN_ID}"
	else
		echo ""
	fi
}

write_summary() {
	local summary_file="${GITHUB_STEP_SUMMARY:?GITHUB_STEP_SUMMARY is required}"
	local branch
	local sha
	local current_run_url
	local upstream_url
	branch="$(release_branch)"
	sha="$(release_sha)"
	current_run_url="$(run_url)"
	upstream_url="$(upstream_run_url)"

	{
		echo "## Release Automation Context"
		echo ""
		echo "- **Workflow:** ${GITHUB_WORKFLOW:-Release - Automated PR Creation}"
		echo "- **Event:** ${GITHUB_EVENT_NAME:-unknown}"
		echo "- **Branch:** ${branch}"
		echo "- **Checkout SHA:** ${sha}"
		echo "- **Actor:** ${GITHUB_ACTOR:-unknown}"
		echo "- **Run:** ${current_run_url}"
		if [[ "${GITHUB_EVENT_NAME:-}" == "workflow_run" ]]; then
			echo ""
			echo "### Upstream Workflow"
			echo ""
			echo "- **Workflow:** ${UPSTREAM_WORKFLOW_NAME:-unknown}"
			echo "- **Run ID:** ${UPSTREAM_RUN_ID:-unknown}"
			if [[ -n "$upstream_url" ]]; then
				echo "- **Run:** ${upstream_url}"
			fi
			echo "- **Conclusion:** ${UPSTREAM_CONCLUSION:-unknown}"
			echo "- **Head branch:** ${UPSTREAM_HEAD_BRANCH:-unknown}"
			echo "- **Head SHA:** ${UPSTREAM_HEAD_SHA:-unknown}"
		fi
	} >>"$summary_file"
}

failed_step_summary() {
	if ! command -v gh >/dev/null 2>&1; then
		echo "Failed job and step details unavailable because gh is not installed."
		return
	fi

	local failed
	# gh --jq receives this expression literally; shell variables inside it are jq variables.
	# shellcheck disable=SC2016
	failed=$(
		gh run view "${GITHUB_RUN_ID:?GITHUB_RUN_ID is required}" \
			--repo "${GITHUB_REPOSITORY:?GITHUB_REPOSITORY is required}" \
			--json jobs \
			--jq '.jobs[] | select(.conclusion == "failure") | .name as $job | if ([.steps[]? | select(.conclusion == "failure")] | length) > 0 then .steps[]? | select(.conclusion == "failure") | "- **Job:** " + $job + "\n  **Step:** " + .name else "- **Job:** " + $job + "\n  **Step:** unavailable" end' \
			2>/dev/null || true
	)

	if [[ -n "$failed" ]]; then
		echo "$failed"
	else
		echo "Failed job and step details unavailable. See the run logs."
	fi
}

render_failure_body() {
	local branch
	local sha
	local current_run_url
	local upstream_url
	local marker
	branch="$(release_branch)"
	sha="$(release_sha)"
	current_run_url="$(run_url)"
	upstream_url="$(upstream_run_url)"
	marker="$(issue_marker)"

	cat <<EOF
$marker

## Summary

Release automation failed after the normal \`main\` checks completed. This issue keeps post-merge release failures visible outside the Actions history.

## Failure Context

- **Workflow:** ${GITHUB_WORKFLOW:-Release - Automated PR Creation}
- **Event:** ${GITHUB_EVENT_NAME:-unknown}
- **Branch:** ${branch}
- **SHA:** ${sha}
- **Actor:** ${GITHUB_ACTOR:-unknown}
- **Run:** ${current_run_url}
EOF

	if [[ "${GITHUB_EVENT_NAME:-}" == "workflow_run" ]]; then
		cat <<EOF

## Upstream Workflow

- **Workflow:** ${UPSTREAM_WORKFLOW_NAME:-unknown}
- **Run ID:** ${UPSTREAM_RUN_ID:-unknown}
EOF
		if [[ -n "$upstream_url" ]]; then
			echo "- **Run:** ${upstream_url}"
		fi
		cat <<EOF
- **Conclusion:** ${UPSTREAM_CONCLUSION:-unknown}
- **Head branch:** ${UPSTREAM_HEAD_BRANCH:-unknown}
- **Head SHA:** ${UPSTREAM_HEAD_SHA:-unknown}
EOF
	fi

	cat <<EOF

## Failed Job or Step

$(failed_step_summary)

## Suggested Next Action

Open the failed run, inspect the failed step logs, and either fix the release automation failure or close this issue with the run URL if the failure was transient.
EOF
}

find_existing_issue() {
	local failure_marker
	failure_marker="$(issue_marker)"
	gh issue list \
		--repo "${GITHUB_REPOSITORY:?GITHUB_REPOSITORY is required}" \
		--state open \
		--label release \
		--label automation \
		--search "$failure_marker" \
		--json number \
		--jq '.[0].number // empty'
}

notify_failure() {
	local main_branch="${MAIN_BRANCH:-main}"
	local branch
	branch="$(release_branch)"

	if [[ "$branch" != "$main_branch" ]]; then
		echo "Release failure notification skipped for branch '$branch'."
		return
	fi

	if ! command -v gh >/dev/null 2>&1; then
		echo "ERROR: gh CLI is required to report release automation failures." >&2
		exit 1
	fi

	local body_file
	body_file="$(mktemp)"
	trap 'rm -f "$body_file"' EXIT
	render_failure_body >"$body_file"

	local existing_issue
	existing_issue="$(find_existing_issue)"
	if [[ -n "$existing_issue" ]]; then
		gh issue comment "$existing_issue" \
			--repo "${GITHUB_REPOSITORY:?GITHUB_REPOSITORY is required}" \
			--body-file "$body_file" >/dev/null
		echo "Updated release failure issue #${existing_issue}."
	else
		gh issue create \
			--repo "${GITHUB_REPOSITORY:?GITHUB_REPOSITORY is required}" \
			--title "fix(release): release automation failed on ${branch}" \
			--label bug \
			--label ci \
			--label release \
			--label automation \
			--label infrastructure \
			--body-file "$body_file" >/dev/null
		echo "Created release failure issue."
	fi

	rm -f "$body_file"
	trap - EXIT
}

"$@"
