#!/usr/bin/env bash
set -euo pipefail

# delete-ci-ghcr-tags.sh
#
# Delete ephemeral CI tags from py-lintro and py-lintro-base GHCR packages.
#
# The GHCR Packages API deletes whole *versions* (one version = one digest
# carrying every tag that points at it), not individual tags. Two safety
# rules follow (#1138, #1358):
#   - A version is deleted only when the requested CI tag is its ONLY tag.
#   - A version carrying any other tag is skipped: release tags promoted by
#     digest (#1358) share the version with the CI tag, and byte-identical
#     builds from concurrent runs share it with foreign ci-* tags — deleting
#     the version would strip those too (incident: run 29305625795).
# Skipped CI tags are harmless aliases of a kept digest (no extra storage);
# versions they keep alive are removed by the scheduled GHCR prune once the
# real tags are gone.

if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
	cat <<'EOF'
Delete ephemeral CI tags from GHCR packages.

Usage:
  CI_TAG=<tag> GH_TOKEN=<token> scripts/ci/maintenance/delete-ci-ghcr-tags.sh

Environment:
  CI_TAG    Ephemeral tag to delete (required)
  GH_TOKEN  GitHub token with packages:write (required)

A package version is deleted only when CI_TAG is its sole tag; versions
shared with release tags or other runs' CI tags are left in place.
EOF
	exit 0
fi

ci_tag="${CI_TAG:-}"
gh_token="${GH_TOKEN:-}"

if [[ -z "$ci_tag" ]]; then
	echo "CI_TAG is required" >&2
	exit 2
fi

if [[ -z "$gh_token" ]]; then
	echo "GH_TOKEN is required" >&2
	exit 2
fi

delete_ci_tag() {
	local pkg="$1"
	local tag="$2"
	local output=""
	local delete_output=""
	local vid=""
	local version_tags=""

	# One TSV line per matching version: <id>\t<space-joined tags>
	if ! output=$(gh api \
		"orgs/lgtm-hq/packages/container/${pkg}/versions" \
		--paginate \
		--jq ".[] |
			select(((.metadata.container.tags // []) |
				index(\"${tag}\")) != null) |
			[(.id | tostring),
				((.metadata.container.tags // []) | join(\" \"))] |
			@tsv" 2>&1); then
		echo "::warning::Failed to query versions for ${pkg}:${tag}: ${output}" >&2
		return
	fi

	if [[ -z "$output" ]]; then
		echo "No version found for ${pkg}:${tag}"
		return
	fi

	while IFS=$'\t' read -r vid version_tags; do
		[[ -z "$vid" ]] && continue
		local only_ci_tag="true"
		local t=""
		for t in $version_tags; do
			if [[ "$t" != "$tag" ]]; then
				only_ci_tag="false"
				break
			fi
		done
		if [[ "$only_ci_tag" != "true" ]]; then
			echo "Skipping version ${vid} (${pkg}:${tag}): digest is shared" \
				"with other tags [${version_tags}] (#1138)"
			continue
		fi
		if delete_output=$(gh api --method DELETE \
			"orgs/lgtm-hq/packages/container/${pkg}/versions/${vid}" \
			2>&1); then
			echo "Deleted version ${vid} (${pkg}:${tag})"
		else
			echo "::warning::Failed to delete version ${vid}: ${delete_output}"
		fi
	done <<<"$output"
}

delete_ci_tag py-lintro "$ci_tag"
delete_ci_tag py-lintro-base "$ci_tag"
