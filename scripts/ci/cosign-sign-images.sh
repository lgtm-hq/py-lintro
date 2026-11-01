#!/usr/bin/env bash
set -euo pipefail

# cosign-sign-images.sh
#
# Sign promoted image digests with Sigstore Cosign (keyless OIDC).
# Refs must be pinned by digest (image@sha256:...) so the signature is
# bound to the exact manifest CI validated and promoted (#1358) — never
# to a floating tag that could move between promotion and signing.
#
# Keyless signing fetches an ambient OIDC token from GitHub's token
# endpoint, which intermittently returns a non-JSON body and fails the
# whole signing job (#1567: v0.87.1 py-lintro-base was left unsigned by
# `invalid character 'u' looking for beginning of value`). That flake is
# transient and the tag-publish job cannot be re-run in isolation, so we
# retry ONLY that token-fetch failure class here with bounded exponential
# backoff (#1646). A genuine signing rejection or any non-token-fetch
# error stays fatal — it must never be retried away.
#
# Rekor can reject an equivalent log entry (#3114). That response alone is
# not evidence of a usable signature: recover only if cosign verifies an
# existing signature for the exact digest under the documented trust policy
# AND the current workflow's exact identity.

show_help() {
	cat <<'EOF'
Sign image digests with Cosign (keyless).
Requires Bash 4 or newer.

Usage:
  IMAGES=<refs> scripts/ci/cosign-sign-images.sh

Environment:
  IMAGES                    Newline-separated image refs pinned by
                            digest, e.g. ghcr.io/lgtm-hq/py-lintro@sha256:...
                            (required)
  GITHUB_WORKFLOW_REF        GitHub workflow path and ref (set by Actions);
                            required for equivalent-entry recovery.
  COSIGN_SIGN_MAX_ATTEMPTS  Max attempts per ref when the failure is a
                            transient OIDC token-fetch flake (default: 4)
  COSIGN_SIGN_BASE_DELAY    Base backoff in seconds; delay doubles each retry
                            (default: 2). Set 0 for no wait.
  COSIGN_SIGN_MAX_DELAY     Upper bound in seconds for any single backoff wait,
                            regardless of attempt number (default: 30).

Only the ambient-OIDC / ID-token-fetch failure class is retried. A genuine
signing rejection, a policy failure, or a non-digest ref fails immediately.
An equivalent-entry Rekor 409 succeeds only if an existing signature verifies
for the exact digest with the issuer and trusted identities from docs/docker.md
and the exact current workflow identity from a validated GITHUB_WORKFLOW_REF.
Repeated identical refs are signed once; different repositories stay separate.
EOF
}

if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
	show_help
	exit 0
fi

images="${IMAGES:-}"
max_attempts="${COSIGN_SIGN_MAX_ATTEMPTS:-4}"
base_delay="${COSIGN_SIGN_BASE_DELAY:-2}"
max_delay="${COSIGN_SIGN_MAX_DELAY:-30}"

if [[ -z "$images" ]]; then
	echo "IMAGES is required" >&2
	exit 2
fi

if ! [[ "$max_attempts" =~ ^[1-9][0-9]*$ ]]; then
	echo "COSIGN_SIGN_MAX_ATTEMPTS must be a positive integer, got: ${max_attempts}" >&2
	exit 2
fi

if ! [[ "$base_delay" =~ ^[0-9]+$ ]]; then
	echo "COSIGN_SIGN_BASE_DELAY must be a non-negative integer, got: ${base_delay}" >&2
	exit 2
fi

if ! [[ "$max_delay" =~ ^[0-9]+$ ]]; then
	echo "COSIGN_SIGN_MAX_DELAY must be a non-negative integer, got: ${max_delay}" >&2
	exit 2
fi

# Fixed-string markers for the transient ambient-OIDC token-fetch failure
# class only. These identify a token-endpoint flake — never a rejected or
# invalid signature, so matching them is safe to retry. Matched with
# grep -qiF (fixed strings, case-insensitive), no regex.
oidc_flake_markers=(
	"fetching ambient OIDC credentials"
	"retrieving ID token"
	"reading ID token"
)

# is_transient_oidc_error <output>
# Return 0 iff the cosign output is the retryable ambient-OIDC token-fetch
# flake. Any other failure (genuine signing rejection, policy failure,
# registry error) returns 1 and must stay fatal.
is_transient_oidc_error() {
	local output="$1"
	local marker
	for marker in "${oidc_flake_markers[@]}"; do
		if printf '%s' "$output" | grep -qiF -- "$marker"; then
			return 0
		fi
	done
	return 1
}

