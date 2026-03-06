#!/usr/bin/env bash
set -euo pipefail

# sbom-rename-artifacts.sh
# Rename SBOM artifacts to include tag and full SHA for traceability.

if [ "${1:-}" = "--help" ] || [ "${1:-}" = "-h" ]; then
	echo "Usage: $0 [OUTPUT_DIR]"
	echo ""
	echo "Rename SBOM files to include tag and full SHA prefix."
	echo "Defaults: OUTPUT_DIR=dist/sbom"
	exit 0
fi

TAG="${GITHUB_REF_NAME:-${TAG:-unknown}}"
FULL_SHA="${GITHUB_SHA:-${SHA:-unknown}}"

if ! [[ "$FULL_SHA" =~ ^[0-9a-fA-F]{40}$ ]]; then
	echo "::error::FULL_SHA must be a 40-character hex commit hash (got: ${FULL_SHA})" >&2
	exit 1
fi

OUTPUT_DIR=${1:-dist/sbom}

if [ ! -d "${OUTPUT_DIR}" ]; then
	echo "SBOM output dir not found: ${OUTPUT_DIR}" >&2
	exit 1
fi

shopt -s nullglob
for f in "${OUTPUT_DIR}/py-lintro-sbom."*; do
	base=$(basename "$f")
	mv "$f" "${OUTPUT_DIR}/${TAG}-${FULL_SHA}-${base}"
done

echo "Renamed SBOM artifacts in ${OUTPUT_DIR} with ${TAG}-${FULL_SHA}- prefix"
