#!/usr/bin/env bash
# Compute Docker image tags for a given release tag.
# Used by backfill-docker-tags.yml to generate GHCR tags for each release.
#
# Environment variables:
#   TAG               - Git tag (e.g. v0.52.2)
#   SHA               - Full commit SHA
#   GITHUB_REPOSITORY - Owner/repo (e.g. lgtm-hq/py-lintro); owner derives GHCR path
#
# Outputs (via GITHUB_OUTPUT):
#   main-tags  - Comma-separated tags for ghcr.io/<owner>/py-lintro
#   base-tags  - Comma-separated tags for ghcr.io/<owner>/py-lintro-base (if Dockerfile.base exists)
#   has-base   - "true" if Dockerfile.base exists, "false" otherwise
set -euo pipefail

if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
	cat <<'EOF'
Usage: TAG=v0.52.2 SHA=876464d9f1a2b3c4d5e6f7081920a1b2c3d4e5f6 ./scripts/ci/backfill-compute-tags.sh

Computes Docker image tags for a release. Outputs semver tags (X.Y.Z, X.Y, X)
and sha-prefixed tags for both packages (py-lintro and py-lintro-base).

Required environment variables:
  TAG                Git tag (e.g. v0.52.2)
  SHA                Full commit SHA
  GITHUB_REPOSITORY  Owner/repo (defaults to lgtm-hq/py-lintro)
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
if ! [[ "$SHA" =~ ^[0-9a-f]{40}$ ]]; then
	echo "::error::SHA must be a full 40-character lowercase hex commit SHA (got: ${SHA})"
	exit 1
fi

# Derive owner from GITHUB_REPOSITORY; GHCR packages are fixed names
# py-lintro / py-lintro-base (split packages). Repo rename requires updating REGISTRY*,
# BASE_REGISTRY below (legacy single-package code used ghcr.io/<owner>/<repo-lowercase>).
repo="${GITHUB_REPOSITORY:-lgtm-hq/py-lintro}"
owner="${repo%%/*}"
REGISTRY="ghcr.io/${owner,,}/py-lintro"
BASE_REGISTRY="ghcr.io/${owner,,}/py-lintro-base"

# Parse semver components (vMAJOR.MINOR.PATCH)
version="${TAG#v}"
if ! [[ "$version" =~ ^([0-9]+)\.([0-9]+)\.([0-9]+) ]]; then
	echo "::error::TAG does not match semver pattern vX.Y.Z (got: ${TAG})"
	exit 1
fi
major="${BASH_REMATCH[1]}"
minor="${BASH_REMATCH[2]}"

# Main image tags
tags="${REGISTRY}:${version}"
tags+=",${REGISTRY}:${major}.${minor}"
tags+=",${REGISTRY}:${major}"
tags+=",${REGISTRY}:sha-${SHA}"
echo "main-tags=${tags}" >>"$GITHUB_OUTPUT"

# Base image tags (Dockerfile.base exists from v0.42.7+)
if [[ -f Dockerfile.base ]]; then
	base_tags="${BASE_REGISTRY}:${version}"
	base_tags+=",${BASE_REGISTRY}:${major}.${minor}"
	base_tags+=",${BASE_REGISTRY}:${major}"
	base_tags+=",${BASE_REGISTRY}:sha-${SHA}"
	echo "base-tags=${base_tags}" >>"$GITHUB_OUTPUT"
	echo "has-base=true" >>"$GITHUB_OUTPUT"
else
	echo "has-base=false" >>"$GITHUB_OUTPUT"
fi

echo "Building ${TAG} (${version}) at sha-${SHA}"