# Match the recorded Rekor equivalent-entry rejection, not arbitrary registry
# conflicts or an HTTP status alone. Verification remains mandatory afterwards.
is_equivalent_rekor_entry_error() {
	local output="$1"
	grep -qF -- '[POST /api/v1/log/entries][409] createLogEntryConflict' <<<"$output" &&
		grep -qF -- 'an equivalent entry already exists in the transparency log' <<<"$output"
}

# The published policy accepts build and promotion signatures. Recovery needs
# this caller's signature: an earlier build signature cannot prove it persisted.
verify_current_workflow_signature() {
	local ref="$1"
	local workflow_ref="${GITHUB_WORKFLOW_REF:-}"
	local identity="https://github.com/${workflow_ref}"
	# Keep this trust policy in sync with docs/docker.md and
	# docs/release-validation.md. Validate membership before exact verification.
	local trusted_identity_regexp='^https://github\.com/lgtm-hq/(py-lintro/\.github/workflows/(docker-ci|publish-pypi-on-tag)\.yml|lgtm-ci/\.github/workflows/reusable-docker-(build|multiplatform)\.yml)@'
	if ! [[ "$identity" =~ $trusted_identity_regexp ]] ||
		! [[ "$workflow_ref" =~ ^[^[:space:]@]+@(refs/(heads|tags)/[^[:space:]]+)$ ]] ||
		! git check-ref-format "${BASH_REMATCH[1]}" >/dev/null 2>&1; then
		echo "Recovery requires a valid, trusted GITHUB_WORKFLOW_REF; failing." >&2
		return 1
	fi
	cosign verify --new-bundle-format "$ref" \
		--certificate-identity "$identity" \
		--certificate-oidc-issuer https://token.actions.githubusercontent.com
}

# sign_ref_with_retry <ref>
# Sign a single digest ref, retrying only the transient OIDC token-fetch
# flake with exponential backoff. Exits non-zero (fatal) on any other
# failure or once attempts are exhausted.
sign_ref_with_retry() {
	local ref="$1"
	local attempt=1
	local output
	local status
	local delay

	while true; do
		echo "Signing ${ref} (attempt ${attempt}/${max_attempts})"
		set +e
		output="$(cosign sign --yes "$ref" 2>&1)"
		status=$?
		set -e

		if [[ -n "$output" ]]; then
			printf '%s\n' "$output"
		fi

		if [[ "$status" -eq 0 ]]; then
			return 0
		fi

		if is_equivalent_rekor_entry_error "$output"; then
			echo "Rekor equivalent-entry conflict for ${ref}; verifying an existing signature." >&2
			if verify_current_workflow_signature "$ref"; then
				echo "Verified existing current-workflow signature for ${ref}"
				return 0
			fi
			echo "Existing signature verification failed for ${ref}; failing." >&2
			return "$status"
		fi

		if ! is_transient_oidc_error "$output"; then
			echo "cosign sign failed for ${ref} (not a transient OIDC token-fetch flake); failing." >&2
			return "$status"
		fi

		if [[ "$attempt" -ge "$max_attempts" ]]; then
			echo "cosign sign kept hitting the OIDC token-fetch flake for ${ref} after ${max_attempts} attempt(s); failing." >&2
			return "$status"
		fi

		delay=$((base_delay * (1 << (attempt - 1))))
		# Cap the exponential growth so a large max-attempts value can never
		# schedule an unbounded wait.
		if [[ "$delay" -gt "$max_delay" ]]; then
			delay="$max_delay"
		fi
		echo "Transient OIDC token-fetch flake signing ${ref}; retrying in ${delay}s." >&2
		if [[ "$delay" -gt 0 ]]; then
			sleep "$delay"
		fi
		attempt=$((attempt + 1))
	done
}

refs=()
declare -A seen_refs=()
while IFS= read -r ref; do
	[[ -z "$ref" ]] && continue
	if ! [[ "$ref" =~ ^[^[:space:]@]+@sha256:[0-9a-f]{64}$ ]]; then
		echo "Refusing to sign non-digest ref: ${ref}" >&2
		echo "(signatures must bind to a digest, not a floating tag)" >&2
		exit 2
	fi
	# Signatures are stored per repository: deduplicate the complete immutable
	# ref, never the digest alone (two repositories may contain identical bytes).
	[[ -n "${seen_refs[$ref]:-}" ]] && continue
	seen_refs["$ref"]=1
	refs+=("$ref")
done <<<"$images"

if [[ ${#refs[@]} -eq 0 ]]; then
	echo "IMAGES did not contain any refs" >&2
	exit 2
fi

for ref in "${refs[@]}"; do
	sign_ref_with_retry "$ref"
done

echo "Signed ${#refs[@]} image(s)"
