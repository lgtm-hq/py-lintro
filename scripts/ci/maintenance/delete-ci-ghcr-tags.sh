#!/usr/bin/env bash
set -euo pipefail

# delete-ci-ghcr-tags.sh
#
# Delete ephemeral CI tags from py-lintro and py-lintro-base GHCR packages.

if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
	cat <<'EOF'
Delete ephemeral CI tags from GHCR packages.

Usage:
  CI_TAG=<tag> GH_TOKEN=<token> scripts/ci/maintenance/delete-ci-ghcr-tags.sh

Environment:
  CI_TAG    Ephemeral tag to delete (required)
  GH_TOKEN  GitHub token with packages:write (required)
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
	local version_id=""

	version_id=$(gh api \
		"orgs/lgtm-hq/packages/container/${pkg}/versions" \
		--paginate \
		--jq ".[] |
			select((.metadata.container.tags // [])[] == \"${tag}\") |
			.id" 2>/dev/null) || version_id=""

	if [[ -n "$version_id" ]]; then
		while IFS= read -r vid; do
			[[ -z "$vid" ]] && continue
			gh api --method DELETE \
				"orgs/lgtm-hq/packages/container/${pkg}/versions/${vid}" \
				2>/dev/null &&
				echo "Deleted version ${vid} (${pkg}:${tag})" ||
				echo "::warning::Failed to delete version ${vid}"
		done <<<"$version_id"
	else
		echo "No version found for ${pkg}:${tag}"
	fi
}

delete_ci_tag py-lintro "$ci_tag"
delete_ci_tag py-lintro-base "$ci_tag"
