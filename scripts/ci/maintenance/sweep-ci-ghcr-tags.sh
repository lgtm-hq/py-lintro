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
#
# Bounded per run (#3492): a backlog of thousands of aged tags made the weekly
# job hit its timeout mid-pagination and get cancelled, which reported main as
# red. The run is now bounded end to end:
#   - Every gh call runs under timeout(1) and is killed after
#     GH_CALL_TIMEOUT_SECONDS (C).
#   - Listings are paged by hand (PAGE_SIZE per request, default 100: the
#     API maximum; the API's own default is 30) so the clock is checked
#     between pages, and
#     only an empty page ends one. One listing yields both the candidates and
#     the persistent-tag baseline.
#   - With H = TIME_BUDGET_SECONDS and R = VERIFY_RESERVE_SECONDS, new work
#     (a listing page, a recheck-and-delete) only starts while fewer than
#     H - R seconds have passed. Inside a package whose listing took L
#     seconds, deletions only start before H - max(R, 2L), so its
#     verification (up to two listings) still has room. A package listed
#     first therefore deletes nothing once L >= H/3 (400s at the default
#     1200s); a later one reaches that point sooner. Such a package stops on
#     its own and the next package still gets the rest of the work window.
#   - Verification listings only start before H.
#   Since R >= 3C is enforced, the work phase ends by H, so the script returns
#   within H + C plus local overhead, and the job timeout only has to exceed
#   that.
# A run that stops at the budget or at MAX_DELETES still verifies what it
# deleted and exits 0 with a "partial, resumes next run" notice; the next run
# lists the registry afresh and continues. Packages are visited starting at
# PACKAGE_ROTATION mod N (the workflow passes the run number), so a package
# whose listing eats the budget cannot starve the others week after week, and
# a partial run that deleted nothing or skipped a package raises a ::warning::
# so a stuck sweep never looks healthy.

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
                 Must match ^[A-Za-z0-9._-]+$ (container tag characters).
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
                 match ^[A-Za-z0-9._-]+$.
  TIME_BUDGET_SECONDS
                 Total run budget, 0-86400 (default: 1200; 0 disables it).
                 New work stops at TIME_BUDGET_SECONDS minus
                 VERIFY_RESERVE_SECONDS and verification stops at the budget,
                 so the script returns within the budget plus one
                 GH_CALL_TIMEOUT_SECONDS.
  VERIFY_RESERVE_SECONDS
                 Seconds kept back from the budget for post-delete
                 verification, 0-86400 (default: 300). With a budget set it
                 must be below the budget and at least three times
                 GH_CALL_TIMEOUT_SECONDS.
  GH_CALL_TIMEOUT_SECONDS
                 Kill any single gh call after this many seconds, 1-3600
                 (default: 60). A killed call is an error.
  MAX_DELETES    Delete at most this many versions per run across all
                 packages, 0-100000 (default: 500; 0 disables the cap).
                 DRY_RUN is exempt from the cap so it previews every
                 candidate; the time budget still applies to it.
  PAGE_SIZE      Versions per listing request, 1-100 (default: 100).
  PACKAGE_ROTATION
                 Non-negative integer; the run starts with package number
                 PACKAGE_ROTATION mod N of PACKAGES (default: 0).
                 ghcr-cleanup.yml passes the run number.
  DRY_RUN        When "true", log candidates without deleting (default: false)

All numbers are read as base-10 integers; leading zeros are allowed.
A DELETE that times out, gets a 5xx or gets no HTTP answer may have gone
through: it counts against MAX_DELETES and stops further deletions, and the
verification listing then shows whether it did. The run fails only when that
cannot be told. A 404 on DELETE means the version is already gone. A 403 or
429 stops every further recheck and DELETE at once, as do 3 failed rechecks
or DELETE calls in a row; either fails the run.
A run that stops at the budget or the cap exits 0 and prints
"Partial sweep, resumes next run: N deleted, M remaining".
EOF
	exit 0
fi

