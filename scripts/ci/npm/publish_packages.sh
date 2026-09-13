#!/usr/bin/env bash
# publish_packages.sh
# Publish the lintro npm packages (platform packages first, then the
# meta-package). Publishing is DRY-RUN unless LIVE=1 is set. The tag pipeline
# (publish-npm.yml, gated by the `npm` environment) sets LIVE=1; authentication
# is via npm trusted publishing (OIDC), so no NODE_AUTH_TOKEN is required.
# Caller must provide npm ≥ 11.5.1 (Node 24 bundled npm in CI). Do not
# self-upgrade npm in-place before invoking this script.
#
# Resilience (see issue #1682): each publish is wrapped in bounded exponential
# backoff that retries ONLY transient Sigstore/registry failures (notably the
# `TLOG_CREATE_ENTRY_ERROR` Rekor 409 that half-published v0.91.15). Auth and
# validation failures — including the `E404` npm returns when a publish is not
# authorized (see issue #2247) — are never retried; retrying them only hides
# the real problem. Combined with the idempotency skip below, a re-run repairs
# a partial publish instead of compounding it.
#
# Dist-tag reconciliation (see issues #1691 and #2631): on both idempotent
# paths — the `npm view` pre-check skip and the EPUBLISHCONFLICT
# conflict-as-success branch — the requested dist-tag is reconciled. The
# reconcile reads before it writes (`npm dist-tag ls` is an unauthenticated
# read): when the tag already points at this version nothing is written,
# which is the path every already-published package takes on a live re-run.
# A fresh publish never calls dist-tag because publish applies --tag
# atomically. publish-npm.yml authenticates via npm trusted publishing (OIDC),
# whose exchanged token is scoped to `npm publish` only — the registry rejects
# `npm dist-tag add` under it (npm/cli#8547 is still open). A reconcile that
# needs a write and fails is therefore recorded as dist-tag drift — one
# ::warning:: per drifted package plus a step-summary line — and the run
# exits non-zero only after every package has been processed, so a
# half-published release still publishes its remaining packages instead of
# aborting on the first mistagged one. Transient registry/network blips on
# the reconcile itself get the same bounded backoff as publish, so a single
# 5xx does not give up on a tag after one attempt.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd)"
NPM_DIR="$REPO_ROOT/npm"

if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
	cat <<'EOF'
Publish lintro npm packages.

Usage: publish_packages.sh

Environment:
  LIVE=1                     Perform a real publish. Default (unset) is
                             --dry-run.
  NPM_PROVENANCE=0           Disable --provenance (default: enabled). The retry
                             re-attempts the same signed publish; it never
                             falls back to an unsigned one.
  NPM_DIST_TAG               Dist-tag for npm publish (default: latest). Use a
                             non-latest tag (e.g. backfill) when publishing a
                             version lower than the current latest — npm refuses
                             to move latest backwards without an explicit --tag.
  NPM_PUBLISH_MAX_ATTEMPTS   Max attempts per package publish or dist-tag
                             reconcile on a transient error (default: 3).
  NPM_PUBLISH_RETRY_DELAY    Base backoff in seconds; doubles each retry
                             (default: 5).

Publishes @lgtm-hq/lintro-<platform> packages first, then the root meta-package,
so consumers never resolve a meta-package whose optional deps are missing.
EOF
	exit 0
fi

# Platform packages before the meta-package: the meta-package's
# optionalDependencies must exist on the registry first.
PACKAGES=(
	"darwin-arm64"
	"linux-arm64"
	"linux-x64"
	"lintro"
)

publish_flags=("--access" "public")
if [[ "${NPM_PROVENANCE:-1}" != "0" ]]; then
	publish_flags+=("--provenance")
fi
# Always pass --tag so backfills of older versions do not try to move latest.
# Use ${VAR-default} (no colon) so an explicitly empty NPM_DIST_TAG still
# triggers the guard below, while an unset var falls back to "latest".
dist_tag="${NPM_DIST_TAG-latest}"
if [[ -z "$dist_tag" ]]; then
	echo "ERROR: NPM_DIST_TAG must be non-empty (use 'latest' for normal releases)" >&2
	exit 1
