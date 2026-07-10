#!/usr/bin/env bash
# Type-check the documentation site with Astro check.
set -euo pipefail

if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
	echo "Usage: $0 [--help]"
	echo ""
	echo "Run bun install and astro check in apps/site."
	exit 0
fi

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
SITE_DIR="${ROOT}/apps/site"

cd "${SITE_DIR}"

export ASTRO_TELEMETRY_DISABLED="${ASTRO_TELEMETRY_DISABLED:-1}"
export CI="${CI:-true}"

bun install --frozen-lockfile
bun run check

echo "Astro check passed for ${SITE_DIR}"
