#!/usr/bin/env bash
set -euo pipefail

# resolve-pipeline-relevance.sh
#
# Resolve whether the heavy CI pipeline is relevant for this run and write a
# `pipeline` output (true|false), a `lint-scope` output (full|changed), and a
# `skip-reason` output (human-readable, empty when pipeline=true) to
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
# RELEASE_BUMP (#1362): when release-bump-only.sh verified the PR as an
# automated version-bump PR (diff limited to the version stamp + CHANGELOG),
# the pipeline is skipped even though pyproject.toml hits both the pipeline
# and full-lint filters — the diff allowlist is a stronger guarantee than
# the path filters, so this override intentionally outranks the full-lint
# drift guard below. Only the exact string "true" skips; anything else
# (empty, "false", garbage, a failed bump step) keeps the resolved value.
#
# Usage:
#   EVENT_NAME=<event> CHANGES_JSON='{"pipeline":true,"full-lint":false}' \
#     RELEASE_BUMP=false scripts/ci/resolve-pipeline-relevance.sh

if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
	cat <<'EOF'
Resolve heavy-pipeline path relevance for GitHub Actions.

Usage:
  EVENT_NAME=<event> CHANGES_JSON='{"pipeline":true,"full-lint":false}' \
    RELEASE_BUMP=false scripts/ci/resolve-pipeline-relevance.sh

Environment:
  EVENT_NAME    github.event_name
  CHANGES_JSON  detect-changes `changes` output (JSON filter -> boolean);
                only consulted when EVENT_NAME is pull_request
  RELEASE_BUMP  release-bump-only.sh verdict; the exact string "true" on a
                pull_request skips the pipeline (verified version-bump PR,
                #1362), anything else keeps the resolved value

Behavior:
  - Non-pull_request events always resolve pipeline=true (merge_group and
    push runs never path-skip) and lint-scope=full.
  - pull_request events resolve the `pipeline` filter from CHANGES_JSON.
  - pull_request events resolve lint-scope=changed only when the
    `full-lint` filter is explicitly false; anything else resolves full.
  - Missing or unparsable CHANGES_JSON fails open (pipeline=true,
    lint-scope=full).
  - RELEASE_BUMP=true on a pull_request forces pipeline=false with
    skip-reason "version-bump PR", outranking the full-lint drift guard.

Outputs (via GITHUB_OUTPUT):
  pipeline=true|false
  lint-scope=full|changed
  skip-reason=<reason>  (empty when pipeline=true)
EOF
	exit 0
fi

event_name="${EVENT_NAME:-}"
changes_json="${CHANGES_JSON:-}"
release_bump="${RELEASE_BUMP:-}"
pipeline="true"
lint_scope="full"
skip_reason=""

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

	# Verified version-bump PR (#1362): skip the heavy pipeline. This
	# override intentionally outranks the drift guard above — the bump PR
	# always hits the pipeline and full-lint filters via pyproject.toml,
	# but the diff allowlist in release-bump-only.sh has already proven
	# the change is version-stamp-only. Fail-safe: only the exact string
	# "true" (a completed, positive verdict) skips.
	if [[ "$release_bump" == "true" ]]; then
		pipeline="false"
		echo "::notice::skipped: version-bump PR (diff limited to" \
			"version stamp + CHANGELOG, #1362)"
	fi
fi

if [[ "$pipeline" == "false" ]]; then
	if [[ "$release_bump" == "true" ]]; then
		skip_reason="version-bump PR"
	else
		skip_reason="docs-only change"
	fi
fi

echo "event=${event_name:-<unset>} pipeline=${pipeline}" \
	"lint-scope=${lint_scope} skip-reason=${skip_reason:-<none>}"

if [[ -n "${GITHUB_OUTPUT:-}" ]]; then
	{
		echo "pipeline=${pipeline}"
		echo "lint-scope=${lint_scope}"
		echo "skip-reason=${skip_reason}"
	} >>"$GITHUB_OUTPUT"
else
	echo "pipeline=${pipeline}"
	echo "lint-scope=${lint_scope}"
	echo "skip-reason=${skip_reason}"
fi
