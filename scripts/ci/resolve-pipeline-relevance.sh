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
# the full pipeline with full-repo lint.
#
# Pipeline classification is deny-by-default (#1369): instead of enumerating
# every pipeline-relevant path (an allow-list that rots as the repo grows),
# the script enumerates the small, stable set of paths that may SKIP the
# pipeline and resolves pipeline=false only when EVERY changed file matches
# that skip-list:
#
#   - '**/*.md'   pure markdown prose at any depth
#   - 'docs/**'   documentation tree
#   - 'assets/**' images and static assets
#
# with two carve-outs that stay pipeline-RELEVANT despite matching the
# globs above (they feed the integration tests / lint config):
#
#   - 'test_samples/**'              lint fixtures, including *.md samples
#   - 'docs/.markdownlint-cli2.jsonc' markdownlint config living under docs/
#
# Any changed file outside the skip-list — including files in brand-new
# top-level directories — runs the full pipeline. New paths trigger by
# default; the skip-list only grows deliberately (guarded by the drift test
# in tests/unit/test_workflow_wiring.py).
#
# The changed-file list is derived from the PR merge commit (HEAD^1..HEAD,
# same technique as release-bump-only.sh); BASE_SHA/HEAD_SHA override the
# range (tests). A non-merge HEAD, a failed diff, or an empty diff fails
# open (pipeline=true) so required checks run their full jobs instead of
# silently early-exiting.
#
# lint-scope consults the JSON produced by the lgtm-hq/lgtm-ci detect-changes
# action (filter name -> boolean) and resolves `changed` only when the PR
# diff explicitly missed the `full-lint` filter (no path with global lint
# impact was touched); every other case — non-PR events, filter hit, missing
# filter, unparsable JSON — resolves `full` so a filter regression can never
# narrow lint coverage.
#
# RELEASE_BUMP (#1362): when release-bump-only.sh verified the PR as an
# automated version-bump PR (diff limited to the version stamp + CHANGELOG),
# the pipeline is skipped even though pyproject.toml/uv.lock miss the
# skip-list — the diff allowlist is a stronger guarantee than the path
# classification, so this override intentionally outranks the full-lint
# drift guard below. Only the exact string "true" skips; anything else
# (empty, "false", garbage, a failed bump step) keeps the resolved value.
#
# Usage:
#   EVENT_NAME=<event> CHANGES_JSON='{"full-lint":false}' \
#     RELEASE_BUMP=false scripts/ci/resolve-pipeline-relevance.sh

if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
	cat <<'EOF'
Resolve heavy-pipeline path relevance for GitHub Actions.

Usage:
  EVENT_NAME=<event> CHANGES_JSON='{"full-lint":false}' \
    RELEASE_BUMP=false scripts/ci/resolve-pipeline-relevance.sh

