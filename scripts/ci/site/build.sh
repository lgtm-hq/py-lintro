#!/usr/bin/env bash
# Build the lintro documentation site for GitHub Pages.
set -euo pipefail

if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
	echo "Usage: $0 [--help]"
	echo ""
	echo "Build apps/site for GitHub Pages (ASTRO_BASE from defaults.env)."
	exit 0
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "${SCRIPT_DIR}/../../.." && pwd)"
SITE_DIR="${ROOT}/apps/site"

set -a
# shellcheck disable=SC1091 # defaults.env is resolved via SCRIPT_DIR; not a static shellcheck input
source "${SCRIPT_DIR}/defaults.env"
set +a

cd "${SITE_DIR}"

export ASTRO_BASE="${ASTRO_BASE:-${ASTRO_BASE_DEFAULT}}"
export ASTRO_TELEMETRY_DISABLED="${ASTRO_TELEMETRY_DISABLED:-1}"

bun install --frozen-lockfile
bun run build

echo "Site built at ${SITE_DIR}/dist (ASTRO_BASE=${ASTRO_BASE})"