fi
publish_flags+=("--tag" "$dist_tag")
if [[ "${LIVE:-0}" != "1" ]]; then
	publish_flags+=("--dry-run")
	echo "DRY-RUN mode: no packages will be published. Set LIVE=1 to publish."
else
	echo "LIVE mode: packages WILL be published to the registry (dist-tag=$dist_tag)."
fi

max_attempts="${NPM_PUBLISH_MAX_ATTEMPTS:-3}"
retry_base_delay="${NPM_PUBLISH_RETRY_DELAY:-5}"
# Ceiling for the exponential backoff. Without it, a high attempt count would
# double the delay unboundedly and stall the release job for hours.
retry_max_delay="${NPM_PUBLISH_MAX_DELAY:-60}"
# Reject non-integer / negative / octal-looking values up front: bad values
# would otherwise break arithmetic in the retry loop or loop unexpectedly.
if [[ ! "$max_attempts" =~ ^[1-9][0-9]*$ ]]; then
	echo "ERROR: NPM_PUBLISH_MAX_ATTEMPTS must be a positive integer (got '$max_attempts')" >&2
	exit 1
fi
if [[ ! "$retry_base_delay" =~ ^(0|[1-9][0-9]*)$ ]]; then
	echo "ERROR: NPM_PUBLISH_RETRY_DELAY must be a non-negative integer (got '$retry_base_delay')" >&2
	exit 1
fi
if [[ ! "$retry_max_delay" =~ ^(0|[1-9][0-9]*)$ ]]; then
	echo "ERROR: NPM_PUBLISH_MAX_DELAY must be a non-negative integer (got '$retry_max_delay')" >&2
	exit 1
fi

# Non-retryable failures: authentication, permission, and validation errors.
# These are checked BEFORE the transient patterns because an auth message can
# also mention a Sigstore component (e.g. "sigstore authentication failed
# (E401)"), and retrying it would only hide the real problem.
#
# E404 on a publish is an authorization failure, not a missing resource: npm
# masks "you are not allowed to publish here" as a 404 ("could not be found or
# you do not have permission to access it" — that prose is matched too, so the
# classification does not hinge on npm printing the code). It is what an
# unauthenticated trusted-publishing fallback produces, so retrying it burns
# three attempts per package on a permanent condition (see issue #2247). The
# E404 handling in the `npm view` pre-check below is unrelated: there a 404
# legitimately means "this version is not published yet".
NON_RETRYABLE_ERROR_RE='E401|E403|E402|E404|ENEEDAUTH|EOTP|EPERM|unauthorized|forbidden|authentication failed|permission denied|do not have permission'
# Transient failures that are safe to retry: the Rekor transparency-log 409
# (TLOG_CREATE_ENTRY_ERROR), other Sigstore/tlog hiccups, registry 5xx,
# rate-limit 429s, and transient network errors.
TRANSIENT_ERROR_RE='TLOG_CREATE_ENTRY_ERROR|creating tlog entry|transparency log|rekor|fulcio|sigstore|ETIMEDOUT|ECONNRESET|EAI_AGAIN|ENOTFOUND|socket hang up|5[0-9][0-9] (internal server error|bad gateway|service unavailable|gateway time-?out)|internal server error|bad gateway|service unavailable|gateway time-?out|EAGAIN|E429|429 too many requests'
# A publish conflict means the exact name@version is already on the registry —
# the desired end state. Treat it as an idempotent success (a prior attempt in
# this loop or an earlier run landed the tarball) rather than a failure.
ALREADY_PUBLISHED_RE='EPUBLISHCONFLICT|cannot publish over|previously published version|already published'

# Set to 1 when at least one already-published package could not be
# reconciled to the requested dist-tag. A reconcile failure no longer aborts
# the loop: every missing package still gets published, each drifted package
# emits a ::warning:: plus a step-summary line, and the deferred non-zero
# exit at the end of the run keeps it red (see #2631).
dist_tag_drift=0

