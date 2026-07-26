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
# Default MIN_AGE_DAYS is 91: Actions keeps runs for 90 days from
# *completion*, but GHCR version updated_at reflects the docker-build
# *push*. The +1 day buffer covers that skew so a partial rerun near the
# end of retention cannot hit an already-swept ci-<run_id> (#1138).
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
#
# Conditional delete: NOT available (checked 2026-07-26, #1652). GitHub's
# REST API supports conditional requests only through ETag/If-None-Match and
# Last-Modified/If-Modified-Since on reads; "conditional requests for unsafe
# methods, such as POST, PUT, PATCH, and DELETE are not supported unless
# otherwise noted", and the delete-package-version endpoint documents no
# If-Match. So the recheck -> DELETE window cannot be closed atomically and
# the guards below (sole-tag rule, dual recheck, post-delete verification)
# are the mitigation. Re-check the docs before re-investigating this.
#
# Post-delete verification (#1652): the sweep only ever deletes versions whose
# sole tag is a ci-* tag, so no persistent (non-CI) tag may disappear while it
# runs. The package's persistent tag set is snapshotted before the deletions
# and re-read after them; a tag that vanished means the residual race actually
# fired, and that is reported as an ::error:: which fails the job.

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
  TAG_PREFIX     Only sweep tags starting with this (default: ci-).
                 Must match ^[A-Za-z0-9._-]+$ because it is interpolated
                 into the gh api --jq filter program.
  MIN_AGE_DAYS   Only delete versions older than N days (default: 91).
                 Values below 91 are rejected unless ALLOW_SHORT_RETENTION
                 is "true" (see below).
  ALLOW_SHORT_RETENTION
                 When "true", allow MIN_AGE_DAYS below the 91-day rerun-
                 retention window. Only for testing: a shorter window
                 sweeps ci-<run_id> tags of still-running workflows and
                 re-creates the "manifest unknown" failure of #1138.
                 Taking the bypass logs a ::warning:: so an inherited
                 value can never waive the guard silently.
                 ghcr-cleanup.yml never sets it.
  VOLATILE_TAG_PREFIXES
                 Space-separated tag prefixes excluded from the post-delete
                 verification baseline (default: "ci- sha-"). ghcr-cleanup.yml
                 runs the ci-* and sha-* sweeps as parallel jobs, so each one
                 sees the other's deletions; without this the sha-* sweep
                 would report swept ci-* tags as lost (#1652). Each entry must
                 match ^[A-Za-z0-9._-]+$ (interpolated into the jq filter).
  DRY_RUN        When "true", log candidates without deleting (default: false)
EOF
	exit 0
fi

gh_token="${GH_TOKEN:-}"
org="${ORG:-lgtm-hq}"
packages="${PACKAGES:-py-lintro py-lintro-base}"
tag_prefix="${TAG_PREFIX:-ci-}"
min_age_days="${MIN_AGE_DAYS:-91}"
volatile_prefixes="${VOLATILE_TAG_PREFIXES:-ci- sha-}"
dry_run="${DRY_RUN:-false}"
sweep_errors=0

# Actions keeps runs for 90 days from completion; GHCR updated_at reflects the
# push, so +1 day covers the skew. Sweeping below this window deletes
# ci-<run_id> tags of runs that can still be partially re-run (#1138).
readonly SAFE_MIN_AGE_DAYS=91

if [[ -z "$gh_token" ]]; then
	echo "GH_TOKEN is required" >&2
	exit 2
fi

if ! [[ "$min_age_days" =~ ^[0-9]+$ ]]; then
	echo "MIN_AGE_DAYS must be a non-negative integer, got: ${min_age_days}" >&2
	exit 2
fi

if [[ "$min_age_days" -lt "$SAFE_MIN_AGE_DAYS" ]]; then
	if [[ "${ALLOW_SHORT_RETENTION:-false}" != "true" ]]; then
		echo "MIN_AGE_DAYS=${min_age_days} is below the ${SAFE_MIN_AGE_DAYS}d" \
			"rerun-retention window (#1138)." >&2
		echo "Set ALLOW_SHORT_RETENTION=true to override." >&2
		exit 2
	fi
	# Never let an inherited ALLOW_SHORT_RETENTION bypass go unnoticed: an
	# operator reading the log must see that the #1138 guard was waived.
	echo "::warning::ALLOW_SHORT_RETENTION=true waives the" \
		"${SAFE_MIN_AGE_DAYS}d guard; sweeping with" \
		"MIN_AGE_DAYS=${min_age_days} can delete ci-<run_id> tags of" \
		"workflows that are still re-runnable (#1138)." >&2
fi

# TAG_PREFIX is interpolated into the gh api --jq program below (gh has no
# --arg equivalent), so constrain it to characters that cannot terminate the
# jq string literal or alter the filter's semantics. This also subsumes the
# previous non-empty check.
if ! [[ "$tag_prefix" =~ ^[A-Za-z0-9._-]+$ ]]; then
	echo "TAG_PREFIX must match ^[A-Za-z0-9._-]+\$, got: ${tag_prefix}" >&2
	exit 2
fi

# The verification baseline must ignore every tag family that some sweep may
# legitimately delete, otherwise a sibling job's work reads as a lost tag
# (#1652). TAG_PREFIX is always excluded; the rest come from
# VOLATILE_TAG_PREFIXES and are interpolated into jq, so apply the same
# character class.
volatile_prefixes_jq="\"${tag_prefix}\""
for prefix in $volatile_prefixes; do
	if ! [[ "$prefix" =~ ^[A-Za-z0-9._-]+$ ]]; then
		echo "VOLATILE_TAG_PREFIXES entries must match" \
			"^[A-Za-z0-9._-]+\$, got: ${prefix}" >&2
		exit 2
	fi
	# The default VOLATILE_TAG_PREFIXES repeats the default TAG_PREFIX, so
	# skip what the seed already covers. `any` short-circuits either way;
	# this just keeps the array matching what the comment above promises.
	[[ "$prefix" == "$tag_prefix" ]] && continue
	volatile_prefixes_jq+=",\"${prefix}\""
done
readonly volatile_prefixes_jq

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

# Print the package's persistent tags (those carrying none of the volatile
# prefixes), one per line, sorted. This is the invariant the post-delete
# verification checks: the sweep only deletes sole-ci-tagged versions, so this
# set may never shrink (#1652).
fetch_persistent_tags() {
	local pkg="$1"
	gh api \
		"orgs/${org}/packages/container/${pkg}/versions" \
		--paginate \
		--jq ".[]
			| (.metadata.container.tags // [])[]
			| . as \$tag
			| select([${volatile_prefixes_jq}]
				| any(. as \$p | \$tag | startswith(\$p))
				| not)" |
		LC_ALL=C sort -u
}

# Print the entries of newline-separated list "$1" absent from list "$2".
# Container tags cannot contain whitespace, so line-wise matching is exact.
tags_missing_from() {
	local expected="$1"
	local actual="$2"
	local tag=""
	while IFS= read -r tag; do
		[[ -z "$tag" ]] && continue
		case $'\n'"${actual}"$'\n' in
		*$'\n'"${tag}"$'\n'*) ;;
		*) printf '%s\n' "$tag" ;;
		esac
	done <<<"$expected"
}