Environment:
  EVENT_NAME    github.event_name
  CHANGES_JSON  detect-changes `changes` output (JSON filter -> boolean);
                only the `full-lint` filter is consulted, and only when
                EVENT_NAME is pull_request
  RELEASE_BUMP  release-bump-only.sh verdict; the exact string "true" on a
                pull_request skips the pipeline (verified version-bump PR,
                #1362), anything else keeps the resolved value
  BASE_SHA      Diff base override (default: HEAD^1 of the PR merge ref)
  HEAD_SHA      Diff head override (default: HEAD)

Behavior:
  - Non-pull_request events always resolve pipeline=true (merge_group and
    push runs never path-skip) and lint-scope=full.
  - pull_request events resolve pipeline=false only when EVERY changed
    file matches the skip-list ('**/*.md', 'docs/**', 'assets/**') and
    none hits a carve-out ('test_samples/**',
    'docs/.markdownlint-cli2.jsonc'). Anything else — new directories,
    new configs, new file types — resolves pipeline=true (deny-by-default,
    #1369).
  - pull_request events resolve lint-scope=changed only when the
    `full-lint` filter is explicitly false; anything else resolves full.
  - An unavailable or empty changed-file list and missing or unparsable
    CHANGES_JSON fail open (pipeline=true, lint-scope=full).
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

# The deny-by-default skip-list (#1369). A changed file may skip the
# pipeline only when this function returns 0. Keep the carve-outs first:
# they are pipeline-RELEVANT paths that would otherwise match the skip
# globs. The drift test in tests/unit/test_workflow_wiring.py asserts these
# literals stay in sync with the top-level categorization.
is_skippable_path() {
	local path="$1"
	case "$path" in
	# Carve-outs (pipeline-relevant despite matching the globs below):
	# test_samples/ holds lint fixtures (including *.md) feeding the
	# integration tests; docs/.markdownlint-cli2.jsonc is lint config.
	test_samples/*) return 1 ;;
	docs/.markdownlint-cli2.jsonc) return 1 ;;
	# Skip-list: pure prose and static assets.
	*.md) return 0 ;;
	docs/*) return 0 ;;
	assets/*) return 0 ;;
	esac
	return 1
}

# Print the PR's changed files (one per line). BASE_SHA/HEAD_SHA override;
# otherwise derive from the pull_request merge ref: its first parent is the
# base branch tip, so HEAD^1..HEAD is exactly the change the merge would
# land. A non-merge HEAD returns nonzero so the caller fails open.
# --no-renames: rename detection would collapse a rename to its destination
# path only, so moving a pipeline-relevant file into a skippable location
# (e.g. lintro/core.py -> docs/core.py) could classify as docs-only; with
# renames disabled both the deleted source and the added destination are
# listed and the source keeps the pipeline on.
resolve_changed_files() {
	local base="${BASE_SHA:-}"
	local head="${HEAD_SHA:-}"
	if [[ -z "$base" || -z "$head" ]]; then
		if ! git rev-parse --verify --quiet 'HEAD^2' >/dev/null 2>&1; then
			return 1
		fi
		base="$(git rev-parse 'HEAD^1')"
		head="$(git rev-parse HEAD)"
	fi
	git diff --name-only --no-renames "$base" "$head"
}

if [[ "$event_name" == "pull_request" ]]; then
	# Deny-by-default (#1369): assume relevant, prove every changed file
	# skippable. An unavailable or empty diff fails open (pipeline=true).
	if changed_files="$(resolve_changed_files 2>/dev/null)" &&
		[[ -n "$changed_files" ]]; then
		pipeline="false"
		while IFS= read -r changed_file; do
			if ! is_skippable_path "$changed_file"; then
				pipeline="true"
				break
			fi
		done <<<"$changed_files"
	else
		echo "::warning::changed-file list unavailable (non-merge HEAD," \
			"failed or empty diff); failing open (pipeline=true)"
		pipeline="true"
	fi
	# lint-scope narrows to `changed` only on an explicit full-lint=false;
	# true, missing, or unparsable all keep full-repo lint (fail-safe: a
	# filter regression widens coverage, never narrows it).
	# Plain -r (not -e): jq -e exits nonzero for a legitimate false value.
	if resolved_full_lint="$(
		jq -r '."full-lint"' <<<"$changes_json" 2>/dev/null
	)"; then
		if [[ "$resolved_full_lint" == "false" ]]; then
			lint_scope="changed"
		elif [[ "$resolved_full_lint" == "true" && "$pipeline" == "false" ]]; then
			# Invariant guard: a path with global lint impact must never be
			# pipeline-skipped. The skip-list is deliberately tiny, but a
			# full-lint-relevant file can still hide inside it (e.g. a new
			# dotfile config under docs/) — run the full pipeline rather
			# than skipping the dogfooding lint of a global-impact change.
			echo "::warning::full-lint filter matched while every changed" \
				"file was skippable; forcing pipeline=true"
			pipeline="true"
		fi
	fi

	# Verified version-bump PR (#1362): skip the heavy pipeline. This
	# override intentionally outranks the drift guard above — the bump PR
	# always resolves pipeline=true via pyproject.toml/uv.lock, but the
	# diff allowlist in release-bump-only.sh has already proven the change
	# is version-stamp-only. Fail-safe: only the exact string "true" (a
	# completed, positive verdict) skips.
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
