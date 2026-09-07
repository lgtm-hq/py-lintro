#!/usr/bin/env bash
# SPDX-License-Identifier: MIT
# Purpose: Decide whether a binary build job can reuse the asset already on the release (#2435).

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=../utils/utils.sh disable=SC1091
source "$SCRIPT_DIR/../utils/utils.sh"

if [[ "${1:-}" == "--help" || "${1:-}" == "-h" || $# -lt 4 ]]; then
	cat <<'EOF'
Decide whether a rerun can reuse the binary already attached to the release.

Usage: reuse_release_asset.sh <release-tag> <asset-name> <checksum-artifact> <dest-path>

Arguments:
  release-tag        Tag of the release holding the asset (empty => rebuild).
  asset-name         Release asset to look for (e.g. lintro-linux-x64).
  checksum-artifact  Run artifact holding <checksum-artifact>.txt, the SHA256
                     this same job wrote on an earlier attempt
                     (e.g. sha256-linux-x64).
  dest-path          Where a reused asset is placed (e.g.
                     dist/nuitka/lintro-linux-x64).

Outputs (GITHUB_OUTPUT, when set):
  reuse=true|false   Whether the caller may skip build/verify/smoke/upload.
  sha256=<hash>      The verified checksum, so the "Save SHA256" step still has
                     a value when "Finalize binary" was skipped.

Environment:
  GH_TOKEN            Token with contents: read (release), actions: read (run
                      artifacts), and contents: write when an interrupted swap
                      has to be promoted.
  GITHUB_REPOSITORY   owner/repo; exported to gh as GH_REPO when set.
  GITHUB_RUN_ID       Run whose artifacts are consulted.
  GITHUB_OUTPUT       Appended to when set.

Safety invariant
----------------
The asset is reused only when its SHA256 equals the checksum stored in a
*same-run* sha256 artifact. That artifact is written by "Save SHA256 to file",
which runs after "Verify binary" and "Smoke-test tool registry" passed on an
earlier attempt of this very job, on the same commit and the same matrix leg.
So a match proves the bytes on the release are bytes this job already verified
and smoke-tested, which is what makes skipping those steps safe. An asset
uploaded by hand (or by any other run) has no matching same-run artifact and is
therefore treated as unverified: the job rebuilds.

Interrupted swaps
-----------------
upload_release_asset.sh publishes through <asset-name>.new and then deletes the
old asset and renames the staging one. A kill between those two calls leaves
the release holding only <asset-name>.new. When <asset-name> is missing or its
checksum is stale, this script therefore also inspects <asset-name>.new; if
*that* matches the same-run checksum it is promoted (any stale <asset-name> is
deleted, <asset-name>.new is renamed onto it) and reused. The same invariant
applies: the bytes were verified and smoke-tested by an earlier attempt.

Never fails the step: every lookup failure degrades to reuse=false.
EOF
	[[ "${1:-}" == "--help" || "${1:-}" == "-h" ]] && exit 0
	exit 2
fi

RELEASE_TAG="$1"
ASSET_NAME="$2"
CHECKSUM_ARTIFACT="$3"
DEST_PATH="$4"

WORK_DIR=""
# shellcheck disable=SC2329 # Invoked via the EXIT trap below.
cleanup() {
	[[ -n "$WORK_DIR" && -d "$WORK_DIR" ]] && rm -rf "$WORK_DIR"
	return 0
}
trap cleanup EXIT

# Write the decision and exit 0. Callers gate their steps on `reuse`, so an
# undecidable state is a rebuild, never a failed step.
emit() {
	local reuse="$1"
	local sha="${2:-}"
	if [[ -n "${GITHUB_OUTPUT:-}" ]]; then
		{
			echo "reuse=$reuse"
			echo "sha256=$sha"
		} >>"$GITHUB_OUTPUT"
	fi
	if [[ "$reuse" == "true" ]]; then
		log_success "Reusing verified release asset ${ASSET_NAME} (sha256=${sha})"
	else
		log_info "No reusable asset for ${ASSET_NAME}; rebuilding"
	fi
	exit 0
}

if [[ -z "$RELEASE_TAG" ]]; then
	log_warning "No release tag resolved; nothing to reuse"
	emit false
fi

if ! command -v gh >/dev/null 2>&1; then
	log_warning "gh CLI not available; cannot check the release"
	emit false
fi

WORK_DIR="$(mktemp -d)"
CHECKSUM_DIR="$WORK_DIR/checksum"
ASSET_DIR="$WORK_DIR/asset"
mkdir -p "$CHECKSUM_DIR" "$ASSET_DIR"

# gh honours GH_REPO for repository selection, which keeps the call sites free
# of conditionally-empty argument arrays (bash 3.2 on the macOS runners has no
# safe empty-array expansion under `set -u`).
if [[ -n "${GITHUB_REPOSITORY:-}" ]]; then
	export GH_REPO="$GITHUB_REPOSITORY"
fi

# 1. The same-run checksum artifact. Missing means either a first attempt or an
#    asset this job never produced; both rebuild.
if ! gh run download "${GITHUB_RUN_ID:-}" \
	--name "$CHECKSUM_ARTIFACT" \
	--dir "$CHECKSUM_DIR" >/dev/null 2>&1; then
	log_warning "No same-run ${CHECKSUM_ARTIFACT} artifact; treating the release asset as unverified"
	emit false
fi

CHECKSUM_FILE="$CHECKSUM_DIR/${CHECKSUM_ARTIFACT}.txt"
if [[ ! -f "$CHECKSUM_FILE" ]]; then
	log_warning "Artifact ${CHECKSUM_ARTIFACT} does not contain ${CHECKSUM_ARTIFACT}.txt"
	emit false
fi

EXPECTED_SHA="$(tr -d '[:space:]' <"$CHECKSUM_FILE")"
if [[ ! "$EXPECTED_SHA" =~ ^[0-9a-fA-F]{64}$ ]]; then
	log_warning "Checksum artifact does not hold a SHA256 digest; rebuilding"
	emit false
fi
EXPECTED_SHA="$(printf '%s' "$EXPECTED_SHA" | tr '[:upper:]' '[:lower:]')"

# Echo the lowercase SHA256 of a published asset into ASSET_SHA and its local
# path into ASSET_FILE, or return 1 when the release has no such asset (or it
# cannot be hashed). Two names are tried in turn by the caller.
ASSET_FILE=""
ASSET_SHA=""
inspect_published_asset() {
	local name="$1"
	local path sha
	path="$(release_download_asset "$RELEASE_TAG" "$name" "$ASSET_DIR")" || return 1
	sha="$(sha256_file "$path")" || return 1
	ASSET_FILE="$path"
	ASSET_SHA="$(printf '%s' "$sha" | tr '[:upper:]' '[:lower:]')"
}

# 2. The asset currently attached to the release, then - when that is missing or
#    stale - the staging asset an interrupted swap can leave behind. A kill
#    between upload_release_asset.sh's delete and its rename leaves the release
#    holding only <asset>.new; promoting it here finishes that swap and saves
#    the rebuild the missing final name would otherwise force.
STAGING_NAME="$(release_staging_name "$ASSET_NAME")"
REUSE_NAME=""
if inspect_published_asset "$ASSET_NAME" && [[ "$ASSET_SHA" == "$EXPECTED_SHA" ]]; then
	REUSE_NAME="$ASSET_NAME"
elif inspect_published_asset "$STAGING_NAME" && [[ "$ASSET_SHA" == "$EXPECTED_SHA" ]]; then
	REUSE_NAME="$STAGING_NAME"
fi

# 3. Only an exact match against the run's own checksum short-circuits the build.
if [[ -z "$REUSE_NAME" ]]; then
	log_warning "Release ${RELEASE_TAG} has no ${ASSET_NAME} matching the run checksum ${EXPECTED_SHA}; rebuilding"
	emit false
fi

if [[ "$REUSE_NAME" == "$STAGING_NAME" ]]; then
	# Finish the interrupted swap before reusing the bytes, so the release ends
	# this job with the asset under its published name. A failure here is not
	# fatal: the binary is still rebuilt and re-uploaded by the normal path.
	STAGING_ID="$(release_asset_id "$RELEASE_TAG" "$STAGING_NAME")"
	if [[ -z "$STAGING_ID" ]]; then
		log_warning "Could not resolve ${STAGING_NAME} on ${RELEASE_TAG}; rebuilding"
		emit false
	fi
	log_info "Promoting ${STAGING_NAME} left by an interrupted swap"
	if ! release_promote_asset "$RELEASE_TAG" "$STAGING_ID" "$ASSET_NAME"; then
		log_warning "Could not promote ${STAGING_NAME}; rebuilding"
		emit false
	fi
fi

mkdir -p "$(dirname "$DEST_PATH")"
cp "$ASSET_FILE" "$DEST_PATH"
chmod +x "$DEST_PATH"
emit true "$ASSET_SHA"