# Record one unreconciled dist-tag as drift: set the flag the end-of-loop
# exit reads, and append one line to the GitHub step summary when running in
# Actions. The ::warning:: itself is emitted at the failure site, where the
# error kind (and therefore the accurate remediation) is known.
#
# Args:
#   $1: package name. $2: version the tag should point at. $3: the tag's
#   actual target as read from the registry ("unknown" when the read failed).
_record_dist_tag_drift() {
	local dr_name="$1"
	local dr_version="$2"
	local dr_actual="$3"
	dist_tag_drift=1
	if [[ -n "${GITHUB_STEP_SUMMARY:-}" ]]; then
		echo "- **${dr_name}@${dr_version}**: dist-tag \`$dist_tag\` points at \`${dr_actual}\`, not \`${dr_version}\`; the reconcile write was rejected (OIDC trusted publishing cannot run \`npm dist-tag\`, npm/cli#8547)." \
			>>"$GITHUB_STEP_SUMMARY"
	fi
}

# Reconcile the requested dist-tag for an already-published name@version.
#
# Read before write (#2631): `npm dist-tag ls` is an unauthenticated read on
# a public package. When it already shows the requested tag pointing at this
# version, the registry is in the desired end state and nothing is written —
# the path every already-published package takes on a live re-run. This
# matters because publish-npm.yml authenticates via npm trusted publishing
# (OIDC), whose exchanged token is scoped to `npm publish` only: the registry
# rejects `npm dist-tag add` under it (npm/cli#8547), so the write must only
# be attempted when the read proves it necessary. A failed read falls back to
# the write path rather than inventing a success.
#
# When a write is needed and fails, the failure is recorded as dist-tag
# drift, not aborted on: one ::warning:: per drifted package naming the
# expected and actual tags (and the OIDC remediation for auth-scope
# rejections), one step-summary line, and the drift flag that turns into the
# deferred non-zero exit after the loop — a half-published release must still
# publish its remaining packages (#1682's intent).
#
# `npm dist-tag add` itself is idempotent: a no-op when the tag already
# points at that version, and (unlike publish) allowed to move latest.
# Transient failures (network, registry 5xx, 429) get the same bounded
# exponential-backoff retry as publish_one: a single-attempt mutation would
# give up on a tag after one blip. Auth and validation rejections are never
# retried.
#
# Args:
#   $1: package name (e.g. "@lgtm-hq/lintro-linux-x64").
#   $2: package version.
# Returns:
#   0 when the tag is correct (already or after the write); 1 on drift that
#   could not be repaired, after recording the warning and the flag.
reconcile_dist_tag() {
	local dt_name="$1"
	local dt_version="$2"
	local dt_actual=""
	local dt_list
	if dt_list="$(npm dist-tag ls "$dt_name" 2>&1)"; then
		dt_actual="$(awk -v tag="$dist_tag" '
			{
				line = $0
				sub(/\r$/, "", line)
				sub(/[[:space:]]+$/, "", line)
				sub(/^- /, "", line)
				n = split(line, parts, ": ")
				if (n >= 2 && parts[1] == tag) {
					print parts[2]
					exit
				}
			}
		' <<<"$dt_list")"
		if [[ "$dt_actual" == "$dt_version" ]]; then
			echo "==> Dist-tag '$dist_tag' already points at $dt_name@$dt_version; nothing to reconcile."
			return 0
		fi
	fi
	local attempt=1
	local delay="$retry_base_delay"
	local dt_output dt_rc
	while :; do
		echo "==> Reconciling dist-tag '$dist_tag' for $dt_name@$dt_version (attempt $attempt/$max_attempts)"
		dt_output="$(npm dist-tag add "$dt_name@$dt_version" "$dist_tag" 2>&1)" && dt_rc=0 || dt_rc=$?
		printf '%s\n' "$dt_output"
		if [[ "$dt_rc" -eq 0 ]]; then
			return 0
		fi
		# The OIDC remediation only applies to auth-scope rejections; retrying
		# those would only hide the real problem.
		if grep -qiE "$NON_RETRYABLE_ERROR_RE" <<<"$dt_output"; then
			_record_dist_tag_drift "$dt_name" "$dt_version" "${dt_actual:-unknown}"
			echo "ERROR: could not reconcile dist-tag '$dist_tag' for $dt_name@$dt_version (exit $dt_rc)." >&2
			echo "ERROR: npm trusted publishing (OIDC) tokens are publish-scoped and cannot run 'npm dist-tag' (npm/cli#8547)." >&2
			echo "ERROR: re-apply the tag with classic auth: npm dist-tag add $dt_name@$dt_version $dist_tag" >&2
			echo "::warning::Dist-tag drift for $dt_name: '$dist_tag' should point at $dt_version but reads '${dt_actual:-unknown}' on the registry, and the write was rejected. npm trusted publishing (OIDC) tokens are publish-scoped and cannot run 'npm dist-tag' (npm/cli#8547). Re-apply with classic auth: npm dist-tag add $dt_name@$dt_version $dist_tag"
			return 1
		fi
		if grep -qiE "$TRANSIENT_ERROR_RE" <<<"$dt_output"; then
			if [[ "$attempt" -ge "$max_attempts" ]]; then
				_record_dist_tag_drift "$dt_name" "$dt_version" "${dt_actual:-unknown}"
				echo "ERROR: could not reconcile dist-tag '$dist_tag' for $dt_name@$dt_version after $max_attempts attempts on a transient error." >&2
				echo "::warning::Dist-tag drift for $dt_name: '$dist_tag' should point at $dt_version but reads '${dt_actual:-unknown}' on the registry, and the reconcile write kept failing (see the errors above). The remaining packages are still published; the run fails after the loop."
				return 1
			fi
			echo "WARNING: transient dist-tag error for $dt_name@$dt_version (attempt $attempt/$max_attempts); retrying in ${delay}s." >&2
			sleep "$delay"
			attempt=$((attempt + 1))
			delay=$((delay * 2))
			if [[ "$delay" -gt "$retry_max_delay" ]]; then
				delay="$retry_max_delay"
			fi
			continue
		fi
		_record_dist_tag_drift "$dt_name" "$dt_version" "${dt_actual:-unknown}"
		echo "ERROR: could not reconcile dist-tag '$dist_tag' for $dt_name@$dt_version (exit $dt_rc)." >&2
		echo "::warning::Dist-tag drift for $dt_name: '$dist_tag' should point at $dt_version but reads '${dt_actual:-unknown}' on the registry, and the reconcile write failed (see the errors above). The remaining packages are still published; the run fails after the loop."
		return 1
	done
}

