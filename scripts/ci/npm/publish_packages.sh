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
# validation failures are never retried — retrying them only hides the real
# problem. Combined with the idempotency skip below, a re-run repairs a partial
# publish instead of compounding it.

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
  NPM_PUBLISH_MAX_ATTEMPTS   Max publish attempts per package on a transient
                             error (default: 3).
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
	"darwin-x64"
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

# Transient failures that are safe to retry: the Rekor transparency-log 409
# (TLOG_CREATE_ENTRY_ERROR), other Sigstore/tlog hiccups, registry 5xx, and
# transient network errors. Auth (E401/E403/ENEEDAUTH/EOTP) and validation
# errors do NOT match and therefore fall through to a hard failure.
TRANSIENT_ERROR_RE='TLOG_CREATE_ENTRY_ERROR|creating tlog entry|transparency log|rekor|fulcio|sigstore|ETIMEDOUT|ECONNRESET|EAI_AGAIN|ENOTFOUND|socket hang up|(50[0-9]|5[0-9][0-9]) (internal server error|bad gateway|service unavailable|gateway time-?out)|internal server error|bad gateway|service unavailable|gateway time-?out|EAGAIN'
# A publish conflict means the exact name@version is already on the registry —
# the desired end state. Treat it as an idempotent success (a prior attempt in
# this loop or an earlier run landed the tarball) rather than a failure.
ALREADY_PUBLISHED_RE='EPUBLISHCONFLICT|cannot publish over|previously published version|already published|forbidden.*over'

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
		output="$( (cd "$pkg_dir" && npm publish "${publish_flags[@]}") 2>&1 )" && rc=0 || rc=$?
		printf '%s\n' "$output"
		if [[ "$rc" -eq 0 ]]; then
			return 0
		fi
		if grep -qiE "$ALREADY_PUBLISHED_RE" <<<"$output"; then
			echo "==> $pkg already present on the registry (publish conflict); treating as an idempotent success." >&2
			return 0
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
		view_err="$(npm view "$pkg_name@$pkg_version" version 2>&1 >/dev/null)" && view_ok=1 || view_ok=0
		if [[ "$view_ok" == "1" ]]; then
			echo "==> Skipping $pkg_name@$pkg_version (already published)"
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

echo "npm publish step complete."
