#!/usr/bin/env bash
set -euo pipefail

# resolve-pipeline-relevance.sh
#
# Resolve whether the heavy CI pipeline is relevant for this run and write a
# `pipeline` output (true|false) plus a `lint-scope` output (full|changed) to
# GITHUB_OUTPUT for downstream job and step conditions.
#
# Non-pull_request events (push, merge_group, workflow_dispatch) always run
# the full pipeline with full-repo lint. pull_request events consult the JSON
# produced by the lgtm-hq/lgtm-ci detect-changes action (filter name ->
# boolean). Missing or unparsable JSON fails open (pipeline=true,
# lint-scope=full) so required checks run their full jobs instead of silently
# early-exiting.
#
# lint-scope resolves `changed` only when the PR diff explicitly missed the
# `full-lint` filter (no path with global lint impact was touched); every
# other case — non-PR events, filter hit, missing filter, unparsable JSON —
# resolves `full` so a filter regression can never narrow lint coverage.
#
# Usage:
#   EVENT_NAME=<event> CHANGES_JSON='{"pipeline":true,"full-lint":false}' \
#     scripts/ci/resolve-pipeline-relevance.sh

if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
	cat <<'EOF'
Resolve heavy-pipeline path relevance for GitHub Actions.

Usage:
  EVENT_NAME=<event> CHANGES_JSON='{"pipeline":true,"full-lint":false}' \
    scripts/ci/resolve-pipeline-relevance.sh

Environment:
  EVENT_NAME    github.event_name
  CHANGES_JSON  detect-changes `changes` output (JSON filter -> boolean);
                only consulted when EVENT_NAME is pull_request

Behavior:
  - Non-pull_request events always resolve pipeline=true (merge_group and
    push runs never path-skip) and lint-scope=full.
  - pull_request events resolve the `pipeline` filter from CHANGES_JSON.
  - pull_request events resolve lint-scope=changed only when the
    `full-lint` filter is explicitly false; anything else resolves full.
  - Missing or unparsable CHANGES_JSON fails open (pipeline=true,
    lint-scope=full).

Outputs (via GITHUB_OUTPUT):
  pipeline=true|false
  lint-scope=full|changed
EOF
	exit 0
fi

event_name="${EVENT_NAME:-}"
changes_json="${CHANGES_JSON:-}"
pipeline="true"
lint_scope="full"

if [[ "$event_name" == "pull_request" ]]; then
	# Plain -r (not -e): jq -e exits nonzero for a legitimate false value.
	if resolved="$(jq -r '.pipeline' <<<"$changes_json" 2>/dev/null)"; then
		case "$resolved" in
		true)
			pipeline="true"
			;;
		false)
			pipeline="false"
			;;
		*)
			echo "::warning::Unexpected pipeline filter value" \
				"'${resolved}'; failing open (pipeline=true)"
			pipeline="true"
			;;
		esac
	else
		echo "::warning::detect-changes JSON missing or unparsable;" \
			"failing open (pipeline=true)"
		pipeline="true"
	fi
	# lint-scope narrows to `changed` only on an explicit full-lint=false;
	# true, missing, or unparsable all keep full-repo lint (fail-safe: a
	# filter regression widens coverage, never narrows it).
	if resolved_full_lint="$(
		jq -r '."full-lint"' <<<"$changes_json" 2>/dev/null
	)"; then
		if [[ "$resolved_full_lint" == "false" ]]; then
			lint_scope="changed"
		elif [[ "$resolved_full_lint" == "true" && "$pipeline" == "false" ]]; then
			# Invariant guard: a path with global lint impact must never be
			# pipeline-skipped. The filter lists are kept in lockstep, but if
			# they ever drift, run the full pipeline rather than skipping the
			# dogfooding lint of a full-lint-relevant change.
			echo "::warning::full-lint filter matched while pipeline did" \
				"not; forcing pipeline=true (filter lists drifted)"
			pipeline="true"
		fi
	fi
fi

echo "event=${event_name:-<unset>} pipeline=${pipeline} lint-scope=${lint_scope}"

if [[ -n "${GITHUB_OUTPUT:-}" ]]; then
	{
		echo "pipeline=${pipeline}"
		echo "lint-scope=${lint_scope}"
	} >>"$GITHUB_OUTPUT"
else
	echo "pipeline=${pipeline}"
	echo "lint-scope=${lint_scope}"
fi
