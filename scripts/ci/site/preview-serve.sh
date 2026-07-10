#!/usr/bin/env bash
# Serve apps/site/dist with the same ASTRO_BASE used at build time (GitHub Pages subpath).
set -euo pipefail

if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
	echo "Usage: $0 [--help] [astro preview args...]"
	echo ""
	echo "Serve apps/site/dist via astro preview (requires prior build)."
	exit 0
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "${SCRIPT_DIR}/../../.." && pwd)"
SITE_DIR="${ROOT}/apps/site"

set -a
# shellcheck disable=SC1091
source "${SCRIPT_DIR}/defaults.env"
set +a

export ASTRO_BASE="${ASTRO_BASE:-${ASTRO_BASE_DEFAULT}}"

cd "${SITE_DIR}"
if [[ ! -d dist ]]; then
	echo "Missing ${SITE_DIR}/dist — run ./scripts/ci/site/build.sh or make site-build first." >&2
	exit 1
fi

echo "Serving dist with ASTRO_BASE=${ASTRO_BASE}"
echo "Open: http://127.0.0.1:4321${ASTRO_BASE}"
# Bind IPv4 explicitly — default astro preview may listen on ::1 only, breaking 127.0.0.1.
exec bunx astro preview --host 127.0.0.1 --port 4321 "$@"
