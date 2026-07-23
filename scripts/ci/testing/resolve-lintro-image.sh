#!/usr/bin/env bash
set -euo pipefail

# Resolve the GHCR image ref for scheduled lintro analysis.
# Prefers sha-<commit> when the manifest exists; otherwise falls back to the
# newest published sha-* tag (never :latest — see issue #1032).

if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
	cat <<'EOF'
Resolve the published lintro Docker image for scheduled analysis.

Usage:
  LINTRO_SHA=<commit> scripts/ci/testing/resolve-lintro-image.sh

Environment:
  LINTRO_SHA           Commit SHA to prefer (default: git rev-parse HEAD)
  GHCR_ORG_PACKAGE     Registry prefix (default: ghcr.io/lgtm-hq)
  GITHUB_REPOSITORY    Repo for CI - Docker run lookup (default: lgtm-hq/py-lintro)
  GITHUB_OUTPUT        When set, writes lintro_image and fallback metadata
  GITHUB_STEP_SUMMARY  When set, appends a preflight note on fallback

Outputs (via GITHUB_OUTPUT):
  lintro_image         Full image ref to pull (e.g. ghcr.io/lgtm-hq/py-lintro:sha-abc...)
  lintro_requested_sha Commit SHA that was requested
  lintro_resolved_sha  Commit SHA backing the resolved image tag
  lintro_fallback      true when a newer published sha-* tag was used instead

Resolution:
  1. docker manifest inspect on sha-<requested>
  2. On miss, newest sha-* tag from GHCR package versions (gh api)
  3. Never fall back to :latest
EOF
	exit 0
fi

# SC1091: path is dynamically constructed, file exists at runtime
# shellcheck disable=SC1091
source "$(dirname "$0")/../../utils/utils.sh"

registry="${GHCR_ORG_PACKAGE:-ghcr.io/lgtm-hq}"
# GHCR package API path uses the org segment of the registry prefix.
ghcr_org="${registry##*/}"
requested_sha="${LINTRO_SHA:-$(git rev-parse HEAD)}"
primary_tag="sha-${requested_sha}"
primary_image="${registry}/py-lintro:${primary_tag}"
repo="${GITHUB_REPOSITORY:-lgtm-hq/py-lintro}"

image_manifest_exists() {
	local ref="$1"
	docker manifest inspect "$ref" >/dev/null 2>&1
}

find_newest_sha_tag() {
	local tag=""
	local versions_json=""
	local api_base=""
	local list_err=""
	# Use --paginate without --jq so pages are concatenated into one array,
	# then select the newest sha-* tag with a single jq pass.
	# GHCR_ORG_PACKAGE is a registry prefix; the owner may be an org or a
	# user. Prefer the org Packages API, then fall back to the user endpoint
	# (Greptile #1326 P1).
	for api_base in "orgs/${ghcr_org}" "users/${ghcr_org}"; do
		if versions_json=$(gh api \
			--paginate \
			"${api_base}/packages/container/py-lintro/versions" 2>/dev/null); then
			break
		fi
		list_err="${api_base}"
		versions_json=""
	done
	if [[ -z "$versions_json" ]]; then
		echo "::error::Failed to list GHCR sha-* tags for fallback resolution" \
			"(tried orgs/${ghcr_org} and users/${ghcr_org}; last=${list_err})." >&2
		exit 1
	fi
	if ! tag=$(printf '%s' "$versions_json" | jq -s -r '
		add
		| [.[]
			| select((.metadata.container.tags // []) | any(startswith("sha-")))
			| {
				updated: .updated_at,
				tag: ([.metadata.container.tags[] | select(startswith("sha-"))] | first)
			}
		]
		| sort_by(.updated)
		| reverse
		| .[0].tag // empty
	'); then
		echo "::error::Failed to parse GHCR package versions for fallback resolution." >&2
		exit 1
	fi
	if [[ -z "$tag" ]]; then
		echo "::error::No published sha-* tags found for ${registry}/py-lintro." >&2
		exit 1
	fi
	printf '%s' "$tag"
}

lookup_docker_ci_run_ref() {
	local sha="$1"
	local ref=""
	ref=$(gh run list \
		--repo "$repo" \
		--workflow docker-ci.yml \
		--commit "$sha" \
		--limit 1 \
		--json url,conclusion \
		--jq '.[0] | select(. != null) | "\(.url) (conclusion: \(.conclusion))"' \
		2>/dev/null || true)
	printf '%s' "$ref"
}

write_fallback_summary() {
	local docker_ci_ref="$1"
	local resolved_image="$2"
	if [[ -z "${GITHUB_STEP_SUMMARY:-}" ]]; then
		return 0
	fi
	{
		echo "### Docker image preflight"
		echo ""
		echo "- **Requested:** \`${primary_image}\` (not published)"
		echo "- **Using:** \`${resolved_image}\`"
		if [[ -n "$docker_ci_ref" ]]; then
			echo "- **CI - Docker:** ${docker_ci_ref}"
		fi
		echo ""
	} >>"$GITHUB_STEP_SUMMARY"
}

if image_manifest_exists "$primary_image"; then
	set_github_output "lintro_image" "$primary_image"
	set_github_output "lintro_requested_sha" "$requested_sha"
	set_github_output "lintro_resolved_sha" "$requested_sha"
	set_github_output "lintro_fallback" "false"
	echo "::notice::Using published image ${primary_image}"
	exit 0
fi

docker_ci_ref=$(lookup_docker_ci_run_ref "$requested_sha")
fallback_tag=$(find_newest_sha_tag)
fallback_sha="${fallback_tag#sha-}"
fallback_image="${registry}/py-lintro:${fallback_tag}"

if ! image_manifest_exists "$fallback_image"; then
	echo "::error::Fallback tag ${fallback_tag} is listed in GHCR but manifest is unavailable." >&2
	exit 1
fi

set_github_output "lintro_image" "$fallback_image"
set_github_output "lintro_requested_sha" "$requested_sha"
set_github_output "lintro_resolved_sha" "$fallback_sha"
set_github_output "lintro_fallback" "true"

{
	echo "::warning::Image not published for commit ${requested_sha} (${primary_image})."
	echo "::warning::Falling back to ${fallback_image} (newest published sha-* tag)."
	if [[ -n "$docker_ci_ref" ]]; then
		echo "::warning::See CI - Docker run: ${docker_ci_ref}"
	fi
} >&2

write_fallback_summary "$docker_ci_ref" "$fallback_image"
