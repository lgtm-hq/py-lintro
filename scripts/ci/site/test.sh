#!/usr/bin/env bash
# Run Vitest with coverage for the documentation site.
# SPDX-License-Identifier: MIT
set -euo pipefail

if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
	echo "Usage: $0 [--help]"
	echo ""
	echo "Run Vitest with coverage in apps/site."
	exit 0
fi

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
SITE_DIR="${ROOT}/apps/site"

cd "${SITE_DIR}"

if ! command -v bun >/dev/null 2>&1; then
	echo "test.sh: bun is required" >&2
	exit 1
fi

bun install --frozen-lockfile
bun run test
