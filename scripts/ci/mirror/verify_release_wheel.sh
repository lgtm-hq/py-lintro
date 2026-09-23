#!/usr/bin/env bash
# verify_release_wheel.sh
# Prove the wheel the mirror is about to pin is the bytes the release gate
# attested, before publish-mirror-release.sh pushes the mirror tag (#2742).
#
# wait-for-pypi-wheel.sh only proves a wheel exists on PyPI; it does not
# compare bytes. Two checks here, both fail closed:
#   1. sha256 equality between the PyPI wheel URL's digest and the release's
#      SHA256SUMS manifest, which the release gate wrote over the assets it
#      verified (#2562);
#   2. `gh attestation verify` on the downloaded wheel, proving it was built
#      by lgtm-ci's build reusable (a reusable signs as the called file,
#      never as the entry workflow).
#
# A replaced PyPI artifact or one with no attestation fails here, before the
# bump branch is created and the mirror tag is pushed.

set -euo pipefail

if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
	cat <<'EOF'
Verify the PyPI wheel's digest against the release's SHA256SUMS and its attestation.

Usage:
  GH_TOKEN=<token> VERSION=<version> ATTESTATION_REPO=<owner/repo> \
    DIST_SIGNER_WORKFLOW=<owner/repo/.github/workflows/x.yml> \
    scripts/ci/mirror/verify_release_wheel.sh

Environment:
  VERSION                 lintro version without a leading v (required)
  ATTESTATION_REPO        Repository whose attestations are consulted,
                          passed as `--repo` (required)
  DIST_SIGNER_WORKFLOW    Signer workflow the wheel attestation must carry,
                          passed as `--signer-workflow` (required)
  RELEASE_TAG             Release tag the SHA256SUMS asset is fetched from
                          (default: v<VERSION>)
  WORK_DIR                Scratch directory (default: a mktemp dir, removed
                          on exit)
  GH_CMD                  gh binary name (overridable in tests; default gh)
  CURL_CMD                curl binary name (overridable in tests; default curl)
  SOURCE_REPO             owner/repo the SHA256SUMS release is fetched from
                          (default: lgtm-hq/py-lintro)
  GITHUB_STEP_SUMMARY     When set, one line per check is appended
EOF
	exit 0
fi

: "${VERSION:?VERSION is required}"
attestation_repo="${ATTESTATION_REPO:-}"
dist_signer="${DIST_SIGNER_WORKFLOW:-}"
release_tag="${RELEASE_TAG:-v${VERSION}}"
source_repo="${SOURCE_REPO:-lgtm-hq/py-lintro}"
gh_cmd="${GH_CMD:-gh}"
curl_cmd="${CURL_CMD:-curl}"

die() {
	echo "::error::$*" >&2
	exit 1
}

for var in ATTESTATION_REPO DIST_SIGNER_WORKFLOW; do
	if [[ -z "${!var:-}" ]]; then
		echo "${var} is required" >&2
		exit 2
	fi
done
for cmd in "$gh_cmd" "$curl_cmd" jq; do
	if ! command -v "$cmd" >/dev/null 2>&1; then
		echo "${cmd} not found; the GitHub CLI, curl and jq are required" >&2
		exit 2
	fi
done
if command -v sha256sum >/dev/null 2>&1; then
	sha_cmd=(sha256sum)
elif command -v shasum >/dev/null 2>&1; then
	sha_cmd=(shasum -a 256)
else
	echo "neither sha256sum nor shasum is available" >&2
	exit 2
fi

summary() {
	if [[ -n "${GITHUB_STEP_SUMMARY:-}" ]]; then
		printf '%s\n' "$1" >>"$GITHUB_STEP_SUMMARY"
	fi
}

work_dir="${WORK_DIR:-}"
cleanup() {
	[[ -n "$work_dir" ]] || return 0
	rm -rf "$work_dir"
}
if [[ -z "$work_dir" ]]; then
	work_dir="$(mktemp -d)"
	trap cleanup EXIT
fi

# --- fetch the release's SHA256SUMS ------------------------------------------
checksums_file="${work_dir}/SHA256SUMS"
"$gh_cmd" release download "$release_tag" --repo "$source_repo" \
	--pattern SHA256SUMS --dir "$work_dir" --clobber >/dev/null
