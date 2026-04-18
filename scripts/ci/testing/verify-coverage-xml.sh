#!/usr/bin/env bash
# SPDX-License-Identifier: MIT
# For license details, see the repository root LICENSE file.
#
# Verify that coverage.xml was generated and contains actual coverage data.
# Fails if the file is missing or reports zero covered lines, which indicates
# the test suite was skipped or failed silently.

set -euo pipefail

if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
	cat <<'EOF'
Verify coverage.xml was generated and contains actual coverage data.

Usage:
  scripts/ci/testing/verify-coverage-xml.sh [coverage.xml]

Arguments:
  coverage.xml  Path to coverage XML file (default: coverage.xml)

Exits non-zero if:
  - The file does not exist
  - The file reports zero covered lines
EOF
	exit 0
fi

COVERAGE_FILE="${1:-coverage.xml}"

if [[ ! -f "$COVERAGE_FILE" ]]; then
	echo "::error::${COVERAGE_FILE} missing — test suite skipped or failed silently"
	exit 1
fi

covered=$(python3 -c "
import xml.etree.ElementTree as ET, sys
root = ET.parse(sys.argv[1]).getroot()
print(int(root.get('lines-covered', '0') or 0))
" "$COVERAGE_FILE" 2>/dev/null || echo "0")

if [[ "${covered:-0}" -le 0 ]]; then
	echo "::error::${COVERAGE_FILE}: 0 covered lines (tests may not have run)"
	exit 1
fi

echo "Coverage verified: ${covered} lines covered."