gh_token="${GH_TOKEN:-}"
org="${ORG:-lgtm-hq}"
packages="${PACKAGES:-py-lintro py-lintro-base}"
tag_prefix="${TAG_PREFIX:-ci-}"
volatile_prefixes="${VOLATILE_TAG_PREFIXES:-ci- sha-}"
dry_run="${DRY_RUN:-false}"
sweep_errors=0
# Run-wide progress for the per-run bound (#3492).
swept_count=0
remaining_count=0
stop_reason=""
unreached_packages=""
# Per-package stops (a package whose listing left too little of the budget to
# verify its deletions). They do not stop the next package from being listed.
package_stops=""
# "<pkg>:<id>" of DELETE calls whose answer was lost (timed out, 5xx, no HTTP
# answer), sorted by what the verification listing showed: gone, still
# listed, or unknown because that listing could not run.
uncertain_confirmed=""
uncertain_present=""
uncertain_unknown=""
# Failed rechecks or DELETE calls since the last successful DELETE (#3492).
consecutive_refusals=0
readonly MAX_CONSECUTIVE_REFUSALS=3

# Actions keeps runs for 90 days from completion; GHCR updated_at reflects the
# push, so +1 day covers the skew. Sweeping below this window deletes
# ci-<run_id> tags of runs that can still be partially re-run (#1138).
readonly SAFE_MIN_AGE_DAYS=91

if [[ -z "$gh_token" ]]; then
	echo "GH_TOKEN is required" >&2
	exit 2
fi

