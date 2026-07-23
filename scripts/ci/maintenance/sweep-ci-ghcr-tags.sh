#!/usr/bin/env bash
set -euo pipefail

# sweep-ci-ghcr-tags.sh
#
# Age-based sweep of ephemeral CI tags (ci-*) from GHCR packages.
#
# Rationale (issue #1138): docker-ci.yml previously deleted the run-scoped
# ci-<run_id> tag when the run finished. Re-running only the failed jobs of
# that run does NOT rebuild the image (docker-build already succeeded), so
# downstream jobs pulled a deleted tag and failed with "manifest unknown".
# Deferring cleanup to this age-based sweep keeps run-scoped tags alive for
# the whole life of the run (all attempts) while still bounding GHCR storage:
# tags older than MIN_AGE_DAYS are pruned on the weekly schedule.
#
# The GHCR Packages API deletes whole *versions* (one version = one digest
# carrying every tag that points at it). Safety rules match
# delete-ci-ghcr-tags.sh (#1138, #1358):
#   - Only versions whose EVERY tag starts with TAG_PREFIX are candidates.
#   - Mixed CI+release (or CI+foreign) versions are skipped.
#   - Tags are re-checked immediately before DELETE to narrow the TOCTOU
#     window if promotion or a byte-identical concurrent build attaches a
#     persistent tag between the list and the delete.
# The persistent ":cache" tag never matches the CI prefix. Architecture-
# specific child manifests that become untagged are left for the weekly
# untagged prune (reusable-ghcr-cleanup.yml).

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

if [[ -z "$tag_prefix" ]]; then
	echo "TAG_PREFIX must be non-empty" >&2
	exit 2
fi

cutoff_epoch=$(($(date -u +%s) - min_age_days * 86400))

tags_are_ci_only() {
	local tags="$1"
	local t=""
	[[ -z "$tags" ]] && return 1
	for t in $tags; do
		case "$t" in
		"${tag_prefix}"*) ;;
		*) return 1 ;;
		esac
	done
	return 0
}

sweep_package() {
	local pkg="$1"
	local query_output=""
	local versions=""

	# One TSV line per candidate: <id>\t<space-joined tags>
	# Embed prefix/cutoff via shell interpolation (gh api has no --arg).
	if ! query_output=$(gh api \
		"orgs/${org}/packages/container/${pkg}/versions" \
		--paginate \
		--jq ".[]
			| select(
				((.metadata.container.tags // []) | length) > 0
				and ((.metadata.container.tags // [])
					| all(startswith(\"${tag_prefix}\")))
				and ((.updated_at | fromdateiso8601) < ${cutoff_epoch})
			)
			| [( .id | tostring),
				((.metadata.container.tags // []) | join(\" \"))]
			| @tsv" 2>&1); then
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
		if ! tags_are_ci_only "$tags"; then
			echo "Skipping version ${vid} (${pkg}): unexpected non-CI tags" \
				"[${tags}] (#1138)"
			continue
		fi
		if [[ "$dry_run" == "true" ]]; then
			echo "[dry-run] Would delete ${pkg} version ${vid} (tags: ${tags})"
			continue
		fi
		# Re-check immediately before deleting: promotion (#1358) or a
		# byte-identical concurrent build may have attached a persistent
		# tag between the paginated snapshot and now.
		local current_tags=""
		if ! current_tags=$(gh api \
			"orgs/${org}/packages/container/${pkg}/versions/${vid}" \
			--jq '(.metadata.container.tags // []) | join(" ")' 2>&1); then
			echo "::warning::Failed to re-check version ${vid}; skipping" \
				"deletion: ${current_tags}"
			continue
		fi
		if ! tags_are_ci_only "$current_tags"; then
			echo "Skipping version ${vid} (${pkg}): tags changed since" \
				"snapshot [${current_tags}] (#1138)"
			continue
		fi
		local delete_output=""
		if delete_output=$(gh api --method DELETE \
			"orgs/${org}/packages/container/${pkg}/versions/${vid}" \
			2>&1); then
			echo "Deleted ${pkg} version ${vid} (tags: ${current_tags})"
		else
			echo "::warning::Failed to delete ${pkg} version ${vid}: ${delete_output}"
		fi
	done <<<"$versions"
}

echo "Sweeping ${tag_prefix}* tags older than ${min_age_days}d (dry_run=${dry_run})"
for pkg in $packages; do
	sweep_package "$pkg"
done
