#!/usr/bin/env bash
set -euo pipefail

# read_manifest_sha.sh
#
# Read one release asset's SHA-256 out of release-manifest.json (written by
# the release gate, #2562) and export it as `arm64_sha256=<hex>` on
# GITHUB_OUTPUT for the Homebrew tap dispatch. The manifest is the record of
# the bytes the gate verified and the GitHub Release attached, so the tap
# gets the digest of exactly what ships.

show_help() {
	cat <<'EOF'
Read a release asset's SHA-256 from release-manifest.json.

Usage:
  scripts/ci/release-gate/read_manifest_sha.sh <manifest.json> <asset-name>

Prints the hex digest and, when GITHUB_OUTPUT is set, appends
`arm64_sha256=<hex>` (the key the Homebrew dispatch step reads).
Fails when the asset is missing from the manifest or its digest is malformed.
EOF
}

if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
	show_help
	exit 0
fi

if [[ $# -ne 2 ]]; then
	show_help >&2
	exit 2
fi

manifest="$1"
asset="$2"

if [[ ! -s "$manifest" ]]; then
	echo "manifest not found or empty: ${manifest}" >&2
	exit 1
fi
if ! command -v jq >/dev/null 2>&1; then
	echo "jq is required" >&2
	exit 2
fi

sha="$(jq -r --arg asset "$asset" '.files[$asset].sha256 // empty' "$manifest")"
if ! [[ "$sha" =~ ^[0-9a-f]{64}$ ]]; then
	echo "no sha256 for ${asset} in ${manifest} (got: '${sha}')" >&2
	exit 1
fi

printf '%s\n' "$sha"
if [[ -n "${GITHUB_OUTPUT:-}" ]]; then
	echo "arm64_sha256=${sha}" >>"$GITHUB_OUTPUT"
fi
