#!/usr/bin/env bash
# Run the lintro documentation site dev server.
set -euo pipefail

if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
	echo "Usage: $0 [--help]"
	echo ""
	echo "Start the apps/site Astro dev server (ASTRO_BASE from defaults.env)."
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

# SITE_ASTRO_BASE is the legacy override name kept from the old Makefile target.
export ASTRO_BASE="${ASTRO_BASE:-${SITE_ASTRO_BASE:-${ASTRO_BASE_DEFAULT}}}"
export ASTRO_TELEMETRY_DISABLED="${ASTRO_TELEMETRY_DISABLED:-1}"

bun install
bun run dev
