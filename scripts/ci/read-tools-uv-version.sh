#!/usr/bin/env bash
# SPDX-License-Identifier: MIT
# Read the canonical resolver pin without maintaining another version literal.
set -euo pipefail

if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
	cat <<'EOF'
Read the canonical uv version from docker/tools.Dockerfile.

Usage:
  GITHUB_OUTPUT=<file> scripts/ci/read-tools-uv-version.sh

Writes version=<pin> to the GitHub step output file. Fails unless there is
exactly one exact UV_VERSION pin and GITHUB_OUTPUT is set.
EOF
	exit 0
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
version="$(sed -nE 's/^ARG UV_VERSION=([0-9]+\.[0-9]+\.[0-9]+)$/\1/p' "${PROJECT_ROOT}/docker/tools.Dockerfile")"
if [[ ! "$version" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
	echo 'Error: expected one exact UV_VERSION pin in docker/tools.Dockerfile' >&2
	exit 1
fi
echo "version=$version" >>"${GITHUB_OUTPUT:?GITHUB_OUTPUT must be set}"