# Publish one package directory with bounded, exponential-backoff retry on
# transient Sigstore/registry errors only.
#
# Args:
#   $1: package subdirectory under $NPM_DIR (e.g. "linux-arm64").
# Returns:
#   0 on a successful (or idempotently already-present) publish; 1 otherwise.
publish_one() {
	local pkg="$1"
	local pkg_dir="$NPM_DIR/$pkg"
	local attempt=1
	local delay="$retry_base_delay"
	local output rc
	while :; do
		echo "==> Publishing $pkg (attempt $attempt/$max_attempts) (${publish_flags[*]})"
		# Re-run the identical signed publish each attempt (provenance intact).
		# Capture combined output so we can both echo it and classify the error.
		output="$( (cd "$pkg_dir" && npm publish "${publish_flags[@]}") 2>&1)" && rc=0 || rc=$?
		printf '%s\n' "$output"
		if [[ "$rc" -eq 0 ]]; then
			return 0
		fi
		if grep -qiE "$ALREADY_PUBLISHED_RE" <<<"$output"; then
			echo "==> $pkg already present on the registry (publish conflict); treating as an idempotent success." >&2
			# A fresh publish applies --tag atomically, but this version was
			# published earlier (possibly under a different tag): reconcile the
			# requested tag. A failed reconcile is recorded as drift and the
			# run exits non-zero after the loop (see reconcile_dist_tag).
			# Dry-runs never mutate the registry, so they skip reconciliation.
			if [[ "${LIVE:-0}" == "1" ]]; then
				reconcile_dist_tag \
					"$(node -p "require('$pkg_dir/package.json').name")" \
					"$(node -p "require('$pkg_dir/package.json').version")" || true
			fi
			return 0
		fi
		if grep -qiE "$NON_RETRYABLE_ERROR_RE" <<<"$output"; then
			echo "ERROR: $pkg publish failed with a non-retryable auth/validation error; not retrying." >&2
			return 1
		fi
		if grep -qiE "$TRANSIENT_ERROR_RE" <<<"$output"; then
			if [[ "$attempt" -ge "$max_attempts" ]]; then
				echo "ERROR: $pkg publish failed after $max_attempts attempts on a transient error." >&2
				return 1
			fi
			echo "WARNING: transient publish error for $pkg (attempt $attempt/$max_attempts); retrying in ${delay}s." >&2
			sleep "$delay"
			attempt=$((attempt + 1))
			delay=$((delay * 2))
			if [[ "$delay" -gt "$retry_max_delay" ]]; then
				delay="$retry_max_delay"
			fi
			continue
		fi
		echo "ERROR: $pkg publish failed with a non-transient error (exit $rc); not retrying." >&2
		return 1
	done
}

