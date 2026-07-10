#!/usr/bin/env bash
set -euo pipefail

# Pull the published lintro Docker image from GHCR and tag it for local use.
# Prefers an immutable sha-<commit> tag over :latest to avoid stale images.

# Show help if requested
if [ "${1:-}" = "--help" ] || [ "${1:-}" = "-h" ]; then
	echo "Usage: $0 [--help]"
	echo ""
	echo "Pull Lintro Docker Image"
	echo "Pulls a published lintro image from GHCR and tags it locally."
	echo ""
	echo "Environment:"
	echo "  LINTRO_SHA          Commit SHA for ghcr.io/.../py-lintro:sha-<sha> (preferred)"
	echo "  LINTRO_IMAGE        Full image ref override (wins over LINTRO_SHA)"
	echo "  GHCR_ORG_PACKAGE    Registry prefix (default: ghcr.io/lgtm-hq)"
	echo ""
	echo "Resolution order:"
	echo "  1. LINTRO_IMAGE when set"
	echo "  2. sha-<LINTRO_SHA> when set"
	echo "  3. sha-<git rev-parse HEAD> from the checked-out tree"
	echo ""
	echo "Tags the resolved image as py-lintro:latest for report scripts."
	exit 0
fi

registry="${GHCR_ORG_PACKAGE:-ghcr.io/lgtm-hq}"
commit_sha="${LINTRO_SHA:-$(git rev-parse HEAD)}"

if [ -n "${LINTRO_IMAGE:-}" ]; then
	image="$LINTRO_IMAGE"
else
	image="${registry}/py-lintro:sha-${commit_sha}"
fi

if ! docker pull "$image"; then
	echo "::error::Failed to pull ${image}. Ensure CI - Docker publish completed for commit ${commit_sha}." >&2
	echo "::error::Do not fall back to :latest — it may predate dependency changes on main." >&2
	exit 1
fi

docker tag "$image" py-lintro:latest

DIGEST=$(docker inspect --format='{{index .RepoDigests 0}}' "$image")
if [ -z "$DIGEST" ]; then
	echo "::error::Failed to resolve image digest for $image" >&2
	exit 1
fi
echo "::notice::Pulled ${image}"
echo "::notice::Resolved digest: ${DIGEST}"
