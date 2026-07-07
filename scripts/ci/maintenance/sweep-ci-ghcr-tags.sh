#!/usr/bin/env bash
set -euo pipefail

# sweep-ci-ghcr-tags.sh
#
# Age-based sweep of ephemeral CI tags (ci-*) from GHCR packages.
#
# Rationale (issue #1138): docker-ci.yml previously deleted the run-scoped
# ci-<run_id> tag the moment the run finished. Re-running only the failed jobs
# of that run does NOT rebuild the image (docker-build already succeeded), so
# downstream jobs pulled a tag that had already been deleted and failed with
# "manifest unknown". Deferring cleanup to this age-based sweep keeps run-scoped
# tags alive for the whole life of the run (all attempts) while still bounding
# GHCR storage: tags older than MIN_AGE_DAYS are pruned on the weekly schedule.
#
# Only tagged versions whose tags start with TAG_PREFIX (default "ci-") and are
# older than MIN_AGE_DAYS are deleted. The persistent ":cache" tag is never
# matched. Architecture-specific child manifests that become untagged are left
# for the weekly untagged prune (reusable-ghcr-cleanup.yml).

if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
	cat <<'EOF'
Age-based sweep of ephemeral CI tags (ci-*) from GHCR packages.

Usage:
  GH_TOKEN=<token> scripts/ci/maintenance/sweep-ci-ghcr-tags.sh

Environment:
  GH_TOKEN       GitHub token with packages:write (required)
  ORG            GHCR org owner (default: lgtm-hq)
  PACKAGES       Space-separated package names
                 (default: "py-lintro py-lintro-base")
  TAG_PREFIX     Only sweep tags starting with this (default: ci-)
  MIN_AGE_DAYS   Only delete versions older than N days (default: 7)
  DRY_RUN        When "true", log candidates without deleting (default: false)
EOF
	exit 0
fi

gh_token="${GH_TOKEN:-}"
org="${ORG:-lgtm-hq}"
packages="${PACKAGES:-py-lintro py-lintro-base}"
tag_prefix="${TAG_PREFIX:-ci-}"
min_age_days="${MIN_AGE_DAYS:-7}"
dry_run="${DRY_RUN:-false}"

if [[ -z "$gh_token" ]]; then
	echo "GH_TOKEN is required" >&2
	exit 2
fi

if ! [[ "$min_age_days" =~ ^[0-9]+$ ]]; then
	echo "MIN_AGE_DAYS must be a non-negative integer, got: ${min_age_days}" >&2
	exit 2
fi

cutoff_epoch=$(($(date -u +%s) - min_age_days * 86400))

sweep_package() {
	local pkg="$1"
	local versions=""
	local query_output=""

	# Select versions where any tag starts with the prefix AND the version is
	# older than the cutoff. .id is emitted for each match.
	if ! query_output=$(gh api \
		"orgs/${org}/packages/container/${pkg}/versions" \
		--paginate \
		--jq "
			.[]
			| select(
				((.metadata.container.tags // []) | any(startswith(\$prefix)))
				and ((.updated_at | fromdateiso8601) < (\$cutoff | tonumber))
			)
			| \"\(.id)\t\((.metadata.container.tags // []) | join(\",\"))\"
		" \
		--arg prefix "$tag_prefix" \
		--arg cutoff "$cutoff_epoch" 2>&1); then
		echo "::warning::Failed to query versions for ${pkg}: ${query_output}" >&2
		return
	fi
	versions="$query_output"

	if [[ -z "$versions" ]]; then
		echo "No ${tag_prefix}* tags older than ${min_age_days}d for ${pkg}"
		return
	fi

	while IFS=$'\t' read -r vid tags; do
		[[ -z "$vid" ]] && continue
		if [[ "$dry_run" == "true" ]]; then
			echo "[dry-run] Would delete ${pkg} version ${vid} (tags: ${tags})"
			continue
		fi
		local delete_output=""
		if delete_output=$(gh api --method DELETE \
			"orgs/${org}/packages/container/${pkg}/versions/${vid}" \
			2>&1); then
			echo "Deleted ${pkg} version ${vid} (tags: ${tags})"
		else
			echo "::warning::Failed to delete ${pkg} version ${vid}: ${delete_output}"
		fi
	done <<<"$versions"
}

echo "Sweeping ${tag_prefix}* tags older than ${min_age_days}d (dry_run=${dry_run})"
for pkg in $packages; do
	sweep_package "$pkg"
done
