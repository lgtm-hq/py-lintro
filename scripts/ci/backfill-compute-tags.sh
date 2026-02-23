#!/usr/bin/env bash
# Compute Docker image tags for a given release tag.
# Used by backfill-docker-tags.yml to generate GHCR tags for each release.
#
# Environment variables:
#   TAG  - Git tag (e.g. v0.52.2)
#   SHA  - Short commit SHA (e.g. 876464d)
#
# Outputs (via GITHUB_OUTPUT):
#   main-tags  - Comma-separated GHCR tags for the main image
#   base-tags  - Comma-separated GHCR tags for the base image (if Dockerfile.base exists)
#   has-base   - "true" if Dockerfile.base exists, "false" otherwise
set -euo pipefail

if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
	cat <<'EOF'
Usage: TAG=v0.52.2 SHA=876464d ./scripts/ci/backfill-compute-tags.sh

Computes Docker image tags for a release. Outputs semver tags (X.Y.Z, X.Y, X)
and sha-prefixed tags for both main and base images.

Required environment variables:
  TAG   Git tag (e.g. v0.52.2)
  SHA   Short commit SHA (e.g. 876464d)
EOF
	exit 0
fi

if [[ -z "${TAG:-}" ]]; then
	echo "::error::TAG environment variable is required"
	exit 1
fi
if [[ -z "${SHA:-}" ]]; then
	echo "::error::SHA environment variable is required"
	exit 1
fi

REGISTRY="ghcr.io/lgtm-hq/py-lintro"

version="${TAG#v}"
major="${version%%.*}"
minor="${version#*.}"
minor="${minor%%.*}"

# Main image tags
tags="${REGISTRY}:${version}"
tags+=",${REGISTRY}:${major}.${minor}"
tags+=",${REGISTRY}:${major}"
tags+=",${REGISTRY}:sha-${SHA}"
echo "main-tags=${tags}" >>"$GITHUB_OUTPUT"

# Base image tags (Dockerfile.base exists from v0.42.7+)
if [[ -f Dockerfile.base ]]; then
	base_tags="${REGISTRY}:${version}-base"
	base_tags+=",${REGISTRY}:${major}.${minor}-base"
	base_tags+=",${REGISTRY}:${major}-base"
	base_tags+=",${REGISTRY}:sha-${SHA}-base"
	echo "base-tags=${base_tags}" >>"$GITHUB_OUTPUT"
	echo "has-base=true" >>"$GITHUB_OUTPUT"
else
	echo "has-base=false" >>"$GITHUB_OUTPUT"
fi

echo "Building ${TAG} (${version}) at sha-${SHA}"
