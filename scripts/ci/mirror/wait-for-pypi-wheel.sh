#!/usr/bin/env bash
# SPDX-License-Identifier: MIT
# Purpose: Wait for a published wheel on PyPI before bumping the pre-commit mirror.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=../../utils/utils.sh disable=SC1091
source "$SCRIPT_DIR/../../utils/utils.sh"

if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
	cat <<'EOF'
Wait for a package wheel to be available on PyPI.

Usage: wait-for-pypi-wheel.sh <package-name> <version> [max-attempts] [delay-seconds]

Arguments:
  package-name   The package name on PyPI (e.g., lintro)
  version        The version to wait for (e.g., 1.0.0)
  max-attempts   Maximum number of attempts (default: 30)
  delay-seconds  Delay between attempts in seconds (default: 10)
EOF
	exit 0
fi

PACKAGE_NAME="${1:?Package name is required}"
VERSION="${2:?Version is required}"
MAX_ATTEMPTS="${3:-30}"
DELAY_SECONDS="${4:-10}"

PYPI_URL="https://pypi.org/pypi/${PACKAGE_NAME}/${VERSION}/json"

has_wheel() {
	local response="$1"
	if command -v jq &>/dev/null; then
		echo "$response" | jq -e '.urls[] | select(.packagetype == "bdist_wheel")' &>/dev/null
	else
		echo "$response" | grep -E -q '"packagetype"[[:space:]]*:[[:space:]]*"bdist_wheel"'
	fi
}

log_info "Waiting for ${PACKAGE_NAME} ${VERSION} wheel on PyPI..."
log_info "URL: ${PYPI_URL}"

for i in $(seq 1 "$MAX_ATTEMPTS"); do
	RESPONSE=$(curl -sf "$PYPI_URL" 2>/dev/null || echo "")

	if [[ -z "$RESPONSE" ]]; then
		log_info "Attempt ${i}/${MAX_ATTEMPTS}: Package metadata not yet available, waiting ${DELAY_SECONDS}s..."
		sleep "$DELAY_SECONDS"
		continue
	fi

	if has_wheel "$RESPONSE"; then
		log_success "Package ${PACKAGE_NAME} ${VERSION} wheel is available on PyPI"
		exit 0
	fi

	log_info "Attempt ${i}/${MAX_ATTEMPTS}: Metadata exists but wheel not yet indexed, waiting ${DELAY_SECONDS}s..."
	sleep "$DELAY_SECONDS"
done

log_error "Timeout waiting for ${PACKAGE_NAME} ${VERSION} wheel on PyPI after ${MAX_ATTEMPTS} attempts"
exit 1
