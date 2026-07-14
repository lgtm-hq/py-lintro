#!/usr/bin/env bash
set -euo pipefail

# resolve-pipeline-relevance.sh
#
# Resolve whether the heavy CI pipeline is relevant for this run and write a
# `pipeline` output (true|false) to GITHUB_OUTPUT for downstream job and step
# conditions.
#
# Non-pull_request events (push, merge_group, workflow_dispatch) always run
# the full pipeline. pull_request events consult the JSON produced by the
# lgtm-hq/lgtm-ci detect-changes action (filter name -> boolean). Missing or
# unparsable JSON fails open (pipeline=true) so required checks run their
# full jobs instead of silently early-exiting.
#
# Usage:
#   EVENT_NAME=<event> CHANGES_JSON='{"pipeline":true}' \
#     scripts/ci/resolve-pipeline-relevance.sh

if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
	cat <<'EOF'
Resolve heavy-pipeline path relevance for GitHub Actions.

Usage:
  EVENT_NAME=<event> CHANGES_JSON='{"pipeline":true}' \
    scripts/ci/resolve-pipeline-relevance.sh

Environment:
  EVENT_NAME    github.event_name
  CHANGES_JSON  detect-changes `changes` output (JSON filter -> boolean);
                only consulted when EVENT_NAME is pull_request

Behavior:
  - Non-pull_request events always resolve pipeline=true (merge_group and
    push runs never path-skip).
  - pull_request events resolve the `pipeline` filter from CHANGES_JSON.
  - Missing or unparsable CHANGES_JSON fails open (pipeline=true).

Outputs (via GITHUB_OUTPUT):
  pipeline=true|false
EOF
	exit 0
fi

event_name="${EVENT_NAME:-}"
changes_json="${CHANGES_JSON:-}"
pipeline="true"

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
fi

echo "event=${event_name:-<unset>} pipeline=${pipeline}"

if [[ -n "${GITHUB_OUTPUT:-}" ]]; then
	echo "pipeline=${pipeline}" >>"$GITHUB_OUTPUT"
else
	echo "pipeline=${pipeline}"
fi