[[ -s "$checksums_file" ]] || die "release ${release_tag} has no SHA256SUMS asset; refusing to pin an unverifiable wheel"

# --- resolve every wheel's name, URL and PyPI digest -------------------------
# All bdist_wheels are verified, and the PyPI wheel set must equal the
# manifest's wheel set: pip may prefer any same-version wheel, so an extra
# unmanifested one on PyPI must fail the gate, not just the first entry.
metadata="$("$curl_cmd" -sf --connect-timeout 10 --max-time 30 \
	"https://pypi.org/pypi/lintro/${VERSION}/json")" ||
	die "PyPI metadata for lintro ${VERSION} is gone; cannot verify the wheel"

# (filename url sha256) per bdist_wheel, one TSV line each.
wheel_rows="$(
	jq -r '.urls[] | select(.packagetype == "bdist_wheel") |
		[.filename, .url, .digests.sha256 // ""] | @tsv' <<<"$metadata"
)"
[[ -n "$wheel_rows" ]] || die "PyPI metadata for lintro ${VERSION} lists no wheel"

while IFS=$'\t' read -r wheel_name wheel_url pypi_digest; do
	[[ -n "$wheel_name" && -n "$wheel_url" ]] ||
		die "PyPI metadata for lintro ${VERSION} carries an incomplete wheel entry"
	[[ -n "$pypi_digest" ]] ||
		die "PyPI metadata for ${wheel_name} carries no sha256 digest"

	# --- 1. digest equality with the release manifest -------------------------
	expected_digest="$(awk -v p="$wheel_name" '{ h = $1; $1 = ""; sub(/^[[:space:]]+/, ""); if ($0 == p) print h }' "$checksums_file" | tail -1)"
	[[ -n "$expected_digest" ]] || die "${checksums_file} has no entry for ${wheel_name}; refusing to pin an unlisted wheel"
	if [[ "$pypi_digest" != "$expected_digest" ]]; then
		die "sha256 mismatch for ${wheel_name}: release manifest ${expected_digest}, PyPI ${pypi_digest}"
	fi
	echo "==> ${wheel_name}: sha256 ok (PyPI digest matches the release manifest)"

	# --- 2. provenance attestation on the downloaded bytes --------------------
	# The bytes hashed and attested are the ones curl fetches from PyPI's own
	# file URL — not a same-named copy from the GitHub Release.
	"$curl_cmd" -sf --connect-timeout 10 --max-time 300 -o "$work_dir/$wheel_name" \
		"$wheel_url" ||
		die "download of ${wheel_url} failed"
	wheel_file="${work_dir}/${wheel_name}"
	[[ -s "$wheel_file" ]] || die "downloaded wheel ${wheel_file} is missing or empty"
	actual="$("${sha_cmd[@]}" "$wheel_file" | awk '{print $1}')"
	[[ "$actual" == "$expected_digest" ]] ||
		die "sha256 mismatch for the downloaded ${wheel_name}: manifest ${expected_digest}, downloaded ${actual}"
	if ! "$gh_cmd" attestation verify "$wheel_file" \
		--repo "$attestation_repo" \
		--signer-workflow "$dist_signer"; then
		die "attestation verification failed for ${wheel_name} (expected ${attestation_repo} / ${dist_signer})"
	fi

	summary "- mirror wheel verified before pinning: \`${wheel_name}\` (PyPI digest + release manifest + attestation by \`${dist_signer}\`)"
	echo "==> ${wheel_name}: attestation ok (signer ${dist_signer})"
done <<<"$wheel_rows"

# --- 3. set equality: PyPI wheels must all be in the manifest, and vice versa
manifest_wheels="$(
	awk '$2 ~ /\.whl$/ { print $2 }' "$checksums_file" | sort
)"
pypi_wheels="$(
	jq -r '[.urls[] | select(.packagetype == "bdist_wheel") | .filename] | sort | .[]' <<<"$metadata"
)"
if ! diff <(printf '%s\n' "$manifest_wheels") <(printf '%s\n' "$pypi_wheels") >/dev/null; then
	die "PyPI wheel set differs from the release manifest wheel set; refusing to pin (unmanifested or missing wheels on PyPI)"
fi
echo "==> PyPI wheel set matches the release manifest"
