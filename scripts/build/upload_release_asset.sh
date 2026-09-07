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

Only step 3 has a gap, and it is a delete plus a rename rather than a
multi-second upload. A kill anywhere else leaves the release with the old asset
(steps 1-2) or with both names present. A kill inside step 3 leaves only
<asset-name>.new. Both halves of the next attempt recover from that: this
script promotes the leftover when it matches the file being uploaded, and
reuse_release_asset.sh promotes it (skipping the rebuild entirely) when it
matches the run's own checksum artifact. A staging asset from some other build
is deleted before a fresh upload -- but only once its digest has actually been
read and found to differ. When the leftover cannot be downloaded or hashed this
script fails instead, because it can be the release's only copy of the binary.
EOF
	[[ "${1:-}" == "--help" || "${1:-}" == "-h" ]] && exit 0
	exit 2
fi

RELEASE_TAG="$1"
FILE="$2"
ASSET_NAME="${3:-$(basename "$FILE")}"
STAGING_NAME="$(release_staging_name "$ASSET_NAME")"

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

# Echo the SHA256 of a published asset, or return 1 when it could not be read.
# The distinction matters: an empty string must never be compared against a real
# digest, because "the hashes differ" is what authorises deleting an asset. A
# transient download failure has to fail the step instead.
published_sha256() {
	local name="$1"
	local path
	path="$(release_download_asset "$RELEASE_TAG" "$name" "$WORK_DIR/inspect")" || return 1
	sha256_file "$path"
}

if ! LOCAL_SHA="$(sha256_file "$FILE")"; then
	log_error "No SHA256 tool found (expected sha256sum or shasum)"
	exit 1
fi

WORK_DIR="$(mktemp -d)"
STAGING_FILE="$WORK_DIR/$STAGING_NAME"
cp "$FILE" "$STAGING_FILE"

# A previous killed attempt may have left the staging asset behind. If it is
# already the bytes we are about to upload, finish that swap instead of
# deleting it: a kill in the delete-and-rename pair below leaves the release
# holding only <asset>.new, and re-uploading from scratch would delete the one
# good copy first. Stale bytes from some other build are removed, since gh
# refuses to upload a duplicate name and they must never be renamed into place.
STALE_ID="$(release_asset_id "$RELEASE_TAG" "$STAGING_NAME")"
if [[ -n "$STALE_ID" ]]; then
	# Fail closed when the leftover cannot be read. After a killed swap it can
	# be the release's only copy of the binary, so it may only be deleted on a
	# digest that was actually computed and actually differs -- never on a
	# transient download error.
	if ! STALE_SHA="$(published_sha256 "$STAGING_NAME")"; then
		log_error "Could not read ${STAGING_NAME} from ${RELEASE_TAG}; refusing to delete it"
		exit 1
	fi
	if [[ "$STALE_SHA" == "$LOCAL_SHA" ]]; then
		log_info "Promoting the ${STAGING_NAME} left by an earlier attempt"
		release_promote_asset "$RELEASE_TAG" "$STALE_ID" "$ASSET_NAME"
		log_success "Published ${ASSET_NAME} to ${RELEASE_TAG} (sha256=${LOCAL_SHA})"
		exit 0
	fi
	log_warning "Removing stale ${STAGING_NAME} (sha256=${STALE_SHA}) from ${RELEASE_TAG}"
	release_delete_asset "$STALE_ID"
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

if ! REMOTE_SHA="$(sha256_file "$VERIFY_DIR/$STAGING_NAME")"; then
	log_error "Could not hash the uploaded asset"
	exit 1
fi

if [[ "$REMOTE_SHA" != "$LOCAL_SHA" ]]; then
	log_error "Uploaded ${STAGING_NAME} sha256=${REMOTE_SHA} does not match local ${LOCAL_SHA}"
	NEW_ID="$(release_asset_id "$RELEASE_TAG" "$STAGING_NAME")"
	if [[ -n "$NEW_ID" ]]; then
		release_delete_asset "$NEW_ID"
	fi
	exit 1
fi

NEW_ID="$(release_asset_id "$RELEASE_TAG" "$STAGING_NAME")"
if [[ -z "$NEW_ID" ]]; then
	log_error "Uploaded ${STAGING_NAME} is not listed on ${RELEASE_TAG}"
	exit 1
fi

release_promote_asset "$RELEASE_TAG" "$NEW_ID" "$ASSET_NAME"

log_success "Published ${ASSET_NAME} to ${RELEASE_TAG} (sha256=${LOCAL_SHA})"
