#!/usr/bin/env bash
# SPDX-License-Identifier: MIT
# Purpose: Attach a binary to a release without ever leaving the release assetless (#2435).

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=../utils/utils.sh disable=SC1091
source "$SCRIPT_DIR/../utils/utils.sh"

if [[ "${1:-}" == "--help" || "${1:-}" == "-h" || $# -lt 2 ]]; then
	cat <<'EOF'
Upload a release asset with an upload-then-swap sequence.

Usage: upload_release_asset.sh <release-tag> <file> [asset-name]

Arguments:
  release-tag  Tag of the release to attach the asset to.
  file         Local file to upload.
  asset-name   Published asset name (defaults to the file's basename).

Environment:
  GH_TOKEN           Token with contents: write.
  GITHUB_REPOSITORY  owner/repo; exported to gh as GH_REPO when set.

Why not `--clobber` / `overwrite_files: true`
---------------------------------------------
Both delete the existing asset and then upload the replacement. A runner kill
or step timeout in that window leaves a published release with no binary, which
is what happened to lintro-linux-x64 on v0.147.3 (#2435). This script instead:

  1. uploads the new bytes under <asset-name>.new (the release still serves the
     old asset throughout),
  2. downloads <asset-name>.new back and compares its SHA256 with the local
     file, so a truncated upload is caught before anything is deleted,
  3. deletes the old asset and renames <asset-name>.new to <asset-name>.

Only step 3 has a gap, and it is a single API call rather than a multi-second
upload. A kill anywhere else leaves the release with the old asset (steps 1-2)
or with both names present, and the next attempt starts by clearing any stale
<asset-name>.new.
EOF
	[[ "${1:-}" == "--help" || "${1:-}" == "-h" ]] && exit 0
	exit 2
fi

RELEASE_TAG="$1"
FILE="$2"
ASSET_NAME="${3:-$(basename "$FILE")}"
STAGING_NAME="${ASSET_NAME}.new"

if [[ -z "$RELEASE_TAG" ]]; then
	log_error "Release tag is required"
	exit 1
fi

if [[ ! -f "$FILE" ]]; then
	log_error "File not found: $FILE"
	exit 1
fi

if ! command -v gh >/dev/null 2>&1; then
	log_error "gh CLI is required to upload release assets"
	exit 1
fi

# gh honours GH_REPO for repository selection; see reuse_release_asset.sh.
if [[ -n "${GITHUB_REPOSITORY:-}" ]]; then
	export GH_REPO="$GITHUB_REPOSITORY"
fi

WORK_DIR=""
STAGING_FILE=""
# shellcheck disable=SC2329 # Invoked via the EXIT trap below.
cleanup() {
	[[ -n "$WORK_DIR" && -d "$WORK_DIR" ]] && rm -rf "$WORK_DIR"
	return 0
}
trap cleanup EXIT

sha256_of() {
	local file="$1"
	if command -v sha256sum >/dev/null 2>&1; then
		sha256sum "$file" | cut -d' ' -f1
	elif command -v shasum >/dev/null 2>&1; then
		shasum -a 256 "$file" | cut -d' ' -f1
	else
		return 1
	fi
}

# Echo the numeric id of a named asset on the release, or nothing when absent.
asset_id() {
	local name="$1"
	gh api "repos/{owner}/{repo}/releases/tags/${RELEASE_TAG}" \
		--jq ".assets[] | select(.name == \"${name}\") | .id" 2>/dev/null || true
}

delete_asset_by_id() {
	local id="$1"
	gh api -X DELETE "repos/{owner}/{repo}/releases/assets/${id}" >/dev/null
}

if ! LOCAL_SHA="$(sha256_of "$FILE")"; then
	log_error "No SHA256 tool found (expected sha256sum or shasum)"
	exit 1
fi

WORK_DIR="$(mktemp -d)"
STAGING_FILE="$WORK_DIR/$STAGING_NAME"
cp "$FILE" "$STAGING_FILE"

# A previous killed attempt may have left the staging asset behind; gh refuses
# to upload a duplicate name, and stale bytes must never be renamed into place.
STALE_ID="$(asset_id "$STAGING_NAME")"
if [[ -n "$STALE_ID" ]]; then
	log_warning "Removing stale ${STAGING_NAME} from ${RELEASE_TAG}"
	delete_asset_by_id "$STALE_ID"
fi

log_info "Uploading ${STAGING_NAME} to ${RELEASE_TAG}"
gh release upload "$RELEASE_TAG" "$STAGING_FILE"

# Verify the published bytes before touching the asset the release already
# serves. A short read here is a failed step, not a lost binary.
VERIFY_DIR="$WORK_DIR/verify"
mkdir -p "$VERIFY_DIR"
gh release download "$RELEASE_TAG" \
	--pattern "$STAGING_NAME" \
	--dir "$VERIFY_DIR" \
	--clobber

if ! REMOTE_SHA="$(sha256_of "$VERIFY_DIR/$STAGING_NAME")"; then
	log_error "Could not hash the uploaded asset"
	exit 1
fi

if [[ "$REMOTE_SHA" != "$LOCAL_SHA" ]]; then
	log_error "Uploaded ${STAGING_NAME} sha256=${REMOTE_SHA} does not match local ${LOCAL_SHA}"
	NEW_ID="$(asset_id "$STAGING_NAME")"
	if [[ -n "$NEW_ID" ]]; then
		delete_asset_by_id "$NEW_ID"
	fi
	exit 1
fi

NEW_ID="$(asset_id "$STAGING_NAME")"
if [[ -z "$NEW_ID" ]]; then
	log_error "Uploaded ${STAGING_NAME} is not listed on ${RELEASE_TAG}"
	exit 1
fi

OLD_ID="$(asset_id "$ASSET_NAME")"
if [[ -n "$OLD_ID" ]]; then
	log_info "Replacing existing ${ASSET_NAME}"
	delete_asset_by_id "$OLD_ID"
fi

gh api -X PATCH "repos/{owner}/{repo}/releases/assets/${NEW_ID}" \
	-f "name=${ASSET_NAME}" >/dev/null

log_success "Published ${ASSET_NAME} to ${RELEASE_TAG} (sha256=${LOCAL_SHA})"
