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

# shellcheck disable=SC1091 # helper path resolved at runtime via $(dirname "$0")
source "$(dirname "$0")/tools-image-digest-helpers.sh"

IMAGE_NAME="${IMAGE_TAG%:*}"

RESOLVED=$(resolve_image_digest "$IMAGE_TAG")
DIGEST="${RESOLVED#*@}"

echo "image_name=$IMAGE_NAME" >>"$GITHUB_OUTPUT"
echo "digest=$DIGEST" >>"$GITHUB_OUTPUT"
echo "Resolved digest for $IMAGE_NAME: $DIGEST"