# Validate a non-negative integer and store it, normalised to base 10, in the
# variable named by $1. Bash arithmetic reads a leading zero as octal, so an
# unnormalised "08" is an arithmetic error, and inside [[ -gt ]] that error
# quietly evaluates false — which is how MAX_DELETES=08 disabled the cap.
parse_uint() {
	local var="$1"
	local name="$2"
	local value="$3"
	local min="$4"
	local max="$5"
	if ! [[ "$value" =~ ^[0-9]{1,9}$ ]]; then
		echo "${name} must be a non-negative integer, got: ${value}" >&2
		exit 2
	fi
	value=$((10#$value))
	if ((value < min || value > max)); then
		echo "${name} must be between ${min} and ${max}, got: ${value}" >&2
		exit 2
	fi
	printf -v "$var" '%d' "$value"
}

min_age_days=0
time_budget_seconds=0
verify_reserve_seconds=0
gh_call_timeout=0
max_deletes=0
page_size=0
package_rotation=0
parse_uint min_age_days MIN_AGE_DAYS "${MIN_AGE_DAYS:-91}" 0 36500
parse_uint time_budget_seconds TIME_BUDGET_SECONDS \
	"${TIME_BUDGET_SECONDS:-1200}" 0 86400
parse_uint verify_reserve_seconds VERIFY_RESERVE_SECONDS \
	"${VERIFY_RESERVE_SECONDS:-300}" 0 86400
parse_uint gh_call_timeout GH_CALL_TIMEOUT_SECONDS \
	"${GH_CALL_TIMEOUT_SECONDS:-60}" 1 3600
parse_uint max_deletes MAX_DELETES "${MAX_DELETES:-500}" 0 100000
parse_uint page_size PAGE_SIZE "${PAGE_SIZE:-100}" 1 100
parse_uint package_rotation PACKAGE_ROTATION "${PACKAGE_ROTATION:-0}" \
	0 999999999

if ((time_budget_seconds > 0)); then
	if ((verify_reserve_seconds >= time_budget_seconds)); then
		echo "VERIFY_RESERVE_SECONDS (${verify_reserve_seconds}) must be below" \
			"TIME_BUDGET_SECONDS (${time_budget_seconds})" >&2
		exit 2
	fi
	# A recheck-and-delete is up to three calls; the reserve must absorb one
	# that starts just before the work deadline, or verification has no room.
	if ((verify_reserve_seconds < 3 * gh_call_timeout)); then
		echo "VERIFY_RESERVE_SECONDS (${verify_reserve_seconds}) must be at" \
			"least 3 x GH_CALL_TIMEOUT_SECONDS (${gh_call_timeout})" >&2
		exit 2
	fi
fi

if ((min_age_days < SAFE_MIN_AGE_DAYS)); then
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

# Prefixes are matched against tags literally; restricting them to the
# container-tag character set keeps globs and quotes out of the matching.
if ! [[ "$tag_prefix" =~ ^[A-Za-z0-9._-]+$ ]]; then
	echo "TAG_PREFIX must match ^[A-Za-z0-9._-]+\$, got: ${tag_prefix}" >&2
	exit 2
fi

# The verification baseline must ignore every tag family that some sweep may
# legitimately delete, otherwise a sibling job's work reads as a lost tag
# (#1652). TAG_PREFIX is always excluded; the rest come from
# VOLATILE_TAG_PREFIXES.
volatile_list="$tag_prefix"
for prefix in $volatile_prefixes; do
	if ! [[ "$prefix" =~ ^[A-Za-z0-9._-]+$ ]]; then
		echo "VOLATILE_TAG_PREFIXES entries must match" \
			"^[A-Za-z0-9._-]+\$, got: ${prefix}" >&2
		exit 2
	fi
	[[ "$prefix" == "$tag_prefix" ]] && continue
	volatile_list+=" ${prefix}"
done
readonly volatile_list

if ! command -v timeout >/dev/null 2>&1; then
	echo "timeout(1) is required to bound each gh call (#3492)" >&2
	exit 2
fi

now_epoch() {
	date -u +%s
}

start_epoch="$(now_epoch)"
cutoff_epoch=$((start_epoch - min_age_days * 86400))
# Seconds since start after which no new work / no verification starts.
work_deadline=$((time_budget_seconds - verify_reserve_seconds))
verify_deadline="$time_budget_seconds"

# Succeed while fewer than $1 seconds have passed (always, with no budget).
before_deadline() {
	local limit="$1"
	((time_budget_seconds == 0)) && return 0
	(($(now_epoch) - start_epoch < limit))
}

# Run gh under timeout(1) so one hung call cannot outlive the budget.
gh_call() {
	local rc=0
	timeout --kill-after=5 "$gh_call_timeout" gh "$@" || rc=$?
	if is_timeout_status "$rc"; then
		echo "gh call timed out after ${gh_call_timeout}s" >&2
	fi
	return "$rc"
}

# timeout(1) exits 124 when it stopped the call and 137 when it had to kill it.
is_timeout_status() {
	(($1 == 124 || $1 == 137))
}

# One TSV row per version: <id>\t<updated_at>\t<updated epoch>\t<tags>.
readonly LIST_JQ='.[]
	| [(.id | tostring),
		.updated_at,
		(.updated_at | fromdateiso8601 | tostring),
		((.metadata.container.tags // []) | join(" "))]
	| @tsv'

# List every version of package $1 into file $3, a page at a time, checking
# deadline $2 (seconds since start) before each page. Only an empty page ends
# the listing: the API may return a short page before the last one, and
# stopping there would silently drop versions from the persistent-tag
# baseline. That costs one extra request per listing.
# Returns 0 when complete, 3 when the deadline came first, and gh's status
# (with its stderr) when a request failed.
list_versions() {
	local pkg="$1"
	local deadline="$2"
	local out="$3"
	local page=1
	local rows=""
	local line=""
	local count=0
	: >"$out"
	while true; do
		if ! before_deadline "$deadline"; then
			return 3
		fi
		rows=$(gh_call api \
			"orgs/${org}/packages/container/${pkg}/versions?per_page=${page_size}&page=${page}" \
			--jq "$LIST_JQ") || return $?
		count=0
		while IFS= read -r line; do
			[[ -z "$line" ]] && continue
			printf '%s\n' "$line" >>"$out"
			count=$((count + 1))
		done <<<"$rows"
		if ((count == 0)); then
			return 0
		fi
		page=$((page + 1))
	done
}

# Print the persistent tags (those carrying none of the volatile prefixes) in
# listing file $1, one per line, sorted. This is the invariant the post-delete
# verification checks: the sweep only deletes sole-ci-tagged versions, so this
# set may never shrink (#1652).
persistent_tags_from() {
	awk -F'\t' -v prefixes="$volatile_list" '
		BEGIN { n = split(prefixes, p, " ") }
		{
			m = split($4, tags, " ")
			for (i = 1; i <= m; i++) {
				volatile = 0
				for (j = 1; j <= n; j++) {
					if (index(tags[i], p[j]) == 1) { volatile = 1; break }
				}
				if (!volatile) print tags[i]
			}
		}' "$1" | LC_ALL=C sort -u
}

# Print "<id>\t<updated_at>\t<tags>" for each version in listing file $1 that
# has tags, only TAG_PREFIX tags, and is older than the cutoff.
candidates_from() {
	awk -F'\t' -v prefix="$tag_prefix" -v cutoff="$cutoff_epoch" '
		$4 != "" && ($3 + 0) < cutoff {
			m = split($4, tags, " ")
			for (i = 1; i <= m; i++) {
				if (index(tags[i], prefix) != 1) next
			}
			printf "%s\t%s\t%s\n", $1, $2, $4
		}' "$1"
}

# Print package $1's persistent tags from a fresh listing bounded by the
# verification deadline.
fetch_persistent_tags() {
	local pkg="$1"
	local rows_file=""
	local rc=0
	rows_file="$(mktemp)"
	list_for_verification "$pkg" "$rows_file" || rc=$?
	if ((rc == 0)); then
		persistent_tags_from "$rows_file"
	fi
	rm -f "$rows_file"
	return "$rc"
}

# List package $1 into file $2, bounded by the verification deadline.
list_for_verification() {
	local rc=0
	list_versions "$1" "$verify_deadline" "$2" || rc=$?
	if ((rc == 3)); then
		echo "the ${time_budget_seconds}s budget ran out before the listing" \
			"finished" >&2
	fi
	return "$rc"
}

# Decide each uncertain DELETE of package $1 (ids in $3) from the complete
# verification listing in file $2: listed means it did not go through, absent
# means it did. With no listing ($2 empty) the outcome stays unknown, which
# fails the run.
resolve_uncertain_deletes() {
	local pkg="$1"
	local rows_file="$2"
	local vid=""
	for vid in $3; do
		if [[ -z "$rows_file" ]]; then
			uncertain_unknown+="${pkg}:${vid} "
			echo "::error::The DELETE outcome of ${pkg} version ${vid} is" \
				"unknown: the verification listing could not run" >&2
			sweep_errors=1
		elif awk -F'\t' -v id="$vid" '$1 == id { found = 1 } END { exit !found }' \
			"$rows_file"; then
			uncertain_present+="${pkg}:${vid} "
			swept_count=$((swept_count - 1))
			remaining_count=$((remaining_count + 1))
			echo "The DELETE of ${pkg} version ${vid} did not go through: it" \
				"is still listed and stays for the next run"
		else
			uncertain_confirmed+="${pkg}:${vid} "
			echo "The DELETE of ${pkg} version ${vid} went through: it is gone" \
				"from the verification listing"
		fi
	done
}

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
	gh_call api \
		"orgs/${org}/packages/container/${pkg}/versions/${vid}" \
		--jq '[.updated_at, ((.metadata.container.tags // []) | join(" "))] | @tsv'
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
# outright, or cannot finish inside the budget, is reported as "could not
# verify" and still fails the job, keeping the sweep fail-closed without
# claiming a loss that was never observed.
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
	local uncertain_ids="$4"
	local after=""
	local missing=""
	local err_file=""
	local rows_file=""

	err_file="$(mktemp)"
	rows_file="$(mktemp)"
	if ! list_for_verification "$pkg" "$rows_file" 2>"${err_file}"; then
		echo "::error::Could not verify persistent tags for ${pkg} after" \
			"deleting [${deleted_ids}]: $(cat "${err_file}") (#1652)" >&2
		rm -f "${err_file}" "${rows_file}"
		sweep_errors=1
		resolve_uncertain_deletes "$pkg" "" "$uncertain_ids"
		return
	fi
	rm -f "${err_file}"
	resolve_uncertain_deletes "$pkg" "$rows_file" "$uncertain_ids"
	after="$(persistent_tags_from "${rows_file}")"
	rm -f "${rows_file}"

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

# Set stop_reason once the run may start no more work at all (#3492): the
# delete cap is reached (dry runs are exempt) or the work window
# (TIME_BUDGET_SECONDS minus VERIFY_RESERVE_SECONDS) has passed. Once set it
# stays set; what is left belongs to the next scheduled run.
check_run_bound() {
	[[ -n "$stop_reason" ]] && return 0
	if [[ "$dry_run" != "true" ]] && ((max_deletes > 0)) &&
		((swept_count >= max_deletes)); then
		stop_reason="cap of ${max_deletes} deletions reached"
		return 0
	fi
	if ! before_deadline "$work_deadline"; then
		stop_reason="time budget for new work spent: ${work_deadline}s of"
		stop_reason+=" ${time_budget_seconds}s"
		return 0
	fi
	return 1
}

# Print the first HTTP status code in file $1, or nothing.
http_status_in() {
	{ grep -Eo 'HTTP[[:space:]]*[0-9]{3}' "$1" || true; } |
		head -n 1 | grep -Eo '[0-9]{3}' || true
}

# Record a failed recheck or DELETE ($1 names it, $2 is its HTTP status or
# empty) (#3492). A 403 or 429 (forbidden or rate limited) stops every
# further write this run at once; any other failure stops them after
# MAX_CONSECUTIVE_REFUSALS in a row, so a registry that keeps refusing is not
# hammered for the whole work window. Either way the run fails.
note_refusal() {
	local what="$1"
	local status="$2"
	sweep_errors=1
	consecutive_refusals=$((consecutive_refusals + 1))
	[[ -n "$stop_reason" ]] && return 0
	if [[ "$status" == "403" || "$status" == "429" ]]; then
		stop_reason="rate limited or forbidden (HTTP ${status} on ${what});"
		stop_reason+=" stopped all rechecks and DELETE calls"
	elif ((consecutive_refusals >= MAX_CONSECUTIVE_REFUSALS)); then
		stop_reason="repeatedly refused: ${consecutive_refusals} failed rechecks"
		stop_reason+=" or DELETE calls in a row (last: ${what}); stopped all"
		stop_reason+=" rechecks and DELETE calls"
	fi
	return 0
}

# Every reason this run stopped short, run-wide first, "; "-joined.
stop_reasons() {
	local reasons="$stop_reason"
	if [[ -n "$package_stops" ]]; then
		[[ -n "$reasons" ]] && reasons+="; "
		reasons+="${package_stops%; }"
	fi
	printf '%s' "$reasons"
}

sweep_package() {
	local pkg="$1"
	local rows_file=""
	local err_file=""
	local list_rc=0
	local list_started=0
	local list_seconds=0
	local versions=""
	local delete_deadline="$work_deadline"
	local reserve=0
	local package_stopped=""

	if check_run_bound; then
		echo "Not listing ${pkg}: ${stop_reason}; it resumes next run"
		unreached_packages+="${pkg} "
		return
	fi

	echo "Listing ${pkg} versions"
	rows_file="$(mktemp)"
	err_file="$(mktemp)"
	list_started="$(now_epoch)"
	list_versions "$pkg" "$work_deadline" "$rows_file" 2>"${err_file}" ||
		list_rc=$?
	list_seconds=$(($(now_epoch) - list_started))
	if ((list_rc == 3)); then
		check_run_bound || true
		echo "Listing ${pkg} did not finish within the time budget" \
			"(${list_seconds}s); it resumes next run"
		unreached_packages+="${pkg} "
		rm -f "${rows_file}" "${err_file}"
		return
	fi
	if ((list_rc != 0)); then
		echo "::error::Failed to query versions for ${pkg}: $(cat "${err_file}")" >&2
		rm -f "${rows_file}" "${err_file}"
		sweep_errors=1
		return
	fi
	rm -f "${err_file}"
	echo "Listed $(wc -l <"${rows_file}" | tr -d ' ') ${pkg} versions in" \
		"${list_seconds}s"

	versions="$(candidates_from "${rows_file}")"
	if [[ -z "$versions" ]]; then
		echo "No ${tag_prefix}* tags older than ${min_age_days}d for ${pkg}"
		rm -f "${rows_file}"
		return
	fi

	# The persistent tags that must survive the deletions (#1652), taken from
	# the same listing as the candidates, so no extra pass is paid. DRY_RUN
	# deletes nothing, so it needs no baseline.
	local baseline_tags=""
	local deleted_ids=""
	local uncertain_ids=""
	if [[ "$dry_run" != "true" ]]; then
		baseline_tags="$(persistent_tags_from "${rows_file}")"
	fi
	rm -f "${rows_file}"

	# Leave this package's verification (up to two listings) room before
	# the budget ends: stop deleting at the larger of the fixed reserve and
	# twice the listing time just measured.
	if ((time_budget_seconds > 0)); then
		reserve="$verify_reserve_seconds"
		if ((2 * list_seconds > reserve)); then
			reserve=$((2 * list_seconds))
		fi
		delete_deadline=$((time_budget_seconds - reserve))
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
		# Per-run bound (#3492): once spent, only count what is left.
		if check_run_bound; then
			remaining_count=$((remaining_count + 1))
			continue
		fi
		# Per-package bound: past this package's delete deadline its
		# verification might not fit, but the next package may still
		# have time to list and delete, so only this package stops.
		if [[ -n "$package_stopped" ]] ||
			! before_deadline "$delete_deadline"; then
			if [[ -z "$package_stopped" ]]; then
				package_stopped=1
				package_stops+="${pkg}: its ${list_seconds}s listing leaves"
				package_stops+=" too little of the ${time_budget_seconds}s"
				package_stops+=" budget to verify deletions; "
			fi
			remaining_count=$((remaining_count + 1))
			continue
		fi
		if [[ "$dry_run" == "true" ]]; then
			echo "[dry-run] Would delete ${pkg} version ${vid} (tags: ${tags})"
			swept_count=$((swept_count + 1))
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
		local recheck_rc=0
		# Dual recheck narrows the promotion/TOCTOU window before DELETE.
		for _ in 1 2; do
			recheck_err_file="$(mktemp)"
			recheck_rc=0
			state=$(fetch_version_state "$pkg" "$vid" \
				2>"${recheck_err_file}") || recheck_rc=$?
			if ((recheck_rc != 0)); then
				# 404 = already deleted by a prior sweep/manual cleanup —
				# benign for a reclaim job. A call that timed out has no
				# definitive answer whatever it printed first, so it is
				# never read as a 404 (#3492). Auth/network/rate-limit still
				# fail closed so we do not silently under-delete.
				if ! is_timeout_status "$recheck_rc" &&
					grep -Eqi 'HTTP[[:space:]]*404|Not Found \(HTTP 404\)' \
						"${recheck_err_file}"; then
					echo "Skipping version ${vid} (${pkg}): already deleted" \
						"(404 on recheck)"
					rm -f "${recheck_err_file}"
					safe_to_delete=0
					break
				fi
				echo "::error::Failed to re-check version ${vid}; skipping" \
					"deletion: $(cat "${recheck_err_file}")" >&2
				local recheck_status=""
				if ! is_timeout_status "$recheck_rc"; then
					recheck_status="$(http_status_in "${recheck_err_file}")"
				fi
				note_refusal "recheck of ${pkg}:${vid}" "$recheck_status"
				rm -f "${recheck_err_file}"
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
		local delete_rc=0
		delete_err_file="$(mktemp)"
		delete_rc=0
		gh_call api --method DELETE \
			"orgs/${org}/packages/container/${pkg}/versions/${vid}" \
			>/dev/null 2>"${delete_err_file}" || delete_rc=$?
		local delete_status=""
		# A call that timed out has no answer, whatever it printed first.
		if ((delete_rc != 0)) && ! is_timeout_status "$delete_rc"; then
			delete_status="$(http_status_in "${delete_err_file}")"
		fi
		if ((delete_rc == 0)); then
			deleted_ids+="${vid} "
			swept_count=$((swept_count + 1))
			consecutive_refusals=0
			echo "Deleted ${pkg} version ${vid} (tags: ${current_tags})"
		elif [[ "$delete_status" == "404" ]]; then
			# Gone already (a prior sweep or a manual cleanup), as with
			# a 404 on recheck: benign for a reclaim job.
			echo "Skipping version ${vid} (${pkg}): already deleted" \
				"(404 on DELETE)"
		elif [[ "$delete_status" == 4* ]]; then
			# Any other 4xx is a definitive refusal: nothing was deleted.
			echo "::error::Failed to delete ${pkg} version ${vid}:" \
				"$(cat "${delete_err_file}")" >&2
			note_refusal "DELETE of ${pkg}:${vid}" "$delete_status"
		else
			# Timed out, 5xx, or no HTTP answer at all: the DELETE may have
			# gone through before the answer was lost (#3492). Count it
			# against the cap, start no more deletes this run, and let the
			# verification listing decide whether it went through.
			echo "::warning::The DELETE of ${pkg} version ${vid} has an" \
				"uncertain outcome and may have gone through; counting it" \
				"against the cap, stopping deletions, and checking the" \
				"verification listing for it: $(cat "${delete_err_file}")" >&2
			deleted_ids+="${vid} "
			uncertain_ids+="${vid} "
			swept_count=$((swept_count + 1))
			if [[ -z "$stop_reason" ]]; then
				stop_reason="the DELETE of ${pkg}:${vid} had an uncertain"
				stop_reason+=" outcome"
			fi
		fi
		rm -f "${delete_err_file}"
	done <<<"$versions"

	if [[ -n "$deleted_ids" ]]; then
		verify_persistent_tags_survived "$pkg" "$baseline_tags" \
			"${deleted_ids% }" "${uncertain_ids% }"
	fi
}

# Print the run summary and mirror it to the job summary when there is one.
# A partial run that deleted nothing, or left a package unswept, is also a
# ::warning:: so a sweep that is stuck week after week does not look healthy.
report_progress() {
	local verb="deleted"
	local line=""
	local reasons=""
	reasons="$(stop_reasons)"
	[[ "$dry_run" == "true" ]] && verb="would delete"
	# Errors win over "partial" so a failed run is never summarised as a
	# healthy partial one; the progress numbers are kept either way.
	if [[ "$sweep_errors" -ne 0 ]]; then
		line="Sweep finished with errors: ${swept_count} ${verb}"
	elif [[ -n "$reasons" ]]; then
		line="Partial sweep, resumes next run: ${swept_count} ${verb}"
	else
		line="Sweep complete: ${swept_count} ${verb}"
	fi
	if [[ -n "$reasons" ]]; then
		line+=", ${remaining_count} remaining (${reasons})"
		if [[ -n "$unreached_packages" ]]; then
			line+="; not swept this run: ${unreached_packages% }"
		fi
	fi
	if [[ -n "$uncertain_confirmed" ]]; then
		line+="; uncertain DELETE confirmed deleted: ${uncertain_confirmed% }"
	fi
	if [[ -n "$uncertain_present" ]]; then
		line+="; uncertain DELETE still present: ${uncertain_present% }"
	fi
	if [[ -n "$uncertain_unknown" ]]; then
		line+="; DELETE outcome unknown: ${uncertain_unknown% }"
	fi
	if [[ "$sweep_errors" -ne 0 ]]; then
		echo "::error::${line}"
	elif [[ -n "$reasons" ]]; then
		echo "::notice::${line}"
	else
		echo "$line"
	fi
	if [[ -n "$reasons" ]]; then
		if ((swept_count == 0)); then
			echo "::warning::The ${tag_prefix}* sweep made no progress this" \
				"run (${reasons}). If this repeats, the backlog is not" \
				"draining (#3492)."
		fi
		if [[ -n "$unreached_packages" ]]; then
			echo "::warning::Not swept this run: ${unreached_packages% }" \
				"(${reasons}). The next run starts from another package" \
				"(#3492)."
		fi
	fi
	if [[ -n "${GITHUB_STEP_SUMMARY:-}" ]]; then
		printf '%s\n' "${tag_prefix}* sweep: ${line}" >>"$GITHUB_STEP_SUMMARY" ||
			true
	fi
}

echo "Sweeping ${tag_prefix}* tags older than ${min_age_days}d" \
	"(dry_run=${dry_run}, time_budget=${time_budget_seconds}s," \
	"max_deletes=${max_deletes})"

read -r -a package_list <<<"$packages"
package_count="${#package_list[@]}"
if ((package_count > 0)); then
	first=$((package_rotation % package_count))
	ordered=()
	for ((i = 0; i < package_count; i++)); do
		ordered+=("${package_list[$(((first + i) % package_count))]}")
	done
	echo "Package order this run: ${ordered[*]}"
	for pkg in "${ordered[@]}"; do
		sweep_package "$pkg"
	done
fi

report_progress

if [[ "$sweep_errors" -ne 0 ]]; then
	echo "::error::GHCR CI-tag sweep completed with errors" >&2
	exit 1
fi