# Verify no persistent tag was collateral damage of this package's deletions.
#
# One check per package rather than one per DELETE: re-listing thousands of
# versions after every deletion would be prohibitively expensive, and the
# invariant is identical either way.
#
# A false "we deleted a release tag" alarm is worse than no alarm, so a tag is
# only reported after it is missing from two independent reads — a paginated
# listing on a busy registry can momentarily under-report. A read that fails
# outright is reported as "could not verify" and still fails the job, keeping
# the sweep fail-closed without claiming a loss that was never observed.
#
# Known limit: a tag first created after the baseline read and destroyed by a
# DELETE in the same run is invisible to this check. That is the deliberate
# trade — detecting it needs a per-delete listing, whose cost and raciness buy
# false alarms. The realistic race (a byte-identical rebuild letting promotion
# move latest/main/a release tag onto an aged ci-* digest) moves tags that the
# baseline already holds, so it is caught.
verify_persistent_tags_survived() {
	local pkg="$1"
	local baseline="$2"
	local deleted_ids="$3"
	local after=""
	local missing=""
	local err_file=""

	err_file="$(mktemp)"
	if ! after=$(fetch_persistent_tags "$pkg" 2>"${err_file}"); then
		echo "::error::Could not verify persistent tags for ${pkg} after" \
			"deleting [${deleted_ids}]: $(cat "${err_file}") (#1652)" >&2
		rm -f "${err_file}"
		sweep_errors=1
		return
	fi
	rm -f "${err_file}"

	missing="$(tags_missing_from "$baseline" "$after")"
	[[ -z "$missing" ]] && return

	# Second, independent read before alarming.
	err_file="$(mktemp)"
	if ! after=$(fetch_persistent_tags "$pkg" 2>"${err_file}"); then
		echo "::error::Could not confirm persistent tags for ${pkg} after" \
			"deleting [${deleted_ids}]: $(cat "${err_file}") (#1652)" >&2
		rm -f "${err_file}"
		sweep_errors=1
		return
	fi
	rm -f "${err_file}"

	missing="$(tags_missing_from "$missing" "$after")"
	[[ -z "$missing" ]] && return

	echo "::error::Persistent tags disappeared from ${pkg} during the sweep:" \
		"[$(printf '%s' "$missing" | tr '\n' ' ')]. Deleted versions:" \
		"[${deleted_ids}]. A promotion very likely raced the DELETE and its" \
		"tag was removed with the CI version (#1652)." >&2
	sweep_errors=1
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

	# Snapshot the persistent tags that must survive the deletions (#1652).
	# Fail closed: without a baseline the post-delete verification is blind,
	# so skip the package rather than delete unverifiably. DRY_RUN deletes
	# nothing, so it needs no baseline.
	local baseline_tags=""
	local deleted_ids=""
	if [[ "$dry_run" != "true" ]]; then
		local baseline_err_file=""
		baseline_err_file="$(mktemp)"
		if ! baseline_tags=$(fetch_persistent_tags "$pkg" \
			2>"${baseline_err_file}"); then
			echo "::error::Failed to snapshot persistent tags for ${pkg};" \
				"skipping its deletions: $(cat "${baseline_err_file}")" \
				"(#1652)" >&2
			rm -f "${baseline_err_file}"
			sweep_errors=1
			return
		fi
		rm -f "${baseline_err_file}"
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
				# 404 = already deleted by a prior sweep/manual cleanup —
				# benign for a reclaim job. Auth/network/rate-limit still
				# fail closed so we do not silently under-delete.
				if grep -Eqi 'HTTP[[:space:]]*404|Not Found \(HTTP 404\)' \
					"${recheck_err_file}"; then
					echo "Skipping version ${vid} (${pkg}): already deleted" \
						"(404 on recheck)"
					rm -f "${recheck_err_file}"
					safe_to_delete=0
					break
				fi
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
			deleted_ids+="${vid} "
			echo "Deleted ${pkg} version ${vid} (tags: ${current_tags})"
		else
			echo "::error::Failed to delete ${pkg} version ${vid}: $(cat "${delete_err_file}")" >&2
			rm -f "${delete_err_file}"
			sweep_errors=1
		fi
	done <<<"$versions"

	if [[ -n "$deleted_ids" ]]; then
		verify_persistent_tags_survived "$pkg" "$baseline_tags" \
			"${deleted_ids% }"
	fi
}

echo "Sweeping ${tag_prefix}* tags older than ${min_age_days}d (dry_run=${dry_run})"
for pkg in $packages; do
	sweep_package "$pkg"
done

if [[ "$sweep_errors" -ne 0 ]]; then
	echo "::error::GHCR CI-tag sweep completed with errors" >&2
	exit 1
fi
