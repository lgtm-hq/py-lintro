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
# Default MIN_AGE_DAYS is 90 to match GitHub Actions' default workflow-run
# retention: maintainers can re-run failed jobs for as long as the run is
# kept, and the ci-<run_id> tag must still resolve for those partial reruns.
#
# The GHCR Packages API deletes whole *versions* (one version = one digest
# carrying every tag that points at it). Safety rules match
# delete-ci-ghcr-tags.sh (#1138, #1358):
#   - Only versions whose EVERY tag starts with TAG_PREFIX are candidates.
#   - Mixed CI+release (or CI+foreign) versions are skipped.
#   - Tags + updated_at are re-checked immediately before DELETE to narrow
#     the TOCTOU window if promotion or a byte-identical concurrent build
#     attaches a persistent tag between the list and the delete.
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
  MIN_AGE_DAYS   Only delete versions older than N days (default: 90)
  DRY_RUN        When "true", log candidates without deleting (default: false)
EOF
	exit 0
fi

gh_token="${GH_TOKEN:-}"
org="${ORG:-lgtm-hq}"
packages="${PACKAGES:-py-lintro py-lintro-base}"
tag_prefix="${TAG_PREFIX:-ci-}"
min_age_days="${MIN_AGE_DAYS:-90}"
dry_run="${DRY_RUN:-false}"
sweep_errors=0

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

# Fetch current tags + updated_at for a version. Prints "<updated_at>\t<tags>".
fetch_version_state() {
	local pkg="$1"
	local vid="$2"
	gh api \
		"orgs/${org}/packages/container/${pkg}/versions/${vid}" \
		--jq '[.updated_at, ((.metadata.container.tags // []) | join(" "))] | @tsv'
}

sweep_package() {
	local pkg="$1"
	local query_output=""
	local versions=""

	# One TSV line per candidate: <id>\t<updated_at>\t<space-joined tags>
	# Embed prefix/cutoff via shell interpolation (gh api has no --arg).
	# Keep stderr out of the TSV payload so diagnostic noise cannot corrupt
	# the while-read loop (CodeRabbit on #1645).
	local query_err_file=""
	query_err_file="$(mktemp)"
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
				.updated_at,
				((.metadata.container.tags // []) | join(\" \"))]
			| @tsv" 2>"${query_err_file}"); then
		echo "::error::Failed to query versions for ${pkg}: $(cat "${query_err_file}")" >&2
		rm -f "${query_err_file}"
		sweep_errors=1
		return
	fi
	rm -f "${query_err_file}"
	versions="$query_output"

	if [[ -z "$versions" ]]; then
		echo "No ${tag_prefix}* tags older than ${min_age_days}d for ${pkg}"
		return
	fi

	while IFS=$'\t' read -r vid snap_updated tags; do
		[[ -z "$vid" ]] && continue
		if ! tags_are_ci_only "$tags"; then
			echo "Skipping version ${vid} (${pkg}): unexpected non-CI tags" \
				"[${tags}] (#1138)"
			continue
		fi
		# Sole-tag only: shared digests (multiple ci-* tags) and residual
		# promotion TOCTOU blast radius stay lower — same rule as
		# delete-ci-ghcr-tags.sh (#1138, follow-up #1652).
		tag_count=0
		for _t in $tags; do
			tag_count=$((tag_count + 1))
		done
		if [[ "$tag_count" -ne 1 ]]; then
			echo "Skipping version ${vid} (${pkg}): not sole-tagged" \
				"[${tags}] (#1138/#1652)"
			continue
		fi
		if [[ "$dry_run" == "true" ]]; then
			echo "[dry-run] Would delete ${pkg} version ${vid} (tags: ${tags})"
			continue
		fi
		# Re-check immediately before deleting: promotion (#1358) or a
		# byte-identical concurrent build may have attached a persistent
		# tag (or refreshed updated_at) between the paginated snapshot and
		# now. A second immediate recheck narrows the residual TOCTOU window
		# before DELETE.
		local state=""
		local current_updated=""
		local current_tags=""
		local safe_to_delete=1
		local recheck_err_file=""
		# Dual recheck narrows the promotion/TOCTOU window before DELETE.
		for _ in 1 2; do
			recheck_err_file="$(mktemp)"
			if ! state=$(fetch_version_state "$pkg" "$vid" 2>"${recheck_err_file}"); then
				echo "::error::Failed to re-check version ${vid}; skipping" \
					"deletion: $(cat "${recheck_err_file}")" >&2
				rm -f "${recheck_err_file}"
				sweep_errors=1
				safe_to_delete=0
				break
			fi
			rm -f "${recheck_err_file}"
			IFS=$'\t' read -r current_updated current_tags <<<"$state"
			if [[ "$current_updated" != "$snap_updated" ]]; then
				echo "Skipping version ${vid} (${pkg}): updated_at changed" \
					"since snapshot (${snap_updated} -> ${current_updated})" \
					"(#1138)"
				safe_to_delete=0
				break
			fi
			if ! tags_are_ci_only "$current_tags"; then
				echo "Skipping version ${vid} (${pkg}): tags changed since" \
					"snapshot [${current_tags}] (#1138)"
				safe_to_delete=0
				break
			fi
			current_tag_count=0
			for _t in $current_tags; do
				current_tag_count=$((current_tag_count + 1))
			done
			if [[ "$current_tag_count" -ne 1 ]]; then
				echo "Skipping version ${vid} (${pkg}): no longer sole-tagged" \
					"[${current_tags}] (#1138/#1652)"
				safe_to_delete=0
				break
			fi
		done
		if [[ "$safe_to_delete" -ne 1 ]]; then
			continue
		fi
		local delete_err_file=""
		delete_err_file="$(mktemp)"
		if gh api --method DELETE \
			"orgs/${org}/packages/container/${pkg}/versions/${vid}" \
			>/dev/null 2>"${delete_err_file}"; then
			rm -f "${delete_err_file}"
			echo "Deleted ${pkg} version ${vid} (tags: ${current_tags})"
		else
			echo "::error::Failed to delete ${pkg} version ${vid}: $(cat "${delete_err_file}")" >&2
			rm -f "${delete_err_file}"
			sweep_errors=1
		fi
	done <<<"$versions"
}

echo "Sweeping ${tag_prefix}* tags older than ${min_age_days}d (dry_run=${dry_run})"
for pkg in $packages; do
	sweep_package "$pkg"
done

if [[ "$sweep_errors" -ne 0 ]]; then
	echo "::error::GHCR CI-tag sweep completed with errors" >&2
	exit 1
fi
