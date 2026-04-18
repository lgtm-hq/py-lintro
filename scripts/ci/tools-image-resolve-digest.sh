#!/usr/bin/env bash
# SPDX-License-Identifier: MIT
# For license details, see the repository root LICENSE file.
#
# tools-image-resolve-digest.sh - Resolve and output the pushed tools image digest
#
# Pulls the image by tag, inspects its repo digests, and writes the canonical
# sha256 digest to GITHUB_OUTPUT. Called from tools-image.yml after a push.
#
# Environment Variables (required):
#   IMAGE_TAG       Full image tag that was pushed (e.g. ghcr.io/org/lintro-tools:sha-abc123)
#   GITHUB_OUTPUT   Path to the GitHub Actions output file

set -euo pipefail

if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
	cat <<'EOF'
Resolve and output the pushed tools image digest.

Usage:
  scripts/ci/tools-image-resolve-digest.sh

Environment Variables (required):
  IMAGE_TAG       Full image tag (e.g. ghcr.io/org/lintro-tools:sha-abc123)
  GITHUB_OUTPUT   Path to the GitHub Actions output file

Outputs (to GITHUB_OUTPUT):
  image_name      Image name without tag (e.g. ghcr.io/org/lintro-tools)
  digest          sha256 digest of the pushed image

Exit codes:
  0  Digest resolved and written to GITHUB_OUTPUT
  1  Digest could not be resolved (missing, unexpected format, or pull failed)
EOF
	exit 0
fi

: "${IMAGE_TAG:?IMAGE_TAG is required}"
: "${GITHUB_OUTPUT:?GITHUB_OUTPUT is required}"

IMAGE_NAME="${IMAGE_TAG%:*}"

echo "Pulling image to resolve digest: $IMAGE_TAG"
docker pull "$IMAGE_TAG"

FORMAT='{{range .RepoDigests}}{{println .}}{{end}}'
REPO_DIGESTS=$(docker inspect --format="$FORMAT" "$IMAGE_TAG" || echo "")

DIGEST=$(echo "$REPO_DIGESTS" |
	awk -v name="$IMAGE_NAME" -F@ '$1==name {print $2; exit}')

if [[ -z "$DIGEST" ]]; then
	echo "::error::Unable to resolve digest for $IMAGE_TAG"
	echo "Available repo digests:"
	echo "$REPO_DIGESTS"
	echo "Raw docker inspect output:"
	docker inspect "$IMAGE_TAG" --format='{{json .RepoDigests}}' || true
	exit 1
fi

echo "image_name=$IMAGE_NAME" >>"$GITHUB_OUTPUT"
echo "digest=$DIGEST" >>"$GITHUB_OUTPUT"
echo "Resolved digest for $IMAGE_NAME: $DIGEST"
