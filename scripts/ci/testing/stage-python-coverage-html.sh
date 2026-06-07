#!/usr/bin/env bash
# Stage flat HTML coverage for GitHub Pages Model B bundling.
set -euo pipefail

if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
	echo "Usage: $0 [--help]"
	echo ""
	echo "Generate coverage-html/ from coverage-report/coverage.xml for Pages bundling."
	exit 0
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
OUTPUT_DIR="${ROOT}/coverage-html"
COVERAGE_XML="${ROOT}/coverage-report/coverage.xml"

cd "${ROOT}"

if [[ ! -f "${COVERAGE_XML}" ]]; then
	echo "::error::Missing coverage.xml at ${COVERAGE_XML}"
	exit 1
fi

rm -rf "${OUTPUT_DIR}"
mkdir -p "${OUTPUT_DIR}"

uv run coverage html -d "${OUTPUT_DIR}" --data-file "${COVERAGE_XML}"

if [[ ! -f "${OUTPUT_DIR}/index.html" ]]; then
	echo "::error::coverage html did not produce index.html"
	exit 1
fi

echo "Staged coverage HTML at ${OUTPUT_DIR}"