for pkg in "${PACKAGES[@]}"; do
	pkg_dir="$NPM_DIR/$pkg"
	# Idempotency: if this exact name@version is already on the registry
	# (e.g. a rerun after a mid-loop failure published some packages), skip
	# it. Without this a retry would fail on the already-published versions
	# and leave the release partially published. Only meaningful for a real
	# publish; dry-runs always run to exercise the tarball.
	if [[ "${LIVE:-0}" == "1" ]]; then
		pkg_name="$(node -p "require('$pkg_dir/package.json').name")"
		pkg_version="$(node -p "require('$pkg_dir/package.json').version")"
		# Distinguish "version not published" (npm E404) from a lookup that
		# failed for another reason (network, rate-limit, 5xx).
		# Redirect order is intentional: inside $() stdout is the capture pipe,
		# so `2>&1` routes stderr into it and `>/dev/null` then discards stdout
		# only. view_err therefore holds just the error text. Do NOT "simplify"
		# this to `>/dev/null 2>&1` — that discards both streams and would
		# break the E404 classification below.
		view_err="$(npm view "$pkg_name@$pkg_version" version 2>&1 >/dev/null)" && view_ok=1 || view_ok=0
		if [[ "$view_ok" == "1" ]]; then
			echo "==> Skipping $pkg_name@$pkg_version (already published)"
			# The version exists but may carry a different tag than this run
			# requested; reconcile it. A failed reconcile is recorded as drift
			# and the run exits non-zero after the loop, so the remaining
			# packages still publish (see reconcile_dist_tag).
			reconcile_dist_tag "$pkg_name" "$pkg_version" || true
			continue
		elif ! grep -qiE 'E404|404 Not Found|is not in this registry' <<<"$view_err"; then
			# The existence check itself failed, so we cannot prove the version
			# is absent. Fail safe by neither skipping nor aborting the whole
			# release: proceed to publish. publish_one() is conflict-safe — if
			# the version is in fact already present, npm's publish conflict is
			# treated as an idempotent success, and a genuine transient error is
			# retried. Aborting here would instead risk leaving a multi-package
			# release partially published on a mere lookup hiccup.
			echo "WARNING: could not verify $pkg_name@$pkg_version on the registry; proceeding to publish (publish is conflict-safe)." >&2
			echo "$view_err" >&2
		fi
	fi
	publish_one "$pkg"
done

# Deferred drift exit (#2631): every package has been processed, so the run
# can now go red for the tags that could not be reconciled. The output lets
# a caller (e.g. the recovery workflow) detect the drift programmatically.
if [[ "$dist_tag_drift" -eq 1 ]]; then
	if [[ -n "${GITHUB_OUTPUT:-}" ]]; then
		echo "dist_tag_drift=true" >>"$GITHUB_OUTPUT"
	fi
	echo "ERROR: npm publish step complete, but dist-tag drift remains (see the warnings above); failing the run." >&2
	exit 1
fi

echo "npm publish step complete."
