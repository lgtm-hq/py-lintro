#!/usr/bin/env bash
# Run Vitest and site script pytest for the documentation site.
# SPDX-License-Identifier: MIT
set -euo pipefail

if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
	echo "Usage: $0 [--help]"
	echo ""
	echo "Run ./scripts/ci/site/test.sh and test-python.sh."
	exit 0
fi

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"

"${ROOT}/scripts/ci/site/test.sh"
"${ROOT}/scripts/ci/site/test-python.sh"
