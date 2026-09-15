#!/usr/bin/env bash
# verify_release_binaries.sh
# Prove the platform binaries download_release_binaries.sh fetched are the
# bytes the build stage produced, before stage_binaries.py copies them into
# the npm tree (#2632).
#
# Two checks per binary, both fail closed:
#   1. sha256 equality against the release's SHA256SUMS manifest, which the
#      release gate wrote over the assets it verified (#2562);
#   2. `gh attestation verify --repo <repo> --signer-workflow <workflow>`,
#      proving the binary was built by build-binaries.yml in this repository
#      (a reusable signs as the called file, never as the entry workflow).
#
# A replaced release asset (the #2484 incident overwrote four by accident)
# or a binary with no attestation fails here, and nothing has been staged,
# packed or published yet.

set -euo pipefail

show_help() {
	cat <<'EOF'
Verify downloaded release binaries against SHA256SUMS and their attestation.

Usage:
  GH_TOKEN=<token> BINARIES_DIR=<dir> ATTESTATION_REPO=<owner/repo> \
    BINARY_SIGNER_WORKFLOW=<owner/repo/.github/workflows/build-binaries.yml> \
    scripts/ci/npm/verify_release_binaries.sh

Environment:
  BINARIES_DIR            Directory download_release_binaries.sh filled:
                          <name>/<name> per binary plus SHA256SUMS (required)
  CHECKSUMS_FILE          Manifest path (default: <BINARIES_DIR>/SHA256SUMS)
  ATTESTATION_REPO        Repository whose attestations are consulted,
                          passed as `--repo` (required)
  BINARY_SIGNER_WORKFLOW  Signer workflow the attestations must carry,
                          passed as `--signer-workflow` (required)
  BINARIES                Space-separated binary asset names (default: the
                          three release binaries)
  GH_CMD                  gh binary name (overridable in tests; default gh)
  GITHUB_STEP_SUMMARY     When set, one line per verified binary is appended
EOF
}

if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
	show_help
	exit 0
fi

binaries_dir="${BINARIES_DIR:-}"
checksums_file="${CHECKSUMS_FILE:-${binaries_dir}/SHA256SUMS}"
attestation_repo="${ATTESTATION_REPO:-}"
binary_signer="${BINARY_SIGNER_WORKFLOW:-}"
binaries="${BINARIES:-lintro-macos-arm64 lintro-linux-arm64 lintro-linux-x64}"
gh_cmd="${GH_CMD:-gh}"

die() {
	echo "::error::$*" >&2
	exit 1
}

for var in BINARIES_DIR ATTESTATION_REPO BINARY_SIGNER_WORKFLOW; do
	if [[ -z "${!var:-}" ]]; then
		echo "${var} is required" >&2
		exit 2
	fi
done
if ! command -v "$gh_cmd" >/dev/null 2>&1; then
	echo "${gh_cmd} not found; the GitHub CLI is required for attestation verification" >&2
	exit 2
fi
if command -v sha256sum >/dev/null 2>&1; then
	sha_cmd=(sha256sum)
elif command -v shasum >/dev/null 2>&1; then
	sha_cmd=(shasum -a 256)
else
	echo "neither sha256sum nor shasum is available" >&2
	exit 2
fi
[[ -s "$checksums_file" ]] || die "missing or empty checksums manifest ${checksums_file}"

summary() {
	if [[ -n "${GITHUB_STEP_SUMMARY:-}" ]]; then
		printf '%s\n' "$1" >>"$GITHUB_STEP_SUMMARY"
	fi
}

# The manifest names release assets (`<hex>  lintro-linux-x64`); the file on
# disk sits at <dir>/<name>/<name>. Exact string match on the asset name, no
# regex, so a name can never match a longer sibling. Last entry wins.
expected_digest() {
	awk -v p="$1" '{ h = $1; $1 = ""; sub(/^[[:space:]]+/, ""); if ($0 == p) print h }' "$checksums_file" | tail -1
}

verified=0
for name in $binaries; do
	file="${binaries_dir}/${name}/${name}"
	[[ -s "$file" ]] || die "missing or empty binary ${file}"

	expected="$(expected_digest "$name")"
	[[ -n "$expected" ]] || die "${checksums_file} has no entry for ${name}; refusing to stage an unlisted binary"
	actual="$("${sha_cmd[@]}" "$file" | awk '{print $1}')"
	if [[ "$actual" != "$expected" ]]; then
		die "sha256 mismatch for ${name}: manifest ${expected}, downloaded ${actual}"
	fi
	echo "==> ${name}: sha256 ok"

	echo "==> ${name}: verifying attestation (signer ${binary_signer})"
	if ! "$gh_cmd" attestation verify "$file" \
		--repo "$attestation_repo" \
		--signer-workflow "$binary_signer"; then
		die "attestation verification failed for ${name} (expected ${attestation_repo} / ${binary_signer})"
	fi
	summary "- release binary verified before staging: \`${name}\` (sha256 + attestation by \`${binary_signer}\`)"
	verified=$((verified + 1))
done

echo "Verified ${verified} release binaries (sha256 + provenance attestation)."
